"""公共工具层 — 文件校验、统一错误响应、运行时地址管理、file:// 链接生成。

全项目复用的基础函数，无外部依赖。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── 服务启动时间戳 ──
SERVER_STARTED_AT: float = time.time()


def server_uptime_seconds() -> float:
    """返回服务启动以来的运行秒数。"""
    return time.time() - SERVER_STARTED_AT


# ── 文件校验 ──

def validate_file(path: str, extensions: tuple[str, ...] = ()) -> str:
    """校验文件存在，可选限制扩展名，返回规范化绝对路径。"""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {p}")
    if extensions and p.suffix.lower() not in extensions:
        raise ValueError(f"文件扩展名必须是 {extensions}: {p}")
    return str(p.resolve())


def is_network_path(path) -> bool:
    """判断路径是否为网络路径（UNC \\\\ 或 URL //），此类路径无法本地校验。"""
    s = str(path)
    return s.startswith(r"\\") or s.startswith("//")


# ── 统一错误响应 ──

def tool_error(code: str, message: str, retryable: bool = False, **extra) -> dict[str, Any]:
    """构建工具统一错误响应。"""
    result: dict[str, Any] = {"success": False, "error_code": code, "message": message}
    if retryable:
        result["retryable"] = True
    if extra:
        result.setdefault("details", {}).update(extra)
    return result


@dataclass
class ServerAddress:
    """运行时服务器地址（host + port），由 start_servers.py 通过 set_server_address() 设置。"""
    host: str = "127.0.0.1"
    port: int = 50026


_address = ServerAddress()
_lock = threading.Lock()


def set_server_address(host: str, port: int) -> None:
    """在 start_servers.py 确定最终 host/port 后调用。"""
    with _lock:
        _address.host = host
        _address.port = port


def get_server_base_url() -> str:
    """返回当前 HTTP 服务的 base URL，供图片 Token 等功能使用。"""
    with _lock:
        host = _address.host
        port = _address.port
    public_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{public_host}:{port}"


def build_file_link(path: str, label: str = "打开文件") -> dict:
    """为本地文件生成 file:// URI 和 Markdown 链接。

    只在 MCP 服务与客户端同机时可靠。
    """
    p = Path(path).resolve()
    uri = p.as_uri()
    return {
        "file_uri": uri,
        "markdown_link": f"[{label}]({uri})",
    }
