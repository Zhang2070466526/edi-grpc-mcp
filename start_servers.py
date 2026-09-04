r"""EDI gRPC MCP 一键启动 — 统一入口。

═══════════════════════════════════════════════════════════
  工具注册见：servers/registry_server.py
  工具定义见：servers/eda/*.py

  启动方式：
    uv run python start_servers.py                          # streamable-http（默认）
    uv run python start_servers.py --transport stdio         # Claude Code
    uv run python start_servers.py --port 9000               # 自定义端口

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
from starlette.responses import JSONResponse

# load_dotenv 必须在 import servers 之前：否则 servers/__init__.py 的
# get_settings() 会先执行并被 lru_cache 缓存，frozen 下 env_file 路径不存在、
# 环境变量尚未加载，导致读到空的 mcp_api_key。
if getattr(sys, "frozen", False):
    load_dotenv(Path(sys.executable).parent / ".env")
else:
    load_dotenv()

from servers.settings import get_settings

# -- 配置（从统一配置读取）--
_cfg = get_settings()
DEFAULT_TRANSPORT = _cfg.mcp_transport
DEFAULT_HOST = _cfg.mcp_host
DEFAULT_PORT = _cfg.mcp_port

# ── 启动时配置校验 ──
_cfg_issues = _cfg.validate()
if _cfg_issues:
    print("WARNING: 配置存在问题 —")
    for issue in _cfg_issues:
        print(f"  - {issue}")
    print()

from servers import mcp, __version__ as _server_ver
from servers.eda.config import EDA_GRPC_SERVER as _grpc_cfg_addr
from servers.utils import set_server_address
import servers.registry_server  # — 触发工具注册

# ── 运行时状态 ──
_server_ready = threading.Event()
_server_stopping = threading.Event()
_log = logging.getLogger("edi_mcp")


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
    """按大小轮转的文件日志，写入 %TEMP%/edi/data/log/。"""
    import tempfile as _tmp
    from datetime import datetime as _dt
    log_dir = Path(_tmp.gettempdir()) / "edi" / "data" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = f"edi_mcp_{_dt.now().strftime('%Y%m')}.log"

    handler = RotatingFileHandler(
        log_dir / log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root = logging.getLogger()
    root.addHandler(handler)
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

    tools = [t.name for t in mcp._tool_manager._tools.values()]
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
        "transport": "streamable-http",
        "stateless": True,
        "version": _server_ver,
        "grpc": "online" if grpc_ok else "offline",
        "tool_count": len(tools),
        "tools_hash": tools_hash,
        "started_at": SERVER_STARTED_AT,
    })


# 需要 token 鉴权的路径（健康检查 /health /ready /metrics 放行）
_AUTH_REQUIRED_PREFIXES = ("/mcp", "/ui", "/chat", "/tools/list", "/upload")


class _TokenAuthMiddleware(BaseHTTPMiddleware):
    """校验敏感端点请求的访问令牌（URL query 参数 ?token=xxx）。

    用于「只允许指定 agent 访问」：配置 MCP_API_KEY 后，只有带正确 ?token=
    的请求才能访问 /mcp、/ui、/chat 等，其余返回 401。
    """

    def __init__(self, app, expected_token: str):
        super().__init__(app)
        self._expected_token = expected_token

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")
        if any(path == p or path.startswith(p + "/") for p in _AUTH_REQUIRED_PREFIXES):
            if request.query_params.get("token", "") != self._expected_token:
                return JSONResponse(
                    {"error": "unauthorized", "error_description": "invalid or missing token"},
                    status_code=401,
                )
        return await call_next(request)


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


def _run_http_server(port: int, transport: str = "streamable-http") -> None:
    """Streamable HTTP 模式入口。"""
    _install_shutdown_handlers()

    # 冻结模式无控制台时，重定向 stdout/stderr 避免 uvicorn 日志报错
    if getattr(sys, "frozen", False) and sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    host = DEFAULT_HOST or "127.0.0.1"
    if host != "127.0.0.1":
        print(f"WARNING: MCP_HOST={host} ignored, forcing 127.0.0.1 (local mode)")
        host = "127.0.0.1"

    # 端口占用检查：被占用时自动结束占用进程，释放后继续启动
    _test = socket.socket()
    try:
        _test.settimeout(1)
        occupied = _test.connect_ex(("127.0.0.1", port)) == 0
    finally:
        _test.close()

    if occupied:
        print(f"端口 {port} 已被占用，尝试自动结束占用进程后重启...")
        if not _kill_port_process(port):
            print(f"无法结束占用端口 {port} 的进程，请手动处理。")
            sys.exit(1)
        # 等待端口释放（进程退出后端口可能短暂处于 TIME_WAIT）
        deadline = time.monotonic() + 5
        while True:
            _test = socket.socket()
            try:
                _test.settimeout(1)
                if _test.connect_ex(("127.0.0.1", port)) != 0:
                    break
            finally:
                _test.close()
            if time.monotonic() >= deadline:
                print(f"端口 {port} 未能释放，请手动处理。")
                sys.exit(1)
            time.sleep(0.5)
        print(f"端口 {port} 已释放，继续启动。")

    if sys.platform == "win32" and sys.version_info < (3, 14):
        asyncio.set_event_loop_policy(
            asyncio.WindowsSelectorEventLoopPolicy()
        )

    tools = [t.name for t in mcp._tool_manager._tools.values()]

    # 检测 50055 状态
    _grpc_host, _grpc_port = _grpc_cfg_addr.rsplit(":", 1)
    _test = socket.socket()
    _test.settimeout(1)
    _grpc_ok = _test.connect_ex((_grpc_host, int(_grpc_port))) == 0
    _test.close()

    # 注册 /ready 路由
    mcp.custom_route("/ready", methods=["GET"])(ready_check)

    # 标记就绪
    _server_ready.set()
    _lifecycle_log("MCP_READY", tools=len(tools),
                   grpc="online" if _grpc_ok else "offline")

    print("=" * 50)
    print(f"  EDI gRPC MCP v{_server_ver}  (streamable-http, stateless)")
    print(f"  UI:    http://{host}:{port}/ui")
    print(f"  MCP:   http://{host}:{port}/mcp")
    print(f"  Ready: http://{host}:{port}/ready")
    print(f"  Tools: {len(tools)} loaded")
    print(f"  gRPC:  {_grpc_cfg_addr} [{'ONLINE' if _grpc_ok else 'OFFLINE'}]")
    print(f"  Close window to stop")
    print("=" * 50)

    mcp.settings.host = host
    mcp.settings.port = port
    set_server_address(host, port)

    # 构建 Starlette app，按需加 token 鉴权中间件（只允许指定 agent 访问 /mcp）
    starlette_app = mcp.streamable_http_app()
    if _cfg.mcp_api_key:
        starlette_app.add_middleware(_TokenAuthMiddleware, expected_token=_cfg.mcp_api_key)
        print(f"  Auth:   enabled (?token={_cfg.mcp_api_key} required on /mcp)")
    else:
        print("  Auth:   disabled (MCP_API_KEY not set)")

    uvicorn.run(starlette_app, host=host, port=port, log_level="info")

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
    args = parser.parse_args()

    # 校验 --port 范围
    if args.port < 1 or args.port > 65535:
        print(f"错误：端口号无效（{args.port}），必须在 1-65535 之间。")
        sys.exit(1)

    if args.transport == "streamable-http":
        _setup_logging()
        _run_http_server(args.port, transport=args.transport)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
