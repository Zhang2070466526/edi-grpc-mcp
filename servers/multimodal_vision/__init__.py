"""多模态视觉工具包 — 图片显示、工作区复制、视觉分析、文档访问。

image_display.py     show_image + HTTP 路由     （不调模型）
workspace_copy.py    copy_image_to_workspace    （不调模型）
vision_analyzer.py   analyze_image              （调用视觉模型）
document.py          open_document（link / local 两种模式）
validators.py        共享校验                   （路径/扩展名/Pillow）
"""

from servers.multimodal_vision.image_display import show_image, register_image_url, serve_image
from servers.multimodal_vision.workspace_copy import copy_image_to_workspace, OPENCLAW_WORKSPACE_PATH
from servers.multimodal_vision.vision_analyzer import analyze_image
from servers.multimodal_vision.document import open_document, serve_document, register_document_url

# 条件注册 copy_image_to_workspace（当前已隐藏：OPENCLAW_WORKSPACE_PATH 恒为 None，故不注册）
from servers import mcp
if OPENCLAW_WORKSPACE_PATH is not None:
    mcp.tool()(copy_image_to_workspace)

__all__ = [
    "show_image", "copy_image_to_workspace", "analyze_image",
    "open_document",
    "register_image_url", "serve_image", "serve_document", "OPENCLAW_WORKSPACE_PATH",
]
