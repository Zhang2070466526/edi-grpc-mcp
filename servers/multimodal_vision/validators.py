"""图片校验工具 — 路径安全校验 + 扩展名白名单 + Pillow 内容验证。

供 show_image / analyze_image / copy_image_to_workspace 复用。
校验规则：拒绝网络路径、拒绝非白名单扩展名、Pillow 内容有效性检查。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from servers.utils import is_network_path

# 允许的图片扩展名
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 图片扩展名 → MIME 类型（供 image_display / vision_analyzer / workspace_copy 复用）
IMAGE_MIME_MAP: dict[str, str] = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def validate_image_path(image_path: str, allowed: set[str] | None = None) -> Path:
    """校验图片路径和扩展名，通过则返回 resolved Path。

    Args:
        image_path: 图片路径。
        allowed: 允许的扩展名集合（默认使用全部支持格式）。

    Raises:
        PermissionError: 网络路径。
        FileNotFoundError: 文件不存在。
        ValueError: 不支持的扩展名。
    """
    exts = allowed or _ALLOWED_EXTENSIONS
    path = Path(image_path).expanduser().resolve()
    if is_network_path(path):
        raise PermissionError(f"禁止访问网络路径: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {path}")
    if path.suffix.lower() not in exts:
        raise ValueError(f"不支持的图片格式: {path.suffix}")
    return path


def validate_image_content(path: Path) -> None:
    """用 Pillow 验证文件是否为有效图片。

    Raises:
        ValueError: 不是有效图片。
    """
    try:
        with Image.open(path) as img:
            img.verify()
    except Exception as e:
        raise ValueError(f"无法解析为有效图片: {path}") from e
