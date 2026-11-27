"""射频链路级联计算 —— 多级增益 / 噪声系数（Friis）/ 输出 P1dB / 输出 IP3。

纯 Python 实现，公式见链路预算口径：
  - 噪声级联（Friis）：F_total = 1 + Σ(F_i − 1) / G_{i−1}（线性值）
  - 线性度级联（输入参考）：1/P_total = Σ G_{i−1} / P_i
"""
from __future__ import annotations

import math
from typing import Any

from servers import mcp
from servers.utils import error_response


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 10.0)


def _linear_to_db(lin: float) -> float:
    return 10.0 * math.log10(lin)


def _parse_stages(stages: list) -> list[dict]:
    """校验并归一化 stages，返回 [{gain_lin, nf_lin, op1db_dbm, iip3_dbm}]。"""
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages 必须是非空列表")
    parsed: list[dict] = []
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            raise ValueError(f"第 {i + 1} 级必须是对象 {{gain_db, nf_db, ...}}")
        gain = s.get("gain_db")
        nf = s.get("nf_db")
        if gain is None or nf is None:
            raise ValueError(f"第 {i + 1} 级缺少 gain_db / nf_db")
        for name, v in (("gain_db", gain), ("nf_db", nf)):
            if not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"第 {i + 1} 级 {name} 必须是有限数值")
        if nf < 0.0:
            raise ValueError(f"第 {i + 1} 级 nf_db 必须非负")
        op1db = s.get("op1db_dbm")
        iip3 = s.get("iip3_dbm")
        for name, v in (("op1db_dbm", op1db), ("iip3_dbm", iip3)):
            if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)):
                raise ValueError(f"第 {i + 1} 级 {name} 必须是有限数值")
        parsed.append({
            "gain_lin": _db_to_linear(gain),
            "nf_lin": _db_to_linear(nf),
            "op1db_dbm": op1db,
            "iip3_dbm": iip3,
        })
    return parsed


def _cascade(stages: list) -> dict:
    parsed = _parse_stages(stages)

    # 增益 + 噪声系数（Friis）
    f_total = 1.0
    gain_before = 1.0
    per_stage: list[dict] = []
    for i, p in enumerate(parsed):
        f_total += (p["nf_lin"] - 1.0) / gain_before
        gain_before *= p["gain_lin"]
        per_stage.append({
            "stage": i + 1,
            "cumulative_gain_db": round(_linear_to_db(gain_before), 3),
            "cumulative_nf_db": round(_linear_to_db(f_total), 3),
        })

    total_gain_lin = gain_before
    result: dict[str, Any] = {
        "total_gain_db": round(_linear_to_db(total_gain_lin), 3),
        "total_nf_db": round(_linear_to_db(f_total), 3),
        "per_stage": per_stage,
    }

    # 输出 P1dB：每级 op1db 先换算成输入参考 ip1db = op1db − gain，再输入参考级联
    if all(p["op1db_dbm"] is not None for p in parsed):
        inv_ip1db = 0.0
        g = 1.0
        for p in parsed:
            ip1db_lin = _db_to_linear(p["op1db_dbm"] - _linear_to_db(p["gain_lin"]))
            inv_ip1db += g / ip1db_lin
            g *= p["gain_lin"]
        result["total_op1db_dbm"] = round(_linear_to_db(1.0 / inv_ip1db * total_gain_lin), 3)

    # IP3：直接输入参考级联，输出 OIP3 = IIP3 + 总增益
    if all(p["iip3_dbm"] is not None for p in parsed):
        inv_iip3 = 0.0
        g = 1.0
        for p in parsed:
            inv_iip3 += g / _db_to_linear(p["iip3_dbm"])
            g *= p["gain_lin"]
        iip3_total_lin = 1.0 / inv_iip3
        result["total_iip3_dbm"] = round(_linear_to_db(iip3_total_lin), 3)
        result["total_oip3_dbm"] = round(_linear_to_db(iip3_total_lin * total_gain_lin), 3)

    return result


@mcp.tool()
def calculate_cascade(stages: list) -> dict[str, Any]:
    """多级射频链路的级联增益 / 噪声系数（Friis）/ 输出 P1dB / 输出 IP3。

    用法："算一下 15dB 增益、2dB 噪声，再串 10dB 增益、3dB 噪声，总增益和总噪声是多少"

    噪声级联用 Friis 公式（F_total = 1 + Σ(F_i−1)/G_{i−1}，线性值）；线性度（OP1dB /
    OIP3）用输入参考级联 1/P_total = Σ(G_{i−1}/P_i)，**只有每一级都给了对应字段才计算**，
    否则省略该项（不猜、不补默认）。

    Args:
        stages: 各级器件列表（非空）。每项 {"gain_db": 增益 dB（可为负）,
            "nf_db": 噪声系数 dB（≥0）, "op1db_dbm": 输出 1dB 压缩点 dBm（可选）,
            "iip3_dbm": 输入 IP3 dBm（可选）}。

    Returns:
        {"success": true, "total_gain_db": ..., "total_nf_db": ...,
         "per_stage": [{"stage", "cumulative_gain_db", "cumulative_nf_db"}],
         ["total_op1db_dbm", "total_iip3_dbm", "total_oip3_dbm"]（仅当各级都给了对应字段）}
    """
    try:
        result = _cascade(stages)
    except (ValueError, TypeError) as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    result["success"] = True
    return result


if __name__ == "__main__":
    # 解析解对照：增益 15dB + 10dB ⇒ 总增益 25dB；
    # F = 10^0.2 + (10^0.3 − 1)/10^1.5 ≈ 1.6164 ⇒ 总 NF ≈ 2.086 dB
    r = _cascade([
        {"gain_db": 15.0, "nf_db": 2.0},
        {"gain_db": 10.0, "nf_db": 3.0},
    ])
    assert abs(r["total_gain_db"] - 25.0) < 1e-6, r
    assert abs(r["total_nf_db"] - 2.086) < 0.01, r

    r2 = _cascade([
        {"gain_db": 10.0, "nf_db": 2.0, "op1db_dbm": 20.0, "iip3_dbm": 5.0},
        {"gain_db": 10.0, "nf_db": 2.0, "op1db_dbm": 20.0, "iip3_dbm": 5.0},
    ])
    assert abs(r2["total_gain_db"] - 20.0) < 1e-6, r2
    assert math.isfinite(r2["total_op1db_dbm"]) and math.isfinite(r2["total_oip3_dbm"]), r2
    print("cascade self-check passed:", r["total_nf_db"], r2["total_oip3_dbm"])
