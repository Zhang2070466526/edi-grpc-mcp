"""协议层兼容补丁 + 工具 annotations 的回归测试（2026-11-17）。

覆盖三处此前与 MCP 规范不一致的行为：
  ① GET /mcp 悬着不返回 → 现在快速 405（客户端不再卡到 socket 超时）；
  ② 未知 JSON-RPC 方法回 -32602「Invalid request parameters」→ 现在 -32601 且带方法名；
  ③ 101 个工具 annotations 全为空 → 现在按 readOnly/destructive 分类标注。

中间件用「手写 ASGI 调用 + 假下游」测，不启动 SDK 的 StreamableHTTPSessionManager
（那个实例的 lifespan 只能 run 一次，测试里反复起会互相干扰）。
真实透传（tools/list / tools/call 经 SDK 返回 200）在源码态实例上另外真调验证过。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import servers.registry_server  # noqa: F401  注册全部工具
from servers import mcp
from servers.protocol_compat import ProtocolCompatMiddleware

_CAPTURED: dict[str, Any] = {}


async def _fake_downstream(scope, receive, send):
    """假下游：读全 body 后回一个 JSON，用来验证中间件的透传与重放。"""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    _CAPTURED["body"] = body
    _CAPTURED["method"] = scope.get("method")
    payload = json.dumps({"ok": True, "downstream": True}).encode()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode())]})
    await send({"type": "http.response.body", "body": payload, "more_body": False})


def _run(app, method: str, body: bytes = b"") -> tuple[int, dict | None, dict]:
    """把一次 ASGI 请求跑完，返回 (status, json_body, headers_dict)。"""
    _CAPTURED.clear()
    scope = {
        "type": "http", "method": method, "path": "/mcp", "query_string": b"",
        "headers": [(b"accept", b"application/json, text/event-stream")],
    }
    queue = [{"type": "http.request", "body": body, "more_body": False}]
    sent: list[dict] = []

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))

    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), 0)
    headers = dict(next((m["headers"] for m in sent if m["type"] == "http.response.start"), []))
    raw = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    return status, payload, headers


# ── ① GET /mcp 不再挂住 ──────────────────────────────────────

def test_get_mcp_returns_405():
    app = ProtocolCompatMiddleware(_fake_downstream)
    status, payload, headers = _run(app, "GET")
    assert status == 405
    assert b"POST" in headers.get(b"allow", b"")
    assert payload["error"] == "method_not_allowed"


# ── ② 未知方法 -32601 ────────────────────────────────────────

def test_unknown_method_returns_32601():
    app = ProtocolCompatMiddleware(_fake_downstream)
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "no/such", "params": {}}).encode()
    status, payload, _ = _run(app, "POST", body)
    assert status == 200
    assert payload["error"]["code"] == -32601
    assert "no/such" in payload["error"]["message"]
    assert payload["error"]["data"]["method"] == "no/such"
    assert "body" not in _CAPTURED, "未知方法不该被转发给下游"


# ── ③ 正常请求必须原样透传（含 body 重放）────────────────────

def test_known_method_is_passed_through_with_body():
    app = ProtocolCompatMiddleware(_fake_downstream)
    body = json.dumps({"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}}).encode()
    status, payload, _ = _run(app, "POST", body)
    assert status == 200
    assert payload == {"ok": True, "downstream": True}
    assert _CAPTURED["body"] == body, "重放给下游的 body 必须与客户端发送的完全一致"
    assert _CAPTURED["method"] == "POST"


def test_notification_and_initialize_pass_through():
    """按前缀放行的命名空间不能被误判成「方法不存在」。"""
    app = ProtocolCompatMiddleware(_fake_downstream)
    for method in ("initialize", "ping", "notifications/initialized",
                   "prompts/list", "resources/read", "logging/setLevel"):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode()
        status, payload, _ = _run(app, "POST", body)
        assert status == 200 and payload.get("downstream") is True, f"{method} 被误拦"


def test_non_mcp_path_is_untouched():
    """非 /mcp 路径（/health、/metrics 等）不受影响。"""
    app = ProtocolCompatMiddleware(_fake_downstream)
    scope = {"type": "http", "method": "GET", "path": "/health", "query_string": b"", "headers": []}
    sent: list[dict] = []
    queue = [{"type": "http.request", "body": b"", "more_body": False}]

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    assert next(m["status"] for m in sent if m["type"] == "http.response.start") == 200


# ── ④ annotations ───────────────────────────────────────────

def test_every_tool_has_annotations():
    tools = mcp._tool_manager.list_tools()
    assert tools, "工具未注册"
    missing = [t.name for t in tools if t.annotations is None]
    assert not missing, f"这些工具缺 annotations: {missing}"
    assert all(a.openWorldHint is False for a in (t.annotations for t in tools))


def test_annotations_classify_readonly_and_destructive():
    by = {t.name: t.annotations for t in mcp._tool_manager.list_tools()}
    # 破坏性：必须标出来（客户端据此弹二次确认）
    assert by["clear_schematic"].destructiveHint is True
    assert by["clear_schematic"].readOnlyHint is False
    assert by["replace_schematic_from_file"].destructiveHint is True
    assert by["close_hfss_project"].destructiveHint is True
    # 只读：不能被误标成破坏性，否则每次查询都弹确认
    assert by["get_service_status"].readOnlyHint is True
    assert by["get_service_status"].destructiveHint is False
    assert by["describe_tool"].readOnlyHint is True
    # 非幂等的写操作不能声称可重试
    assert by["run_tr_simulation"].idempotentHint is False
    assert by["find_netlist_paths"].readOnlyHint is False
