"""EDA 软 IP（Soft IP）工具 — 分类查询 / 模型查询 / AEDT 模型下载。

软 IP 由 EDI 的 soft-ip-manager 服务提供，gRPC 层只转发请求、不解释业务字段。
四个工具均不需要 project_path，也不要求打开工程。
"""

from __future__ import annotations

import math
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, require_uuid, error_response
from servers import mcp


@mcp.tool()
def search_soft_ip_categories(timeout_seconds: int = 60) -> dict[str, Any]:
    """查询全部软 IP 分类（自动合并分页结果）。

    用法："查一下软 IP 有哪些分类"

    该任务不接收业务参数。服务端每页 15 条请求软 IP 分类接口并自动合并分页，
    返回过滤后的 results 与 count。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（count/results）在 details 中。
    """
    return call_grpc(
        ecserver_pb2.SEARCH_SOFT_IP_CATEGORIES,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )


def _search_soft_ip_models(filters: list, task_type: int, timeout_seconds: int) -> dict[str, Any]:
    """查询软 IP 模型（公共/个人复用，仅 task_type 不同）。"""
    if not isinstance(filters, list):
        return error_response("INVALID_PARAMETERS", "filters 必须是数组")
    return call_grpc(
        task_type,
        {"filters": filters},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def search_public_soft_ip_models(
        filters: list,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """查询公共软 IP 模型（自动合并分页结果）。

    用法："搜一下公共软 IP 里有没有满足条件的"

    filters 为必填数组，内容由软 IP 服务解释，gRPC 层不修改；空数组也允许传入。
    服务端每页 15 条请求并自动合并分页，返回过滤后的 results 与 count。

    Args:
        filters: 过滤条件数组（必填，可为空数组），原样转发给软 IP 服务。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（count/results）在 details 中。
    """
    return _search_soft_ip_models(filters, ecserver_pb2.SEARCH_PUBLIC_SOFT_IP_MODELS, timeout_seconds)


@mcp.tool()
def search_personal_soft_ip_models(
        filters: list,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """查询当前用户的个人软 IP 模型（自动合并分页结果）。

    用法："搜一下我的个人软 IP 模型"

    filters 规则与 search_public_soft_ip_models 相同，查询范围为当前登录用户的个人软 IP。

    Args:
        filters: 过滤条件数组（必填，可为空数组），原样转发给软 IP 服务。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（count/results）在 details 中。
    """
    return _search_soft_ip_models(filters, ecserver_pb2.SEARCH_PERSONAL_SOFT_IP_MODELS, timeout_seconds)


@mcp.tool()
def download_soft_ip_model(
        soft_ip_id: str,
        save_path: str,
        freq: float,
        bandwidth: float | None = None,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按软 IP UUID 和频率下载 AEDT 模型文件到指定路径。

    用法："把这个软 IP 的 AEDT 模型下载到 D:/soft-ip/xxx.aedt"

    soft_ip_id 是软 IP 的 UUID，save_path 是完整目标文件路径（不是目录），freq 是频率（>0）。
    bandwidth 是可选相对带宽（有限数值）：提供时下游请求体额外带上 bandwidth 与 allow_extrapolation=false。
    服务端自动创建父目录并安全覆盖同名文件；下载超时 120 秒。

    Args:
        soft_ip_id: 软 IP UUID。
        save_path: 目标文件完整路径（如 D:/soft-ip/C_10mil4350.aedt）。
        freq: 频率（大于 0 的有限数值）。
        bandwidth: 可选相对带宽（有限数值）；不传则下游请求体只含 freq。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details.save_path 为最终绝对路径。
    """
    soft_ip_id, err = require_uuid(soft_ip_id, label="soft_ip_id")
    if err:
        return err
    save_path, err = require_nonempty(save_path, label="save_path")
    if err:
        return err
    if not isinstance(freq, (int, float)) or isinstance(freq, bool) \
            or not math.isfinite(freq) or freq <= 0:
        return error_response("INVALID_PARAMETERS", "freq 必须是大于 0 的有限数值")
    payload: dict[str, Any] = {"id": soft_ip_id, "save_path": save_path, "freq": freq}
    if bandwidth is not None:
        if not isinstance(bandwidth, (int, float)) or isinstance(bandwidth, bool) \
                or not math.isfinite(bandwidth):
            return error_response("INVALID_PARAMETERS", "bandwidth 必须是有限数值")
        payload["bandwidth"] = bandwidth
    return call_grpc(
        ecserver_pb2.DOWNLOAD_SOFT_IP_MODEL,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )
