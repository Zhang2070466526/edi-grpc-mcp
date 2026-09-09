"""多模态视觉工具包 — 图片显示、视觉分析、文档访问。

image_display.py  图片显示
    show_image           读取本地图片返回 MCP ImageContent（不调模型）
    register_image_url   生成临时 HTTP Token（供 Chat 前端渲染）
vision_analyzer.py  视觉分析
    analyze_image        调用视觉模型分析图片内容（会上传第三方）
document.py  文档访问
    open_document        打开本地文档（link 链接 / local 系统打开）
    register_document_url 注册文档 Token 返回预览链接
validators.py  共享校验（路径/扩展名/Pillow）
"""

from servers.multimodal_vision.image_display import show_image, register_image_url, serve_image
from servers.multimodal_vision.vision_analyzer import analyze_image
from servers.multimodal_vision.document import open_document, serve_document, register_document_url

__all__ = [
    "show_image", "analyze_image",
    "open_document",
    "register_image_url", "serve_image", "serve_document",
]
