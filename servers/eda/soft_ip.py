"""EDA 软 IP（Soft IP）工具 — 分类查询 / 按描述下载 AEDT 模型。

软 IP 由 EDI 的 soft-ip-manager 服务提供，gRPC 层只转发请求、不解释业务字段。
两个工具均不需要 project_path，也不要求打开工程。
"""

from __future__ import annotations

import math
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, error_response
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


@mcp.tool()
def download_public_soft_ip_model_by_description(
        category_name: str,
        description: str,
        save_path: str,
        freq: float | None = None,
        bandwidth: float | None = None,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按分类名称和描述查询公共软 IP，补齐可选设计参数后下载 AEDT 模型。

    用法："按描述下载带通滤波器的软 IP 模型到 D:/soft-ip/xxx.aedt"

    category_name 是软 IP 分类名称（完全匹配），description 是描述关键词。
    freq/bandwidth 为可选设计参数，任一未提供时服务端按插值范围中点补齐。
    服务端按 category_name 找分类 → 按 description 查模型取第一个 → 下载。

    Args:
        category_name: 软 IP 分类名称（非空字符串，完全匹配）。
        description: 模型描述关键词（非空字符串）。
        save_path: 目标文件完整路径（如 D:/soft-ip/PCB.aedt）。
        freq: 可选频率（大于 0 的有限数值）；未提供时服务端按插值中点补齐。
        bandwidth: 可选相对带宽（有限数值）；未提供时同上。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details 含 id/model_name/category_id/save_path/freq/bandwidth。
    """
    category_name, err = require_nonempty(category_name, label="category_name")
    if err:
        return err
    description, err = require_nonempty(description, label="description")
    if err:
        return err
    save_path, err = require_nonempty(save_path, label="save_path")
    if err:
        return err
    if freq is not None and (not isinstance(freq, (int, float)) or isinstance(freq, bool)
                             or not math.isfinite(freq) or freq <= 0):
        return error_response("INVALID_PARAMETERS", "freq 必须是大于 0 的有限数值")
    if bandwidth is not None and (not isinstance(bandwidth, (int, float)) or isinstance(bandwidth, bool)
                                  or not math.isfinite(bandwidth)):
        return error_response("INVALID_PARAMETERS", "bandwidth 必须是有限数值")
    payload: dict[str, Any] = {
        "category_name": category_name,
        "description": description,
        "save_path": save_path,
    }
    if freq is not None:
        payload["freq"] = freq
    if bandwidth is not None:
        payload["bandwidth"] = bandwidth
    return call_grpc(
        ecserver_pb2.DOWNLOAD_PUBLIC_SOFT_IP_MODEL_BY_DESCRIPTION,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )
