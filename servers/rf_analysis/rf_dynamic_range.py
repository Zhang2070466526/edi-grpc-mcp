"""射频链路灵敏度与动态范围计算 —— C++ rfdynamicrangecalculator.cpp 的纯 Python 重写。

只重写核心算法（bandwidthToHz / calculate / evaluateTarget）。
公式见「射频链路灵敏度与动态范围计算说明.md」。
"""
from __future__ import annotations

import math
from typing import Any

from servers import mcp
from servers.utils import error_response

_THERMAL_NOISE_DBM_PER_HZ = -174.0

_UNIT_FACTORS = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def bandwidth_to_hz(value: float, unit: str) -> float:
    """带宽数值 + 单位 → Hz。"""
    u = (unit or "hz").lower()
    if u not in _UNIT_FACTORS:
        raise ValueError(f"未知带宽单位: {unit}")
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError("带宽必须大于 0")
    return value * _UNIT_FACTORS[u]


def _calc(
    noise_figure_db: float,
    input_p1dbm: float,
    bandwidth: float,
    bandwidth_unit: str = "mhz",
    required_snr_db: float = 0.0,
    target_dynamic_range_db: float | None = None,
) -> dict:
    """计算链路噪声底、最低输入灵敏度、压缩受限动态范围；给 target 时顺带做目标评价。

    Args:
        noise_figure_db: 链路等效总噪声系数（dB，≥0）。
        input_p1dbm: 输入 1dB 压缩点（dBm，可为负）。
        bandwidth: 等效噪声带宽数值。
        bandwidth_unit: 带宽单位 hz/khz/mhz/ghz，默认 mhz。
        required_snr_db: 最低所需信噪比（dB，可为负）。
        target_dynamic_range_db: 可选，目标动态范围（dB），给了就输出 meets_target/margin/required_sensitivity。

    Returns:
        {"noise_floor_dbm", "sensitivity_dbm", "dynamic_range_db",
         [目标评价时附] "meets_target", "margin_db", "required_sensitivity_dbm"}
    """
    for name, v in (("noise_figure_db", noise_figure_db),
                    ("input_p1dbm", input_p1dbm),
                    ("required_snr_db", required_snr_db)):
        if not math.isfinite(v):
            raise ValueError(f"{name} 必须是有限数")
    if noise_figure_db < 0.0:
        raise ValueError("噪声系数必须非负")

    bandwidth_hz = bandwidth_to_hz(bandwidth, bandwidth_unit)

    noise_floor_dbm = (_THERMAL_NOISE_DBM_PER_HZ
                       + 10.0 * math.log10(bandwidth_hz)
                       + noise_figure_db)
    sensitivity_dbm = noise_floor_dbm + required_snr_db
    dynamic_range_db = input_p1dbm - sensitivity_dbm

    result = {
        "noise_floor_dbm": noise_floor_dbm,
        "sensitivity_dbm": sensitivity_dbm,
        "dynamic_range_db": dynamic_range_db,
    }

    if target_dynamic_range_db is not None:
        if not math.isfinite(target_dynamic_range_db):
            raise ValueError("target_dynamic_range_db 必须是有限数")
        margin_db = dynamic_range_db - target_dynamic_range_db
        result["meets_target"] = margin_db >= 0.0
        result["margin_db"] = margin_db
        result["required_sensitivity_dbm"] = input_p1dbm - target_dynamic_range_db
    return result


@mcp.tool()
def calculate_rf_dynamic_range(
    noise_figure_db: float,
    input_p1dbm: float,
    bandwidth: float,
    bandwidth_unit: str = "mhz",
    required_snr_db: float = 0.0,
    target_dynamic_range_db: float | None = None,
) -> dict[str, Any]:
    """计算射频链路的噪声底、最低输入灵敏度与压缩受限动态范围。

    用法："算一下 NF=3dB、P1dB=-10dBm、1MHz 带宽、要求 10dB 信噪比这条链路的灵敏度"

    公式：噪声底 = -174 + 10·log10(BW) + NF；灵敏度 = 噪声底 + 所需 SNR；
    动态范围 = P1dB − 灵敏度。给 target 时顺带算是否达标与所需灵敏度界限。

    Args:
        noise_figure_db: 链路等效总噪声系数（dB，≥0）。
        input_p1dbm: 输入 1dB 压缩点（dBm，可为负）。
        bandwidth: 等效噪声带宽数值（>0）。
        bandwidth_unit: 带宽单位 hz/khz/mhz/ghz，默认 mhz。
        required_snr_db: 最低所需信噪比（dB，可为负），默认 0。
        target_dynamic_range_db: 目标动态范围（dB，可选），给了就输出 meets_target/margin_db/required_sensitivity_dbm。

    Returns:
        {"success": true, "noise_floor_dbm": ..., "sensitivity_dbm": ...,
         "dynamic_range_db": ..., ["meets_target", "margin_db", "required_sensitivity_dbm"]}
    """
    try:
        result = _calc(noise_figure_db, input_p1dbm, bandwidth, bandwidth_unit,
                       required_snr_db, target_dynamic_range_db)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    result["success"] = True
    return result