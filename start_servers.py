r"""EDI gRPC MCP 一键启动 — 统一入口。

═══════════════════════════════════════════════════════════
  工具注册见：servers/registry_server.py
  工具定义见：servers/eda/*.py

  启动方式：
    uv run python start_servers.py                          # streamable-http（默认，仅本机）
    uv run python start_servers.py --transport stdio         # Claude Code
    uv run python start_servers.py --port 9000               # 自定义端口
    uv run python start_servers.py --host 0.0.0.0            # 接受局域网/远程访问

  客户端连接：
    Claude Code   .mcp.json 自动管理，/mcp 重载
    OpenClaw      http://127.0.0.1:50026/mcp

  健康检查：
    /health       进程是否存在 + gRPC 状态
    /ready        服务是否已初始化完成（启动中返回 503）
═══════════════════════════════════════════════════════════
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import socket
import sys
import threading
import time
import uvicorn
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# load_dotenv 必须在 import servers 之前：否则 servers/__init__.py 的
# get_settings() 会先执行并被 lru_cache 缓存，frozen 下 env_file 路径不存在、
# 环境变量尚未加载，导致读到空配置。
if getattr(sys, "frozen", False):
    load_dotenv(Path(sys.executable).parent / ".env")
else:
    load_dotenv()

from servers.settings import get_settings

# -- 配置（从统一配置读取）--
_cfg = get_settings()
DEFAULT_TRANSPORT = _cfg.mcp_transport
DEFAULT_PORT = _cfg.mcp_port
DEFAULT_BIND_HOST = _cfg.mcp_bind_host

# ── 启动时配置校验 ──
_cfg_issues = _cfg.validate()
if _cfg_issues:
    print("WARNING: 配置存在问题 —")
    for issue in _cfg_issues:
        print(f"  - {issue}")
    print()

from servers import mcp, __version__ as _server_ver
from servers.eda.config import EDA_GRPC_SERVER as _grpc_cfg_addr
from servers.utils import (
    build_transport_security,
    host_without_port,
    is_loopback_host,
    local_host_names,
    registered_tools,
    set_request_base_url,
    set_server_address,
    tcp_port_open,
)
from servers.process_guard import ProcessProbeMiddleware, ProcessWhitelistMiddleware
import servers.registry_server  # — 触发工具注册

# ── 运行时状态 ──
_server_ready = threading.Event()
_server_stopping = threading.Event()
_log = logging.getLogger("edi_mcp")

# 实际生效的监听地址与 Host 允许列表（供 /ready 自述、远程排障）
_runtime_bind_host = "127.0.0.1"
_runtime_allowed_hosts: list[str] = []


class RequestBaseURLMiddleware(BaseHTTPMiddleware):
    """把当前请求的 scheme://Host 记入上下文，供产物/文档链接按客户端视角生成。

    只接受允许列表内的 Host（防止伪造 Host 污染产物链接）；不在列表内则不设置，
    调用方回退到启动时确定的地址。
    """

    def __init__(self, app, allowed_bases: set[str]) -> None:
        super().__init__(app)
        self._allowed = allowed_bases

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        # 长度上限防御；host_without_port 兼容 [::1]:50026 形式
        if host and len(host) <= 255 and host_without_port(host).lower() in self._allowed:
            scheme = request.url.scheme or "http"
            set_request_base_url(f"{scheme}://{host}")
        return await call_next(request)


def is_server_ready() -> bool:
    return _server_ready.is_set()


def _lifecycle_log(event: str, **extra) -> None:
    parts = [f"{k}={v}" for k, v in extra.items()]
    _log.info("%s %s", event, " ".join(parts))


# ── 优雅关闭 ──
def _install_shutdown_handlers() -> None:
    def _handle_shutdown(signum, frame):
        if _server_stopping.is_set():
            return  # 已经在关闭
        _server_stopping.set()
        _lifecycle_log("MCP_STOPPING")
        sys.exit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_shutdown)
        except (ValueError, OSError):
            pass  # 非主线程或平台不支持


def _setup_logging() -> None:
    """按大小轮转的文件日志（写入 %TEMP%/edi/data/log/）+ 带时间戳的控制台日志。"""
    import tempfile as _tmp
    from datetime import datetime as _dt
    log_dir = Path(_tmp.gettempdir()) / "edi" / "data" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = f"edi_mcp_{_dt.now().strftime('%Y%m')}.log"

    _fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        log_dir / log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(_fmt)

    root = logging.getLogger()
    root.addHandler(file_handler)
    # 控制台 handler：让 SDK 等根 logger 的输出（如 "Terminating session"）也带时间戳。
    # frozen 无控制台时 sys.stderr 可能为 None，跳过以免写空报错。
    if getattr(sys, "stderr", None) is not None:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(_fmt)
        root.addHandler(console_handler)
    root.setLevel(logging.INFO)
    _lifecycle_log("MCP_STARTING", version=_server_ver)


# ── /ready 路由处理函数 ──
async def ready_check(request):
    from starlette.responses import JSONResponse
    from servers.eda.config import EDA_GRPC_SERVER as _grpc
    from servers.utils import SERVER_STARTED_AT

    if not is_server_ready():
        return JSONResponse({
            "status": "starting",
            "message": "MCP 服务正在初始化，请稍后重试",
        }, status_code=503)

    tools = [t.name for t in registered_tools(mcp)]
    tools_hash = hashlib.md5(",".join(sorted(tools)).encode("utf-8")).hexdigest()[:8]
    try:
        host, port_str = _grpc.rsplit(":", 1)
        s = socket.socket()
        s.settimeout(0.5)
        grpc_ok = s.connect_ex((host, int(port_str))) == 0
        s.close()
    except Exception:
        grpc_ok = False

    return JSONResponse({
        "status": "ready",
        "transport": _cfg.mcp_transport,
        "stateless": _cfg.mcp_stateless_http,
        "version": _server_ver,
        "grpc": "online" if grpc_ok else "offline",
        "tool_count": len(tools),
        "tools_hash": tools_hash,
        "started_at": SERVER_STARTED_AT,
        # 远程排障用：实际监听地址 + 生效的 Host 允许条目数
        # （非环回地址访问报 421 时，先看这里是否只有 3 条 SDK 默认环回模式）
        "bind_host": _runtime_bind_host,
        "allowed_hosts": len(_runtime_allowed_hosts),
    })


def _find_port_pid(port: int) -> int | None:
    """查找监听指定端口的进程 PID，找不到返回 None。"""
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if (conn.status == psutil.CONN_LISTEN
                    and conn.laddr and conn.laddr.port == port):
                return conn.pid
    except (psutil.AccessDenied, OSError):
        pass
    return None


def _kill_port_process(port: int) -> bool:
    """结束监听指定端口的进程，成功返回 True（端口空闲也视为成功）。"""
    pid = _find_port_pid(port)
    if pid is None:
        return True
    try:
        import psutil
        proc = psutil.Process(pid)
        name = proc.name()
        print(f"端口 {port} 被 {name} (PID {pid}) 占用，正在结束...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return True
    except psutil.NoSuchProcess:
        return True  # 进程已退出
    except (psutil.AccessDenied, OSError) as exc:
        print(f"结束占用进程失败: {exc}")
        return False


def _run_http_server(port: int, host: str = "127.0.0.1") -> None:
    """Streamable HTTP 模式入口。host 默认 127.0.0.1（仅本机），0.0.0.0 = 接受远程。"""
    global _runtime_bind_host, _runtime_allowed_hosts

    _install_shutdown_handlers()

    # 冻结模式无控制台时，重定向 stdout/stderr 避免 uvicorn 日志报错
    if getattr(sys, "frozen", False) and sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    _runtime_bind_host = host
    remote_mode = not is_loopback_host(host)

    # 端口占用检查：被占用时自动结束占用进程，释放后继续启动。
    # 按端口查监听进程（不限于 127.0.0.1）：绑 0.0.0.0 时也要能发现只监听某张网卡的占用者。
    occupied = _find_port_pid(port) is not None or tcp_port_open("127.0.0.1", port)

    if occupied:
        print(f"端口 {port} 已被占用，尝试自动结束占用进程后重启...")
        if not _kill_port_process(port):
            print(f"无法结束占用端口 {port} 的进程，请手动处理。")
            sys.exit(1)
        # 等待端口释放（进程退出后端口可能短暂处于 TIME_WAIT）
        deadline = time.monotonic() + 5
        while True:
            if not tcp_port_open("127.0.0.1", port):
                break
            if time.monotonic() >= deadline:
                print(f"端口 {port} 未能释放，请手动处理。")
                sys.exit(1)
            time.sleep(0.5)
        print(f"端口 {port} 已释放，继续启动。")

    if sys.platform == "win32" and sys.version_info < (3, 14):
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    tools = [t.name for t in registered_tools(mcp)]

    # 检测 50055 状态
    _grpc_host, _grpc_port = _grpc_cfg_addr.rsplit(":", 1)
    _test = socket.socket()
    _test.settimeout(1)
    _grpc_ok = _test.connect_ex((_grpc_host, int(_grpc_port))) == 0
    _test.close()

    # 注册 /ready 路由
    mcp.custom_route("/ready", methods=["GET"])(ready_check)

    # ── Host 允许列表 ──
    # SDK 只在 FastMCP 构造时 host ∈ 环回 才自动填 allowed_hosts（见 fastmcp/server.py），
    # 之后改 settings.host 不会重算 → 非环回访问被 421 Invalid Host header 拒绝。
    # 因此远程模式必须显式重建，且必须在 streamable_http_app() 之前（构建 app 时读该设置）。
    extra_hosts = [h.strip() for h in (_cfg.mcp_extra_allowed_hosts or "").split(",") if h.strip()]
    if remote_mode or extra_hosts:
        mcp.settings.transport_security = build_transport_security(extra_hosts)
    allowed_bases = {h.lower() for h in local_host_names()} | {h.lower() for h in extra_hosts}

    mcp.settings.host = host
    mcp.settings.port = port
    set_server_address(host, port)
    _runtime_allowed_hosts = list(
        getattr(mcp.settings.transport_security, "allowed_hosts", None) or []
    )

    # 标记就绪
    _server_ready.set()
    _lifecycle_log("MCP_READY", tools=len(tools),
                   grpc="online" if _grpc_ok else "offline")
    _lifecycle_log("MCP_BIND", host=host, port=port,
                   allowed_hosts=len(_runtime_allowed_hosts))

    _print_host = host if host not in ("0.0.0.0", "::") else "127.0.0.1"
    print("=" * 50)
    print(f"  EDI gRPC MCP v{_server_ver}  (streamable-http, stateless)")
    print(f"  Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  UI:    http://{_print_host}:{port}/ui")
    print(f"  MCP:   http://{_print_host}:{port}/mcp")
    print(f"  Ready: http://{_print_host}:{port}/ready")
    print(f"  Tools: {len(tools)} loaded")
    print(f"  gRPC:  {_grpc_cfg_addr} [{'ONLINE' if _grpc_ok else 'OFFLINE'}]")
    if remote_mode:
        print(f"  Bind:  {host}:{port}  <- 接受局域网/远程访问")
        for _ip in [h for h in local_host_names()
                    if h not in ("127.0.0.1", "localhost", "[::1]", "::1")][:4]:
            print(f"  LAN:   http://{_ip}:{port}/mcp")
        print(f"  Hosts: {len(_runtime_allowed_hosts)} allowed pattern(s) "
              f"(DNS-rebinding protection ON)")
    print(f"  Close window to stop")
    print("=" * 50)

    # 构建 Starlette app（访问控制仅由进程白名单 MCP_ALLOWED_PROCESSES 负责）
    starlette_app = mcp.streamable_http_app()

    # 请求上下文中间件：产物链接按请求 Host 推导（放在白名单/探针内层）
    starlette_app.add_middleware(RequestBaseURLMiddleware, allowed_bases=allowed_bases)

    # 进程白名单守卫（阶段 1：探针只打印来源进程；阶段 2：配了 MCP_ALLOWED_PROCESSES 才拦截）
    # 远程模式（--host 非环回）下白名单必然失效：远程客户端反查不到来源进程，若仍启用
    # 会把所有远程请求拦成 403 PROCESS_UNKNOWN → 强制忽略 MCP_ALLOWED_PROCESSES。
    if _cfg.mcp_allowed_processes and not remote_mode:
        allowed = {p.strip() for p in _cfg.mcp_allowed_processes.split(",") if p.strip()}
        starlette_app.add_middleware(ProcessWhitelistMiddleware, server_port=port, allowed=allowed)
        print(f"  Guard:  process whitelist enabled ({len(allowed)} pattern(s))")
    elif _cfg.mcp_probe_enabled:
        starlette_app.add_middleware(ProcessProbeMiddleware, server_port=port)
        print("  Probe:  process probe enabled (logging /mcp source process)")
    else:
        print("  Guard:  disabled (no whitelist, probe off)")
    if remote_mode and _cfg.mcp_allowed_processes:
        print("  Note:   MCP_ALLOWED_PROCESSES is set but IGNORED in remote mode "
              "(remote clients cannot be process-whitelisted)")

    # 给 uvicorn 控制台日志（访问日志 "INFO: ..."）加时间戳
    import copy as _copy
    _log_cfg = _copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    _log_cfg["formatters"]["default"]["fmt"] = "[%(asctime)s] %(levelprefix)s %(message)s"
    _log_cfg["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    _log_cfg["formatters"]["access"]["fmt"] = '[%(asctime)s] %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
    _log_cfg["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    uvicorn.run(starlette_app, host=host, port=port, log_level="info", log_config=_log_cfg)

    # 正常退出
    _lifecycle_log("MCP_STOPPED")


def main() -> None:
    """CLI 入口 — 解析参数并启动 MCP 服务。"""
    parser = argparse.ArgumentParser(description="启动所有 MCP 服务")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=DEFAULT_TRANSPORT,
        help=f"通信方式（默认: {DEFAULT_TRANSPORT}）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"HTTP 服务端口（默认: {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_BIND_HOST,
        help=f"监听地址（默认: {DEFAULT_BIND_HOST}；0.0.0.0 = 接受局域网/远程访问）",
    )
    args = parser.parse_args()

    # 校验 --port 范围
    if args.port < 1 or args.port > 65535:
        print(f"错误：端口号无效（{args.port}），必须在 1-65535 之间。")
        sys.exit(1)

    # 校验 --host：IP 或主机名，不接受协议前缀/路径/端口
    bind_host = args.host.strip()
    if not bind_host or "://" in bind_host or any(c in bind_host for c in ("/", "\\", " ")):
        print(f"错误：监听地址无效（{args.host}），应为 IP 或主机名，不带协议/端口/路径。")
        sys.exit(1)

    if args.transport == "streamable-http":
        _setup_logging()
        _run_http_server(args.port, bind_host)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
