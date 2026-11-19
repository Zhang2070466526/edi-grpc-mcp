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
from servers.simulation_tool.config import REPORT_EXPORTER_PATH, TR_SIMULATOR_PATH
from servers.utils import registered_tools

import sys as _sys
if getattr(_sys, "frozen", False):
    _CLIENT_HTML_PATH = Path(_sys._MEIPASS) / "servers" / "chat" / "index.html"
    _TOOL_BROWSER_HTML_PATH = Path(_sys._MEIPASS) / "servers" / "chat" / "tool_browser.html"
else:
    _CLIENT_HTML_PATH = Path(__file__).resolve().parent / "index.html"
    _TOOL_BROWSER_HTML_PATH = Path(__file__).resolve().parent / "tool_browser.html"


async def _check_tcp(endpoint: str) -> bool:
    """TCP 端口连通性检查。"""
    try:
        host, port_text = endpoint.rsplit(":", 1)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port_text)),
            timeout=0.15,
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
             for t in registered_tools(mcp)]
    return JSONResponse(tools)


# ── 工具浏览器（按领域分组可视化）──

#: 工具两级归类（模块前缀 → 一级「引擎/通信方式」、二级「领域/模块」）。
#: 一级把所有走 EDI gRPC 通信的工具收在一起；新增工具落在这些模块下自动归类，无需手维护。
_TOOL_CLASSIFY: list[tuple[str, str, str]] = [
    # ── EDI · gRPC 通信（直接 call_grpc）──
    ("servers.eda.project_manage", "EDI · gRPC", "工程管理"),
    ("servers.eda.simulation", "EDI · gRPC", "仿真"),
    ("servers.eda.simulation_components", "EDI · gRPC", "仿真器件"),
    ("servers.eda.design_export", "EDI · gRPC", "导出与分析"),
    ("servers.eda.signal_chain", "EDI · gRPC", "信号链"),
    ("servers.eda.model_replace", "EDI · gRPC", "模型替换"),
    ("servers.eda.model_library", "EDI · gRPC", "模型库"),
    ("servers.eda.soft_ip", "EDI · gRPC", "软 IP"),
    ("servers.eda.edi_launcher", "EDI · gRPC", "启动/诊断"),
    ("servers.eda.workspace_ops", "EDI · gRPC", "工作区"),
    ("servers.eda.schematic_ops", "EDI · gRPC", "原理图扩展"),
    # ── 独立仿真 exe（subprocess，非直接 gRPC）──
    ("servers.simulation_tool", "独立仿真工具", "仿真/报告"),
    # ── 其它引擎 ──
    ("servers.ansys", "ANSYS HFSS", "ANSYS HFSS"),
    ("servers.cst", "CST", "CST"),
    ("servers.laspcem", "LASPCEM", "LASPCEM"),
    # ── 本地纯计算 ──
    ("servers.microwave", "本地纯计算", "微波计算"),
    ("servers.rf_analysis", "本地纯计算", "射频链路"),
    # ── 图表/图片/文档 ──
    ("servers.turbocharts", "图表/图片/文档", "图表"),
    ("servers.multimodal_vision", "图表/图片/文档", "图片/文档"),
    # ── 自描述 ──
    ("servers.describe_tool", "自描述", "工具查询"),
]

#: 一级分组的展示顺序
_GROUP_ORDER: list[str] = [
    "EDI · gRPC", "独立仿真工具", "ANSYS HFSS", "CST", "LASPCEM",
    "本地纯计算", "图表/图片/文档", "自描述",
]


def _classify(module: str) -> tuple[str, str]:
    """返回 (一级分组, 二级领域)；匹配不到回「其他」。"""
    for prefix, group, domain in _TOOL_CLASSIFY:
        if module == prefix or module.startswith(prefix + "."):
            return group, domain
    return "其他", "其他"


async def tool_browser_page(request: Request):
    """GET /tools — 工具浏览器页面（按领域分组，点击看用法）。"""
    if _TOOL_BROWSER_HTML_PATH.is_file():
        return HTMLResponse(_TOOL_BROWSER_HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h2>tool_browser.html not found</h2>", status_code=404)


async def tool_browser_api(request: Request):
    """GET /tools/grouped — 按「引擎 → 领域」两级分组的工具（name/description/usage/标注）。"""
    groups: dict[str, dict[str, list[dict]]] = {}
    for t in registered_tools(mcp):
        fn = getattr(t, "fn", None)
        ann = getattr(t, "annotations", None)
        group, domain = _classify(getattr(fn, "__module__", "") or "")
        groups.setdefault(group, {}).setdefault(domain, []).append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "usage": (getattr(fn, "__doc__", "") or "").strip(),
            "readonly": bool(getattr(ann, "readOnlyHint", False)),
            "destructive": bool(getattr(ann, "destructiveHint", False)),
        })

    def _order(group: str) -> int:
        return _GROUP_ORDER.index(group) if group in _GROUP_ORDER else len(_GROUP_ORDER)

    result = [
        {
            "group": g,
            "domains": [
                {"domain": d, "tools": sorted(items, key=lambda x: x["name"])}
                for d, items in sorted(domains.items())
            ],
        }
        for g, domains in sorted(groups.items(), key=lambda kv: _order(kv[0]))
    ]
    return JSONResponse({"total": len(registered_tools(mcp)), "groups": result})


# ── 健康检查 ──

async def health_check(request: Request):
    """GET /health — 健康检查：gRPC + TurboCharts / 独立仿真 exe 状态 + 版本号。"""
    eda_ready = await _check_tcp(EDA_GRPC_SERVER)
    turbocharts_ready = bool(TURBOCHARTS_PATH) and Path(TURBOCHARTS_PATH).is_file()
    simulation_tool_ready = all(Path(p).is_file() for p in (REPORT_EXPORTER_PATH, TR_SIMULATOR_PATH))
    tools = sorted(t.name for t in registered_tools(mcp))
    tools_hash = hashlib.md5(",".join(tools).encode("utf-8")).hexdigest()[:8]
    return JSONResponse({
        "status": "ok" if eda_ready else "degraded",
        "version": _ver,
        "mcp_ready": True,
        "eda_grpc_ready": eda_ready,
        "turbocharts_ready": turbocharts_ready,
        "simulation_tool_ready": simulation_tool_ready,
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
