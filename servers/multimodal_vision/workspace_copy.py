"""工作区图片复制 — 将图片复制到 OpenClaw 工作区 media/edi/mcp-cache/。

条件注册：只有 OPENCLAW_WORKSPACE 有效时才注册为 MCP 工具。
自动检测：edi-mcp 同级 rfclaw/.../workspace → ~/.openclaw/workspace。
缓存策略：24h TTL + MD5 去重，避免重复复制。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sys as _sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from servers import mcp
from servers.settings import get_settings
from servers.multimodal_vision.validators import validate_image_path, IMAGE_MIME_MAP

load_dotenv()
_logger = logging.getLogger("multimodal.workspace")

_MAX_WORKSPACE_IMAGE_SIZE = 40 * 1024 * 1024  # 40 MB
_OPENCLAW_CACHE_TTL = 24 * 60 * 60
_ALLOWED_CACHE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

_MIME_MAP = IMAGE_MIME_MAP


# ═══════════════════════════════════════════════════════════
# 工作区检测
# ═══════════════════════════════════════════════════════════

def _get_openclaw_workspace() -> Path | None:
    """检测 OpenClaw 工作区：.env 配置 → 同级 rfclaw → ~/.openclaw/workspace。"""
    # 1. 优先 .env 配置
    value = get_settings().openclaw_workspace
    if value:
        try:
            w = Path(value).expanduser().resolve()
            if w.is_dir():
                return w
            _logger.warning("OPENCLAW_WORKSPACE 配置的路径无效: %s", value)
        except OSError:
            pass

    # 2. 自动检测：edi-mcp/ 同级 rfclaw/openclaw-service/state/workspace
    if getattr(_sys, "frozen", False):
        _app_root = Path(_sys.executable).parent.resolve()
    else:
        _app_root = Path(__file__).resolve().parent.parent.parent  # multimodal_vision/ → servers/ → 项目根

    candidates = [
        _app_root.parent / "rfclaw" / "openclaw-service" / "state" / "workspace",
        Path.home() / ".openclaw" / "workspace",
    ]
    for p in candidates:
        try:
            if p.is_dir():
                _logger.info("OPENCLAW_WORKSPACE auto-detected: %s", p)
                return p
        except OSError:
            continue

    return None


OPENCLAW_WORKSPACE_PATH = _get_openclaw_workspace()

if OPENCLAW_WORKSPACE_PATH is not None:
    _logger.info("copy_image_to_workspace enabled")
else:
    _logger.info("OPENCLAW_WORKSPACE not configured; copy_image_to_workspace disabled")


def _copy_to_workspace(source: Path, workspace: Path) -> str:
    """复制图片到工作区 mcp-cache 目录，返回目标路径；超限返回空串。"""
    if source.stat().st_size > _MAX_WORKSPACE_IMAGE_SIZE:
        return ""

    media_dir = workspace / "media" / "edi" / "mcp-cache"
    media_dir.mkdir(parents=True, exist_ok=True)

    # 清理过期缓存
    expire_before = time.time() - _OPENCLAW_CACHE_TTL
    try:
        for item in media_dir.iterdir():
            try:
                if item.is_file() and item.suffix.lower() in _ALLOWED_CACHE_EXTENSIONS \
                        and item.stat().st_mtime < expire_before:
                    item.unlink()
            except OSError:
                continue
    except OSError:
        pass

    path_hash = hashlib.md5(str(source.resolve()).encode()).hexdigest()[:8]
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in source.stem)
    target = media_dir / f"{safe_name}_{path_hash}{source.suffix}"
    shutil.copyfile(source, target)
    return str(target)


def copy_image_to_workspace(image_path: str) -> dict[str, Any]:
    """复制图片到 OpenClaw 工作区 media/edi，返回绝对路径供结构化附件发送。

    仅当用户明确要求复制到 OpenClaw 工作区时才调用。
    不得猜测默认工作区，不得在 show_image 成功后自动调用。

    Args:
        image_path: 图片文件绝对路径。
    """
    path = validate_image_path(image_path)

    workspace = OPENCLAW_WORKSPACE_PATH
    if workspace is None:
        return {
            "success": True, "copied": False,
            "status": "WORKSPACE_NOT_CONFIGURED", "retryable": False,
            "source_path": str(path),
            "message": "未找到 OpenClaw 工作区。请在 .env 中配置 OPENCLAW_WORKSPACE，或将工作区放在 edi-mcp 同级目录。",
        }

    size = path.stat().st_size
    if size > _MAX_WORKSPACE_IMAGE_SIZE:
        return {
            "success": True, "copied": False,
            "status": "IMAGE_TOO_LARGE", "retryable": False,
            "source_path": str(path),
            "message": "图片超过工作区复制大小限制（40 MB）。",
        }

    try:
        target = _copy_to_workspace(path, workspace)
    except OSError as exc:
        _logger.warning("Failed to copy to workspace: %s", exc)
        return {
            "success": False, "copied": False,
            "status": "COPY_FAILED", "retryable": False,
            "source_path": str(path),
            "message": "图片复制失败，可直接查看原始文件。",
        }

    if not target:
        return {
            "success": True, "copied": False,
            "status": "IMAGE_TOO_LARGE", "retryable": False,
            "source_path": str(path),
            "message": "图片超过工作区复制大小限制（40 MB）。",
        }

    target_path = Path(target)
    rel = target_path.relative_to(workspace) if str(target_path).startswith(str(workspace)) else target_path
    mime = _MIME_MAP.get(target_path.suffix.lower(), "application/octet-stream")

    return {
        "success": True, "copied": True, "displayed": False,
        "status": "COPIED", "retryable": False,
        "source_path": str(path),
        "workspace_path": str(workspace),
        "image_path": target,
        "media_path": str(rel).replace("\\", "/"),
        "media_type": mime,
        "openclaw_attachment": {"filePath": target},
        "message": "图片已复制到工作区。请使用 OpenClaw 消息工具的 filePath 发送。",
    }
