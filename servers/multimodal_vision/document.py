"""文档与产物访问工具 — HTTP 临时链接 + 系统默认程序打开。

open_document         — 打开本地文档：link 模式生成 10 分钟 HTTP 链接，local 模式系统默认程序打开
register_document_url — 供 report 等模块注册文档 Token 并返回预览链接
fetch_artifact        — 把任意产物文件（.snp/.raw/图/报告）注册成可下载 URL（远程取产物）
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from servers import mcp
from servers.token_registry import TokenStore
from servers.utils import is_network_path, error_response
from servers.multimodal_vision.validators import validate_local_file

load_dotenv()

# 支持的文档格式（link 和 local 两种模式共用）
_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".ppt", ".pptx", ".txt", ".csv", ".rtf",
}
_doc_store = TokenStore(route="/documents")

# 仅 disposition 覆盖（业务语义）；MIME 交给 mimetypes 推断，覆盖全部 _ALLOWED_EXTENSIONS。
# 显式补 openxml 三类：mimetypes 在部分平台不识别，会误落 octet-stream。
for _ext, _mime in {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}.items():
    mimetypes.add_type(_mime, _ext)

_DISPOSITION: dict[str, str] = {".pdf": "inline", ".docx": "attachment"}


def _mime_and_disposition(ext: str) -> tuple[str, str]:
    """返回 (mime, disposition)：mime 由 mimetypes 推断，disposition 仅当调用方未显式指定时按覆盖表兜底。"""
    mime, _ = mimetypes.guess_type("f" + ext)
    return mime or "application/octet-stream", _DISPOSITION.get(ext, "attachment")


# ═══════════════════════════════════════════════════════════
# 校验（共用）
# ═══════════════════════════════════════════════════════════

def _validate_path(file_path: str, allowed: set[str]) -> Path:
    """校验文档路径（绝对、拒绝网络路径、存在、扩展名白名单）。"""
    raw = Path(file_path).expanduser()
    if not raw.is_absolute():
        raise ValueError("file_path 必须是绝对路径")
    return validate_local_file(raw.resolve(), allowed, "文件")


# ═══════════════════════════════════════════════════════════
# Token 管理
# ═══════════════════════════════════════════════════════════

def register_document_url(file_path: str, disposition: str = "inline") -> str:
    """为本地文档注册临时 HTTP 访问 Token，返回可访问的 URL。

    供其他模块（如报告生成器）在生成文档后直接返回预览链接。
    Token 10 分钟后过期，仅本机 127.0.0.1 可访问。
    """
    # 复用 open_document 的路径校验，防止未来调用方传入任意本地路径被注册为可访问 token
    path = _validate_path(file_path, _ALLOWED_EXTENSIONS)
    _, url = _doc_store.register(str(path), disposition=disposition)
    return url


def _register_any_file_url(file_path: str, ttl: int = 3600, disposition: str = "inline") -> str:
    """不校验扩展名地把本地文件注册成 token 链接，返回 URL。调用方负责确认文件存在。"""
    _, url = _doc_store.register(file_path, disposition=disposition, ttl=ttl)
    return url


# ═══════════════════════════════════════════════════════════
# open_document — 打开本地文档（link / local 两种模式）
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def open_document(
    file_path: str,
    mode: str = "link",
    disposition: str = "inline",
) -> dict[str, Any]:
    """打开本地文档：link 模式生成 10 分钟 HTTP 链接，local 模式用系统默认程序打开。

    用法："帮我打开这个 PDF"、"用 Word 打开这个报告"

    link 模式只生成链接、不自动打开浏览器；local 模式用 os.startfile 系统打开。
    仅当用户明确要求时才调用，生成报告后不得自动打开。

    Args:
        file_path: 本地文档绝对路径（支持 10 种格式）。
        mode: "link"（生成 HTTP 链接，默认）或 "local"（系统默认程序打开）。
        disposition: link 模式下，inline（预览）或 attachment（下载）。

    Returns:
        link 模式：{"success": True, "url": "http://...", "markdown_link": "...", "expires_in": 600}
        local 模式：{"success": True, "status": "OPEN_REQUESTED", "file_path": "..."}
    """
    try:
        path = _validate_path(file_path, _ALLOWED_EXTENSIONS)
    except PermissionError as e:
        return error_response("INVALID_PATH", str(e))
    except FileNotFoundError as e:
        return error_response("FILE_NOT_FOUND", str(e))
    except ValueError as e:
        return error_response("UNSUPPORTED_FORMAT", str(e))

    if mode not in ("link", "local"):
        mode = "link"

    # local 模式：系统默认程序打开
    if mode == "local":
        try:
            os.startfile(str(path))
        except OSError as exc:
            return error_response("DEFAULT_APPLICATION_UNAVAILABLE",
                              f"系统没有可用于打开该文件的默认程序: {exc}")
        return {
            "success": True,
            "status": "OPEN_REQUESTED",
            "file_path": str(path),
            "file_type": path.suffix.lower(),
            "message": "已请求使用系统默认程序打开文件",
        }

    # link 模式：生成临时 HTTP 链接
    if disposition not in ("inline", "attachment"):
        disposition = "inline"

    ext = path.suffix.lower()
    mime, _ = _mime_and_disposition(ext)
    _, url = _doc_store.register(str(path), disposition=disposition)

    return {
        "success": True,
        "file_name": path.name,
        "mime_type": mime,
        "url": url,
        "expires_in": 600,
        "display_mode": disposition,
        "markdown_link": f"[{path.name}]({url})",
    }


def _sha256_file(path: Path) -> str:
    """分块计算文件 sha256。不用 hashlib.file_digest（3.11+ API），
    手写分块循环以兼容 3.10，也避免大文件被 read_bytes 全量读进内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@mcp.tool()
def fetch_artifact(file_path: str, ttl_seconds: int = 600) -> dict[str, Any]:
    """把本机产物文件注册成可下载 URL（仿真结果 / SNP / RAW / 图 / 报告 / 任意文件）。

    用法："把仿真结果 xxx.snp 下载给我"

    与 open_document 不同：不校验扩展名，只要文件存在就能取。远程场景下主机用
    返回的 url 直接下载；sha256 供下载后校验字节一致。

    Args:
        file_path: 产物文件绝对路径（任意扩展名）。
        ttl_seconds: 链接有效秒数，默认 600（钳制到 30-3600）。

    Returns:
        {"success": True, "url": "...", "file_name": "...",
         "size_bytes": N, "sha256": "...", "expires_in": N}
    """
    p = Path(file_path).expanduser().resolve()
    if not p.is_file():
        return error_response("FILE_NOT_FOUND", f"文件不存在: {p}")
    ttl = max(30, min(ttl_seconds, 3600))
    _, url = _doc_store.register(str(p), disposition="attachment", ttl=ttl)
    return {
        "success": True,
        "url": url,
        "file_name": p.name,
        "size_bytes": p.stat().st_size,
        "sha256": _sha256_file(p),
        "expires_in": ttl,
    }


# ═══════════════════════════════════════════════════════════
# HTTP 路由
# ═══════════════════════════════════════════════════════════

async def serve_document(request: Request) -> FileResponse | JSONResponse:
    """GET /documents/{token} — 根据 Token 返回文档文件，10 分钟过期。"""
    token = request.path_params.get("token", "")
    entry = _doc_store.lookup(token)
    if entry is None:
        return JSONResponse({"error": "not found or expired"}, status_code=404)

    path = Path(entry["path"])
    if not path.is_file():
        return JSONResponse({"error": "file gone"}, status_code=404)

    ext = path.suffix.lower()
    mime, default_disp = _mime_and_disposition(ext)
    disp = entry.get("disposition", default_disp)

    return FileResponse(
        path,
        media_type=mime,
        filename=path.name,
        content_disposition_type=disp,
        headers={
            "Cache-Control": "private, max-age=600",
            "X-Content-Type-Options": "nosniff",
        },
    )
