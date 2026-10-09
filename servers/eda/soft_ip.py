"""EDA 软 IP（Soft IP）工具 — 按描述设计芯片并下载 AEDT 模型。

软 IP 由 EDI 的 soft-ip-manager 服务提供，gRPC 层只转发请求、不解释业务字段。
本工具不需要 project_path，也不要求打开工程。
"""

from __future__ import annotations

import math
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, error_response
from servers import mcp


@mcp.tool()
def design_chip(
        description: str,
        save_path: str,
        freq: float | None = None,
        bandwidth: float | None = None,
        timeout_seconds: int = 600,
) -> dict[str, Any]:
    """用户输入指标来设计cst天线模型（中心频率），hfss带通滤波器（中心频率，相对带宽）、低通滤波器（截止频率）负载（中心频率）等电路指标，获取相应的模型文件。

    用法："设计一个中心频率 20GHz、相对带宽 5% 的带通滤波器"

    description 是自然语言设计需求（非空），写清电路类型与设计指标（中心频率、相对带宽、截止频率等）。
    服务端据此生成对应的芯片模型文件，落到 save_path 指定路径。阶数/拓扑/指标由所选模型自身决定，
    本工具不校验指标、不回模型名。描述写不中库 → FAILED「指定分类下未找到该描述的模型」。

    Args:
        description: 模型设计需求（非空字符串），写清电路类型（带通/低通滤波器、天线、负载…）与指标（中心频率、相对带宽、截止频率）。
        save_path: 目标文件完整路径（如 D:/soft-ip/PCB.aedt）。
        freq: 可选中心频率（大于 0 的有限数值，按 GHz 理解）；未提供时服务端按该软 IP 插值范围中点补齐。
        bandwidth: 可选相对带宽，单位 %（如 5 = 5%）；未提供时同上。注意：部分软 IP 该参数必填（不给会 FAILED「缺少相对带宽」）；超出有效范围也会 FAILED。
        timeout_seconds: 最长等待秒数，默认 600。

    remark: 不现场综合设计，也不创建/打开 .epp 工程，存储到save_path中

    Returns:
        gRPC 统一返回结构。**产物路径读 details.save_path**（result_path 恒为空串）；
        details 含 description/freq/bandwidth/save_path/status。
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
        max_timeout_seconds=600,
    )
