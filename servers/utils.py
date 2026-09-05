"""公共工具层 — 文件/参数校验、统一响应构建、运行时地址、产物与文件链接。

全项目复用的基础函数，无外部依赖。
"""

from __future__ import annotations

import math
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


# ── 文件 / 路径校验 ──

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


# ── 参数校验 ──

def require_nonempty(value: str, *, error_code: str = "INVALID_PARAMETERS",
                     label: str = "") -> tuple[str, dict[str, Any] | None]:
    """校验字符串非空，返回 (stripped_value, error)。为空时返回统一错误字典。"""
    s = (value or "").strip()
    if not s:
        return "", error_response(error_code, f"{label or '参数'} 不能为空")
    return s, None


def require_position(position: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """校验并规范化场景坐标对象，返回 (position, 错误)。

    position 必须是 {"x": .., "y": ..} 形式的对象，x/y 为有限数值（拒绝 bool、
    字符串、NaN/Inf）。返回只含 x/y 的字典；非法时返回统一错误字典。
    """
    if not isinstance(position, dict):
        return None, error_response("INVALID_PARAMETERS", "position 必须是对象 {x, y}")
    x, y = position.get("x"), position.get("y")
    if (not isinstance(x, (int, float)) or isinstance(x, bool)
            or not isinstance(y, (int, float)) or isinstance(y, bool)):
        return None, error_response("INVALID_PARAMETERS", "position 的 x、y 必须是有限数值")
    if not (math.isfinite(x) and math.isfinite(y)):
        return None, error_response("INVALID_PARAMETERS", "position 的 x、y 必须是有限数值")
    return {"x": x, "y": y}, None


# ── 统一响应构建 ──

def error_response(code: str, message: str, retryable: bool = False, **extra) -> dict[str, Any]:
    """构建工具统一错误响应。"""
    result: dict[str, Any] = {"success": False, "error_code": code, "message": message}
    if retryable:
        result["retryable"] = True
    if extra:
        result.setdefault("details", {}).update(extra)
    return result


def submitted_response(task_id: str, *, status: str = "QUEUED",
                       message: str = "任务已提交", **extra: Any) -> dict[str, Any]:
    """构建异步任务提交成功响应（统一 task_id/status/message 结构）。"""
    return {"success": True, "task_id": task_id, "status": status,
            "message": message, **extra}


def queue_full_response(code: str = "QUEUE_FULL", retryable: bool = True,
                       message: str = "当前已有任务在进行，请稍后重试") -> dict[str, Any]:
    """构建异步任务队列满响应（统一走 error_response），message 可覆盖默认文案。"""
    return error_response(code, message, retryable=retryable)


# ── 运行时服务器地址 ──

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


# ── 产物与文件链接 ──

def build_artifact(type_: str, path: str, generated_by: str) -> dict[str, Any]:
    """构建统一产物条目（用于 artifacts 数组）。"""
    return {"type": type_, "path": path, "name": Path(path).name,
            "generated_by": generated_by}


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
