"""EDA 工作区工具 — 创建 / 切换 / 查询当前工作区。

三个工具均不需要 project_path、不要求打开工程，直接操作当前程序的工作区。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty
from servers import mcp


def _set_workspace(path: str, task_type: int, timeout_seconds: int) -> dict[str, Any]:
    """创建/切换工作区的公共实现（二者仅 task_type 不同）。"""
    path, err = require_nonempty(path, label="path")
    if err:
        return err
    return call_grpc(task_type, {"path": path}, timeout_seconds, max_timeout_seconds=300)


@mcp.tool()
def create_workspace(path: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """创建一个新的工作区，不自动切换。

    用法："创建一个工作区 D:/EDI-Workspace-New"

    直接调用 GrpcApiManager::createWorkspace。路径无效、已有合法工作区或目标目录
    非空时失败；创建成功不会自动切换工作区。

    Args:
        path: 工作区目录绝对路径（非空字符串，不是 .epp 文件）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    return _set_workspace(path, ecserver_pb2.CREATE_WORKSPACE, timeout_seconds)


@mcp.tool()
def switch_workspace(path: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """设置下次启动程序时使用的工作区，当前工作区保持不变。

    用法："把工作区切换到 D:/EDI-Workspace-New"

    仅保存最近使用的工作区路径，下次启动程序时生效，不关闭工程、不自动重启、
    不弹出窗口。目标必须是已存在且兼容的工作区。

    Args:
        path: 工作区目录绝对路径（非空字符串）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    return _set_workspace(path, ecserver_pb2.SWITCH_WORKSPACE, timeout_seconds)


@mcp.tool()
def get_current_workspace(timeout_seconds: int = 60) -> dict[str, Any]:
    """查询程序当前实际加载的工作区目录。

    用法："当前 EDI 工作区是哪个目录"、"获取当前工作区路径"

    只读查询，不修改任何状态。如果此前调用了 switch_workspace 但尚未重启程序，
    本工具仍返回当前工作区，而不是下次启动将使用的路径。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，成功时 details 含 workspace_path。
    """
    return call_grpc(
        ecserver_pb2.GET_CURRENT_WORKSPACE,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )
