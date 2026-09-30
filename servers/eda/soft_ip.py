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
def design_soft_ip(
        description: str,
        save_path: str,
        freq: float | None = None,
        bandwidth: float | None = None,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按设计描述获取公共软 IP 设计模型，补齐可选设计参数后下载 AEDT 模型。

    用法："帮我设计一个 9 阶带通滤波器的软 IP 模型到 D:/soft-ip/xxx.aedt"

    description 是模型设计需求（非空），服务端走智能检索（smart-search）取第一个候选模型。
    freq/bandwidth 为可选设计参数，任一未提供时服务端按插值范围中点补齐。
    下载时复用 DOWNLOAD_SOFT_IP_MODEL，存在 bandwidth 时自动带 allow_extrapolation=false。

    Args:
        description: 模型设计需求（非空字符串）。
        save_path: 目标文件完整路径（如 D:/soft-ip/PCB.aedt）。
        freq: 可选频率（大于 0 的有限数值）；未提供时服务端按插值中点补齐。
        bandwidth: 可选相对带宽（有限数值）；未提供时同上。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details 含 save_path/freq（及存在时的 bandwidth/单位字段）。
    """
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
        "description": description,
        "save_path": save_path,
    }
    if freq is not None:
        payload["freq"] = freq
    if bandwidth is not None:
        payload["bandwidth"] = bandwidth
    return call_grpc(
        ecserver_pb2.DESIGN_SOFT_IP,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )
