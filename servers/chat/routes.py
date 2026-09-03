r"""Web 路由 — /health, /chat, /ui, /tools/list, /。

职责：
  - 请求解析与格式校验
  - 委托 ChatService 处理聊天逻辑
  - /health 健康检查、/ui 静态页面、/tools/list 工具列表
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from servers import mcp, __version__ as _ver
from servers.chat.service import ChatService
from servers.eda.config import EDA_GRPC_SERVER, TURBOCHARTS_PATH

import sys as _sys
if getattr(_sys, "frozen", False):
    _CLIENT_HTML_PATH = Path(_sys._MEIPASS) / "servers" / "chat" / "index.html"
else:
    _CLIENT_HTML_PATH = Path(__file__).resolve().parent / "index.html"


async def _check_tcp(endpoint: str) -> bool:
    """TCP 端口连通性检查。"""
    try:
        host, port_text = endpoint.rsplit(":", 1)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_text)),
            timeout=0.5,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, ValueError, asyncio.TimeoutError):
        return False


# ── 静态页面 ──

async def ui_page(request: Request):
    """GET /ui — 返回 Chat 聊天界面（单页应用）。"""
    if _CLIENT_HTML_PATH.is_file():
        return HTMLResponse(_CLIENT_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>chat_client.html not found</h2>", status_code=404)


# ── 工具列表 ──

async def tool_list(request: Request):
    """GET /tools/list — 返回已注册 MCP 工具名称和描述列表。"""
    tools = [{"name": t.name, "description": t.description or ""}
             for t in mcp._tool_manager._tools.values()]
    return JSONResponse(tools)


# ── 健康检查 ──

async def health_check(request: Request):
    """GET /health — 健康检查：gRPC + TurboCharts 状态 + 版本号。"""
    eda_ready = await _check_tcp(EDA_GRPC_SERVER)
    turbocharts_ready = bool(TURBOCHARTS_PATH) and Path(TURBOCHARTS_PATH).is_file()
    tools = sorted(t.name for t in mcp._tool_manager._tools.values())
    tools_hash = hashlib.md5(",".join(tools).encode("utf-8")).hexdigest()[:8]
    return JSONResponse({
        "status": "ok" if eda_ready else "degraded",
        "version": _ver,
        "mcp_ready": True,
        "eda_grpc_ready": eda_ready,
        "turbocharts_ready": turbocharts_ready,
        "eda_grpc_server": EDA_GRPC_SERVER,
        "tool_count": len(tools),
        "tools_hash": tools_hash,
    })


# ── 聊天 ──

async def chat_endpoint(request: Request):
    """POST /chat — 委托 ChatService 处理多轮工具调用闭环。

    请求: {"session_id": "...", "message": "..."}
    返回: {"success": bool, "reply": "...", "activities": [...], "context": {...}}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)

    session_id = body.get("session_id", "")
    message = body.get("message", "")

    if not isinstance(session_id, str):
        return JSONResponse({"error": "session_id must be a string"}, status_code=400)
    if not isinstance(message, str):
        return JSONResponse({"error": "message must be a string"}, status_code=400)

    session_id = session_id.strip()
    message = message.strip()

    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    response = await ChatService.instance().chat(session_id, message)
    return JSONResponse(response.to_dict())


# ── 文件上传 ──
import tempfile  # noqa: E402
import uuid as _uuid  # noqa: E402

_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB 上传上限


async def upload_file(request: Request):
    """POST /upload — 上传文件到临时目录，返回本地路径供 Chat 工具使用。"""
    try:
        form = await request.form()
        uploaded = form.get("file")
        if uploaded is None:
            return JSONResponse({"error": "no file"}, status_code=400)

        name = uploaded.filename or "uploaded_file"
        suffix = Path(name).suffix or ".tmp"
        safe_name = f"{_uuid.uuid4().hex[:8]}_{Path(name).stem}{suffix}"

        dest_dir = Path(tempfile.gettempdir()) / "mcp" / "uploads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name

        # 限制上传大小，避免超大文件耗尽磁盘
        too_large = False
        total = 0
        with open(dest, "wb") as f:
            while True:
                chunk = uploaded.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    too_large = True
                    break
                f.write(chunk)
        if too_large:
            try:
                dest.unlink()
            except OSError:
                pass
            return JSONResponse({"error": "file too large"}, status_code=413)

        return JSONResponse({
            "success": True,
            "file_path": str(dest.resolve()),
            "file_name": name,
            "file_size": dest.stat().st_size,
        })
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
