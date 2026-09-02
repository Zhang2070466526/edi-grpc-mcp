r"""EDA 启动工具 — 启动 EDI 客户端并等待 gRPC 服务就绪。

如果 EDI 已在运行则跳过启动，避免重复进程。
"""

from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from servers.eda.config import EDA_GRPC_SERVER, EDI_PATH
from servers import mcp


@mcp.tool()
def launch_edi(
    edi_path: str = "",
    wait_for_grpc: bool = True,
    wait_timeout: int = 30,
) -> dict[str, Any]:
    """启动 EDI 客户端并等待 gRPC 就绪。已运行时跳过启动。

    用法："启动 EDI"、"打开 EDI 客户端"

    Args:
        edi_path: EDI.exe 路径，默认使用配置的 EDI_PATH。
        wait_for_grpc: 是否等待 gRPC 服务端口就绪，默认 True。
        wait_timeout: 等待 gRPC 就绪的超时秒数，默认 30 秒。

    Returns:
        {"process_started": True, "grpc_ready": True, "success": True,
         "message": "EDI 已在运行（gRPC 127.0.0.1:50055 已就绪）"}
    """
    exe = edi_path or EDI_PATH
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        raise FileNotFoundError(f"EDI.exe 不存在: {exe_path}")

    try:
        host, port_str = EDA_GRPC_SERVER.rsplit(":", 1)
        port = int(port_str)
    except ValueError:
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
        while time.monotonic() - started < wait_timeout:
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
