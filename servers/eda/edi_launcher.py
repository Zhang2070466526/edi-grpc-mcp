r"""EDA 启动工具 — 启动 EDI 客户端并等待 gRPC 服务就绪。

如果 EDI 已在运行则跳过启动，避免重复进程。
"""

from __future__ import annotations

import datetime
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

import grpc

from servers.eda.config import EDA_GRPC_SERVER, EDI_PATH
from servers.settings import get_settings
from servers.eda.grpc_client import get_cached_channel, is_queue_busy, MAX_RECEIVE_MB
from servers.utils import error_response
from servers import mcp


@mcp.tool()
def launch_edi(
    edi_path: str = "",
    wait_for_grpc: bool = True,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """启动 EDI 客户端并等待 gRPC 就绪。已运行时跳过启动。

    用法："启动 EDI"、"打开 EDI 客户端"

    Args:
        edi_path: EDI.exe 路径，默认使用配置的 EDI_PATH。
        wait_for_grpc: 是否等待 gRPC 服务端口就绪，默认 True。
        timeout_seconds: 等待 gRPC 就绪的超时秒数，默认 30 秒。

    Returns:
        {"process_started": True, "grpc_ready": True, "success": True,
         "message": "EDI 已在运行（gRPC 127.0.0.1:50055 已就绪）"}
    """
    exe = edi_path or EDI_PATH
    if not exe:
        return error_response("EDI_NOT_FOUND", "未检测到 EDI.exe 路径，请设置 EDI_PATH 或将其放在项目同级目录")
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        raise FileNotFoundError(f"EDI.exe 不存在: {exe_path}")

    try:
        server = EDA_GRPC_SERVER.strip()
        if server.startswith("["):  # IPv6 带方括号：[::1]:50055
            host, _, port_str = server[1:].partition("]:")
            port = int(port_str)
        else:
            host, _, port_str = server.rpartition(":")
            port = int(port_str)
    except (ValueError, TypeError):
        raise ValueError(f"EDA_GRPC_SERVER 配置无效（需要 host:port）: {EDA_GRPC_SERVER}") from None
    already_running = False
    try:
        with socket.create_connection((host, port), timeout=1):
            already_running = True
    except OSError:
        pass

    if already_running:
        return {
            "process_started": True,
            "grpc_ready": True,
            "success": True,
            "message": f"EDI 已在运行（gRPC {EDA_GRPC_SERVER} 已就绪）",
            "edi_path": str(exe_path),
            "grpc_server": EDA_GRPC_SERVER,
        }

    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(exe_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RuntimeError(f"无法启动 EDI.exe: {exc}") from exc

    result: dict[str, Any] = {
        "process_started": True,
        "grpc_ready": False,
        "success": True,
        "message": "EDI 已启动",
        "edi_path": str(exe_path),
        "grpc_server": EDA_GRPC_SERVER,
    }

    if wait_for_grpc:
        started = time.monotonic()
        while time.monotonic() - started < timeout_seconds:
            try:
                with socket.create_connection((host, port), timeout=1):
                    result["grpc_ready"] = True
                    result["success"] = True
                    result["message"] += (
                        f"，gRPC 服务已就绪（{time.monotonic() - started:.1f}s）"
                    )
                    return result
            except OSError:
                time.sleep(1)

        result["success"] = False
        result["message"] += "，gRPC 服务未在规定时间内就绪"

    return result


@mcp.tool()
def get_service_logs(
    lines: int = 50,
    keyword: str = "",
    level: str = "",
) -> dict[str, Any]:
    """读取 EDI 服务端日志，检查运行异常（含 ERROR/WARN/异常堆栈分析）。

    用法："看看 EDI 服务有没有报错"、"查一下 EDI 最近的日志"

    读取 EDI 软件目录下 logs/eda_YYYY-MM-DD.log（当天），支持按关键词/级别过滤，
    并统计 ERROR / WARN / 异常堆栈，便于快速判断服务是否异常。

    Args:
        lines: 返回最后 N 行（默认 50，范围 1-500）。
        keyword: 关键词过滤（空则不过滤，大小写不敏感）。
        level: 日志级别过滤（DEBUG / INFO / WARN / ERROR，空则不过滤）。

    Returns:
        {"success": True, "log_file": "...", "total_lines": 123, "lines": [...],
         "matched_lines": 42, "error_count": 5, "warning_count": 8,
         "exception_lines": [...], "message": "..."}
    """
    try:
        lines = max(1, min(int(lines), 500))
    except (TypeError, ValueError):
        return error_response("INVALID_PARAMETERS", "lines 必须是整数（1-500）")
    level = (level or "").strip().upper()
    keyword = (keyword or "").strip()

    log_dir = Path(get_settings().edi_log_dir).expanduser()
    today_log = log_dir / f"eda_{datetime.date.today():%Y-%m-%d}.log"

    # 每次调用都动态检测：优先读今天的日志；当天不存在则回退到最新的一份
    fallback_note = ""
    if today_log.is_file():
        log_file = today_log
    else:
        candidates = sorted(
            log_dir.glob("eda_*.log"),
            key=lambda p: (p.stat().st_mtime, p.name),
            reverse=True,
        )
        if not candidates:
            return error_response("LOG_NOT_FOUND", f"日志目录下没有日志文件: {log_dir}")
        log_file = candidates[0]
        fallback_note = f"当天日志不存在，已读取最近日志 {log_file.name}"

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
    except OSError as exc:
        return error_response("LOG_READ_ERROR", f"读取日志失败: {exc}")

    total = len(all_lines)

    def _line_level(line: str) -> str:
        up = line.upper()
        if "TRACEBACK" in up or "EXCEPTION" in up or "ERROR" in up or "FAIL" in up:
            return "ERROR"
        if "WARN" in up:
            return "WARN"
        if "INFO" in up:
            return "INFO"
        if "DEBUG" in up:
            return "DEBUG"
        return ""

    error_count = 0
    warning_count = 0
    exception_lines: list[str] = []
    matched: list[str] = []

    for ln in all_lines:
        lvl = _line_level(ln)
        if lvl == "ERROR":
            error_count += 1
        elif lvl == "WARN":
            warning_count += 1
        if "EXCEPTION" in ln.upper() or "TRACEBACK" in ln.upper():
            exception_lines.append(ln)

        if level and lvl != level:
            continue
        if keyword and keyword.lower() not in ln.lower():
            continue
        matched.append(ln)

    if error_count or warning_count:
        message = (f"日志共 {total} 行，发现 {error_count} 个错误、"
                   f"{warning_count} 个警告。")
    else:
        message = f"日志共 {total} 行，未发现明显错误。"
    if fallback_note:
        message += " " + fallback_note

    return {
        "success": True,
        "log_file": str(log_file),
        "total_lines": total,
        "matched_lines": len(matched),
        "lines": matched[-lines:],
        "error_count": error_count,
        "warning_count": warning_count,
        "exception_lines": exception_lines[-20:],
        "message": message,
    }


@mcp.tool()
def get_service_status() -> dict[str, Any]:
    """返回 EDI gRPC 通道状态和队列占用信息（只读，不占执行槽位），用于诊断：通道是否健康、是否有任务在排队。

    用法："EDI 服务正常吗"、"检查 gRPC 连接状态"、"有没有任务在排队"

    Returns:
        {"grpc_target": "127.0.0.1:50055", "channel_state": "ready/unhealthy/unknown",
         "channel_cached": True, "queue_locked": False, "max_receive_mb": 256}
    """
    target = EDA_GRPC_SERVER
    ch = get_cached_channel(target)
    state = "unknown"
    if ch is not None:
        try:
            grpc.channel_ready_future(ch).result(timeout=1)
            state = "ready"
        except (grpc.FutureTimeoutError, grpc.RpcError):
            state = "unhealthy"
    return {
        "grpc_target": target,
        "channel_state": state,
        "channel_cached": ch is not None,
        "queue_locked": is_queue_busy(),
        "max_receive_mb": MAX_RECEIVE_MB,
    }
