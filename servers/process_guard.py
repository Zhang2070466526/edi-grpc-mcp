r"""进程白名单守卫 —— 只放行指定进程访问 MCP 服务。

原理：OS 的 TCP 连接表记录「每条连接归哪个进程(PID)」。服务端拿到请求的
来源端口，反查 PID → exe 路径 + 命令行，与白名单比对。这是 OS 层身份，
客户端无法伪造。

白名单匹配：每个条目精确对应进程身份，不做裸子串/后缀匹配。分三种：
- 通用解释器/Shell 裸名（python、node、java 等）：硬编码拒绝，防止误配放行任意进程。
- 路径型条目（含 /）：匹配 exe 路径——精确相等或目录前缀，如 C:/.../Hermes.exe。
- 关键词型条目（不含 /）：等于完整命令行 token（空白分隔）或 exe 文件名（basename），
  如 hermes_cli.main、Hermes.exe。
因此 "r"、"python" 这类短串/裸解释器名不会命中；识别 agent 用完整模块名、exe 文件名
或完整 exe 路径。

阶段 1（探针）：ProcessProbeMiddleware 只打印来源进程，不拦截，用于确认白名单值。
阶段 2（白名单）：ProcessWhitelistMiddleware 未命中白名单返回 403。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_log = logging.getLogger("process_guard")

# 端口 → (exe, cmdline) 缓存：keep-alive 连接复用同一端口，避免每次扫全表
_TTL_SECONDS = 60
_cache: dict[int, tuple[float, tuple[str, str]]] = {}
_cache_lock = threading.Lock()


def _norm(s: str) -> str:
    """归一化：统一小写 + 正斜杠，便于跨平台比较。"""
    return s.replace("\\", "/").lower()


# 通用解释器/Shell 裸名：作为白名单条目无法标识具体 agent——python.exe 是任何
# python 进程的 argv[0]，node.exe 是任何 node 进程的 argv[0]。硬编码拒绝匹配，
# 迫使配置方改用命令行里的具体模块名（如 hermes_cli.main）或完整 exe 路径。
# 注意：此名单天然不完整，只能拦常见误配，真正的安全仍靠「具体模块名/完整路径」。
_GENERIC_RUNTIMES = frozenset({
    "python", "python.exe", "python3", "python3.exe", "pythonw", "pythonw.exe",
    "node", "node.exe",
    "java", "java.exe", "javaw", "javaw.exe",
    "ruby", "ruby.exe",
    "perl", "perl.exe",
    "bash", "bash.exe", "sh", "sh.exe", "zsh", "zsh.exe",
    "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
})


def _matches_whitelist(pattern: str, exe: str, cmd: str) -> bool:
    """精准匹配：pattern 必须精确对应进程身份，不做裸子串/后缀匹配。

    - 通用解释器/Shell 裸名（python、node、java 等）：硬编码拒绝。
    - 路径型条目（含 /）：匹配 exe 路径——精确相等或目录前缀。
      例：C:/.../Hermes.exe（完整路径）、C:/.../Hermes（目录）。
    - 关键词型条目（不含 /）：等于完整命令行 token（空白分隔），或等于 exe
      文件名（basename）。例：hermes_cli.main、Hermes.exe。
      因此 "python" 不会命中 "python.exe"（且 python 在硬编码黑名单）、
      "hermes_cli" 不会命中 "hermes_cli.main"、"r" 也不会误命中。
    """
    if not pattern:
        return False
    pattern = _norm(pattern)  # 幂等：_allowed 里已归一化，此处再归一化以防御直接调用
    if pattern in _GENERIC_RUNTIMES:
        return False
    if "/" in pattern:
        # 路径型：精确匹配 exe 路径，或作为目录前缀（pattern 后必须跟 / 才算边界）
        exe_n = _norm(exe)
        pattern = pattern.rstrip("/")
        return exe_n == pattern or exe_n.startswith(pattern + "/")
    # 关键词型：整词匹配命令行 token，或等于 exe 文件名（basename）
    if pattern in _norm(cmd).split():
        return True
    exe_basename = _norm(exe).rstrip("/").rsplit("/", 1)[-1]
    return pattern == exe_basename


def find_client_process(client_port: int, server_port: int) -> tuple[str, str]:
    """反查来源端口对应的进程 (exe 完整路径, 命令行)；查不到返回 ('', '')。

    连接归客户端进程所有：conn.laddr 是客户端地址（来源端口），
    conn.raddr 是服务端地址（服务端口）。
    """
    import psutil
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_ESTABLISHED:
            continue
        if not conn.laddr or not conn.raddr:
            continue
        if conn.raddr.port == server_port and conn.laddr.port == client_port:
            try:
                p = psutil.Process(conn.pid)
                exe = p.exe() or ""
                cmd = " ".join(p.cmdline() or []) or ""
                return exe, cmd
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return "", ""
    return "", ""


def resolve_client_process(client_port: int, server_port: int) -> tuple[str, str]:
    """带缓存的来源进程查询。"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(client_port)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    exe, cmd = find_client_process(client_port, server_port)
    with _cache_lock:
        _cache[client_port] = (now, (exe, cmd))
    return exe, cmd


class ProcessProbeMiddleware(BaseHTTPMiddleware):
    """调试探针：打印每条 MCP 请求的来源进程 exe + 命令行（不拦截）。"""

    def __init__(self, app, server_port: int):
        super().__init__(app)
        self._server_port = server_port

    async def dispatch(self, request: Request, call_next):
        client = request.client
        if client:
            exe, cmd = resolve_client_process(client.port, self._server_port)
            print(f"[process-probe] {request.url.path} <- {client.host}:{client.port} "
                  f"exe={exe} cmd={cmd}", flush=True)
        return await call_next(request)


# 需要进程白名单保护的路径：仅 /mcp 是 agent 直连的 MCP 协议端点。
# /ui /chat /tools/list /upload 依赖浏览器访问，/health /ready /metrics 是诊断路径，均放行。
_PROTECTED_PREFIXES = ("/mcp",)


class ProcessWhitelistMiddleware(BaseHTTPMiddleware):
    """只放行白名单内的进程；未命中返回 403。白名单为空 = 不拦截。

    仅对受保护路径（/mcp）做进程白名单校验；/ui /chat /tools/list /upload
    依赖浏览器访问，/health /ready /metrics 是诊断路径，均放行。
    """

    def __init__(self, app, server_port: int, allowed: Iterable[str],
                 fail_open: bool = False):
        super().__init__(app)
        self._server_port = server_port
        self._allowed = [_norm(a) for a in allowed if a]
        self._fail_open = fail_open

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")
        if any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES):
            client = request.client
            if client and self._allowed:
                exe, cmd = resolve_client_process(client.port, self._server_port)
                if not exe and not cmd:
                    if not self._fail_open:
                        _log.warning("无法识别来源进程，拒绝 %s:%s path=%s", client.host, client.port, path)
                        return JSONResponse({
                            "error": "forbidden",
                            "error_code": "PROCESS_UNKNOWN",
                            "message": "无法识别来源进程，拒绝访问 /mcp",
                            "hint": "确认客户端与服务端同机；请检查白名单设置",
                        }, status_code=403)
                    _log.warning("无法识别来源进程，fail_open 放行 %s:%s path=%s", client.host, client.port, path)
                else:
                    matched = next((a for a in self._allowed if _matches_whitelist(a, exe, cmd)), None)
                    if matched is None:
                        _log.warning("拒绝非白名单进程 exe=%s cmd=%s path=%s", exe, cmd, path)
                        return JSONResponse({
                            "error": "forbidden",
                            "error_code": "PROCESS_NOT_ALLOWED",
                            "message": "来源进程不在白名单，拒绝访问 /mcp",
                            "exe": exe,
                            "cmdline": cmd,
                            "hint": "来源进程不在白名单，拒绝访问",
                        }, status_code=403)
                    # _log.info("放行白名单进程 pattern=%r exe=%s cmd=%s path=%s", matched, exe, cmd, path)
                    # print("放行白名单进程 pattern={!r} exe={} cmd={} path={}".format(matched, exe, cmd, path))
        return await call_next(request)
