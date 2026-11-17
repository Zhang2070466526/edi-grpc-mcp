"""MCP 协议层兼容补丁 —— 两处与规范不一致的行为（2026-11-17 实测）。

1. ``GET /mcp`` 挂住不返回：SDK 的 streamable-http 对 GET 走 SSE 长连接分支，
   本服务没有服务端推送需求、也不发 keep-alive 帧 ⇒ 客户端拿到 0 字节并卡到
   自己超时（实测 8 s socket timeout）。按规范，服务端不支持推送时应快速回
   405 + Allow 头，而不是让连接悬着。

2. 未知 JSON-RPC 方法回 -32602 而非 -32601：SDK 的 ``shared/session.py`` 用
   ``except Exception`` 把分发失败统一翻译成
   ``{"code": -32602, "message": "Invalid request parameters", "data": ""}``，
   客户端/中间件无法区分「方法不存在」和「参数错」。

做法：一个只作用于 ``/mcp`` 的轻量 ASGI 中间件，在进入 SDK 之前拦住这两种情况，
其余请求（含请求体）原样透传给下层。不依赖 SDK 内部实现细节。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

#: 精确匹配的 MCP 方法名（非 `xxx/yyy` 形态的那些）。
_KNOWN_EXACT: frozenset[str] = frozenset({"initialize", "ping"})

#: 按命名空间前缀放行的 MCP 方法，避免将来新增子方法被误判为「方法不存在」。
_KNOWN_PREFIXES: tuple[str, ...] = (
    "tools/", "prompts/", "resources/", "notifications/", "completion/",
    "tasks/", "elicitation/", "sampling/", "roots/", "logging/",
)


def _is_known_method(method: str) -> bool:
    return method in _KNOWN_EXACT or method.startswith(_KNOWN_PREFIXES)


async def _send_json(send: Send, status: int, payload: dict,
                     extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


class ProtocolCompatMiddleware:
    """只拦 ``/mcp`` 的 GET 与未知 JSON-RPC 方法，其余一律透传。"""

    def __init__(self, app: ASGIApp, mcp_path: str = "/mcp") -> None:
        self.app = app
        self.mcp_path = mcp_path.rstrip("/") or "/"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or (scope.get("path", "").rstrip("/") or "/") != self.mcp_path:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")

        # 1) GET：本服务不提供 SSE 推送流 → 快速 405，别让客户端悬着
        if method == "GET":
            await _send_json(
                send, 405,
                {
                    "error": "method_not_allowed",
                    "message": "GET /mcp 不支持：本服务为无状态 streamable-http，不提供服务端推送流。"
                               "请用 POST /mcp（JSON-RPC）；DELETE /mcp 用于结束会话。",
                    "allow": ["POST", "DELETE"],
                },
                extra_headers=[(b"allow", b"POST, DELETE")],
            )
            return

        # 2) POST：读全请求体 → 只检查 JSON-RPC method → 原样重放给 SDK
        if method == "POST":
            body = b""
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break

            consumed = False

            async def replay() -> Message:
                """第一次吐出完整 body，之后转发原始 receive（等客户端断开）。"""
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            payload: Any = None
            if body.strip():
                try:
                    payload = json.loads(body)
                except (ValueError, UnicodeDecodeError):
                    payload = None  # 非法 JSON 交回 SDK，让它按 -32700 处理

            if isinstance(payload, dict):
                rpc_method = payload.get("method")
                if isinstance(rpc_method, str) and not _is_known_method(rpc_method):
                    await _send_json(
                        send, 200,
                        {
                            "jsonrpc": "2.0",
                            "id": payload.get("id"),
                            "error": {
                                "code": -32601,
                                "message": f"Method not found: {rpc_method}",
                                "data": {
                                    "method": rpc_method,
                                    "hint": "MCP 方法形如 initialize / tools/list / tools/call / prompts/get；"
                                            "工具名不是方法名 —— 调工具用 tools/call 且 params.name=工具名。",
                                },
                            },
                        },
                    )
                    return

            await self.app(scope, replay, send)
            return

        await self.app(scope, receive, send)
