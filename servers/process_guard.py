"""进程白名单守卫 —— 只放行指定进程访问 MCP 服务。

原理：OS 的 TCP 连接表记录「每条连接归哪个进程(PID)」。服务端拿到请求的
来源端口，反查 PID → exe 路径 + 命令行，与白名单比对。这是 OS 层身份，
客户端无法伪造。

白名单匹配：每个条目作为「子串」在 `exe路径 + 空格 + 命令行` 里查找，
命中任一即放行。因此既可填 exe 完整路径（如 C:/.../Hermes.exe），
也可填命令行关键词（如 hermes_cli，用于「通用 python.exe 跑专属模块」的场景）。

阶段 1（探针）：ProcessProbeMiddleware 只打印来源进程，不拦截，用于确认白名单值。
阶段 2（白名单）：ProcessWhitelistMiddleware 未命中白名单返回 403。

参考文档：docs/ACCESS_CONTROL.md
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
                        _log.warning("无法识别来源进程，拒绝 %s:%s", client.host, client.port)
                        return JSONResponse({"error": "forbidden"}, status_code=403)
                else:
                    haystack = _norm(exe + " " + cmd)
                    if not any(a in haystack for a in self._allowed):
                        _log.warning("拒绝非白名单进程 exe=%s cmd=%s", exe, cmd)
                        return JSONResponse({"error": "forbidden"}, status_code=403)
        return await call_next(request)
