"""图片显示 — 返回 MCP ImageContent（Base64 内嵌），不调模型，不复制文件。

功能：
  show_image           — 读取本地图片，≤10MB 返回 ImageContent，>10MB 返回本地路径提示
  register_image_url   — 生成临时 HTTP Token（10 分钟有效），供 Chat 前端渲染
  _workspace_note      — 根据 OPENCLAW_WORKSPACE 配置返回自适应提示文案
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.types import ImageContent, TextContent
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from servers import mcp
from servers.token_registry import TokenStore
from servers.multimodal_vision.validators import validate_image_path, IMAGE_MIME_MAP
from servers.multimodal_vision.workspace_copy import OPENCLAW_WORKSPACE_PATH

load_dotenv()
_logger = logging.getLogger("multimodal.display")

_MAX_NATIVE_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
_image_store = TokenStore(route="/images")

_MIME_TYPES = IMAGE_MIME_MAP


# ═══════════════════════════════════════════════════════════
# show_image
# ═══════════════════════════════════════════════════════════

def _workspace_note() -> str:
    """根据工作区配置返回相应的提示文案。"""
    if OPENCLAW_WORKSPACE_PATH is not None:
        return "如需在工作区内查看，可要求复制到 OpenClaw 工作区。"
    return "当前未配置 OPENCLAW_WORKSPACE，请使用资源管理器打开该文件。"


@mcp.tool(structured_output=False)
def show_image(image_path: str) -> list[Any]:
    """读取本地图片，返回标准 MCP ImageContent 和本地路径。

    不复制文件，不调用工作区工具。即使客户端无法渲染 MCP ImageContent，
    MCP 服务本身也是调用成功的——能否显示取决于客户端的渲染能力和目录权限。

    Args:
        image_path: 图片文件绝对路径。
    """
    path = validate_image_path(image_path)
    ws_note = _workspace_note()

    size = path.stat().st_size
    if size > _MAX_NATIVE_IMAGE_SIZE:
        return [
            TextContent(
                type="text",
                text=(
                    "图片文件较大（>10 MB），未内嵌到 MCP 返回内容中。\n\n"
                    f"本地路径：{path}\n\n"
                    f"{ws_note}"
                ),
            ),
        ]

    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    mime = _MIME_TYPES.get(path.suffix.lower(), "application/octet-stream")

    return [
        TextContent(
            type="text",
            text=(
                "图片已返回（MCP ImageContent）。\n"
                f"本地路径：{path}\n"
                "客户端能否直接渲染取决于其图片支持和文件访问策略——"
                "即使显示为 Unavailable，也不代表 MCP 调用失败。\n"
                f"{ws_note}"
            ),
        ),
        ImageContent(type="image", data=encoded, mimeType=mime),
    ]


# ═══════════════════════════════════════════════════════════
# HTTP Token — 供 Chat 界面渲染图片
# ═══════════════════════════════════════════════════════════

def register_image_url(img_path: str) -> str:
    """注册图片临时 Token（10 分钟有效），供 Chat 前端通过 /images/{token} 访问。"""
    p = validate_image_path(img_path)
    _, url = _image_store.register(str(p))
    return url


# ═══════════════════════════════════════════════════════════
# HTTP 路由
# ═══════════════════════════════════════════════════════════

async def serve_image(request: Request) -> FileResponse | JSONResponse:
    """GET /images/{token} — 根据 Token 返回图片文件，10 分钟过期。"""
    token = request.path_params.get("token", "")
    entry = _image_store.lookup(token)
    if entry is None:
        return JSONResponse({"error": "not found or expired"}, status_code=404)

    image_path = Path(entry["path"])
    if not image_path.is_file():
        return JSONResponse({"error": "file gone"}, status_code=404)

    ext = image_path.suffix.lower()
    return FileResponse(
        image_path,
        media_type=_MIME_TYPES.get(ext, "application/octet-stream"),
        filename=image_path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=600", "X-Content-Type-Options": "nosniff"},
    )
