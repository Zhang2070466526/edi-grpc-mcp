"""EDA 工作区工具 — 创建 / 切换 / 查询当前工作区。

三个工具均不需要 project_path、不要求打开工程，直接操作当前程序的工作区。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, error_response
from servers import mcp


def _set_workspace(path: str, task_type: int, timeout_seconds: int) -> dict[str, Any]:
    """创建/切换工作区的公共实现（二者仅 task_type 不同 + 路径语义校验）。"""
    path, err = require_nonempty(path, label="path")
    if err:
        return err
    p = Path(path).expanduser()
    if task_type == ecserver_pb2.CREATE_WORKSPACE:
        if p.suffix.lower() == ".epp":
            return error_response("INVALID_PARAMETERS", "工作区路径不能是 .epp 工程文件，请传目录路径")
        if p.is_dir() and any(p.iterdir()):
            return error_response("INVALID_PARAMETERS", f"目标目录已存在且非空: {path}")
    elif task_type == ecserver_pb2.SWITCH_WORKSPACE:
        if not p.is_dir():
            return error_response("INVALID_PARAMETERS", f"工作区目录不存在: {path}")
    return call_grpc(task_type, {"path": path}, timeout_seconds, max_timeout_seconds=300)


@mcp.tool()
def create_workspace(path: str, timeout_seconds: int = 60) -> dict[str, Any]:
    """创建一个新的工作区（不切换过去）。要切换用 switch_workspace，查当前工作区用 get_current_workspace_path。

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
    """⚠️ 立即切换当前工作区，当前正在进行的任务要及时关闭保存，避免丢失。

    用法："把工作区切换到 D:/EDI-Workspace-New"

    Args:
        path: 工作区目录绝对路径（非空字符串）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    return _set_workspace(path, ecserver_pb2.SWITCH_WORKSPACE, timeout_seconds)


@mcp.tool()
def get_current_workspace_path(timeout_seconds: int = 60) -> dict[str, Any]:
    """查询程序当前实际加载的工作区目录。

    用法："当前 EDI 工作区是哪个目录"、"获取当前工作区路径"

    只读查询，不修改任何状态。switch_workspace 立即生效，本工具返回的就是切换后的当前工作区。

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


def _flatten_models(models: Any) -> list[dict[str, Any]]:
    """把服务端「子类型 → [{uuid, name}]」的模型字典摊平成 [{sub_type, uuid, name}]。

    服务端只返回 config.ini 的 sub_types 里能匹配到名称的性能模型，因此这里不做过滤；
    结构不符（非 dict / 值非 list）时跳过，不抛错，避免整形把有效结果变成失败。
    """
    flat: list[dict[str, Any]] = []
    if not isinstance(models, dict):
        return flat
    for sub_type, items in models.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            flat.append({
                "sub_type": sub_type,
                "uuid": item.get("uuid"),
                "name": item.get("name"),
            })
    return flat


@mcp.tool()
def get_current_workspace_summary(timeout_seconds: int = 60) -> dict[str, Any]:
    """获取当前工作区概要：工程列表与本地性能模型（uuid / 子类型 / 型号）。

    用法："当前工作区有哪些工程"、"列出工作区里的性能模型"、"这个工作区里都放了什么"

    不需要 project_path，也不需要打开工程。只读查询，不修改任何状态，但需要 EDI 在线（走 gRPC）。
    返回的 models 以子类型名称为键；另有 models_flat 把「子类型 + uuid + 型号名称」摊平成列表，
    逐个取模型信息时用它更省事。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段在 details 中：
        {"details": {"workspace_path": "D:/EDI/data/EDI-Workspace",
                     "epp_files": ["D:/.../a.epp"],
                     "models": {"<子类型>": [{"uuid": "...", "name": "..."}]},
                     "epp_count": 1,
                     "models_flat": [{"sub_type": "...", "uuid": "...", "name": "..."}],
                     "model_count": 1}}
    """
    result = call_grpc(
        ecserver_pb2.GET_CURRENT_WORKSPACE_SUMMARY,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )
    details = result.get("details") if isinstance(result, dict) else None
    if isinstance(details, dict):
        epp_files = details.get("epp_files")
        flat = _flatten_models(details.get("models"))
        details["epp_count"] = len(epp_files) if isinstance(epp_files, list) else 0
        details["models_flat"] = flat
        details["model_count"] = len(flat)
    return result
