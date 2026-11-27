"""L 型阻抗匹配网络合成 —— 已知源/负载实阻抗与频率，输出两元件 L 网络。

纯 Python，公式见教科书 L 型匹配：
  Q = √(R_high / R_low − 1)
  串联元件接低阻侧（|X| = Q·R_low），并联元件接高阻侧（|B| = Q / R_high）。
  符号相反（一个感性一个容性）即得实匹配；两解（低通 / 高通）。
"""
from __future__ import annotations

import cmath
import math
from typing import Any

from servers import mcp
from servers.utils import error_response


def _series_to_lc(x_ohm: float, f_hz: float) -> dict:
    """串联电抗 X(Ω) → 元件。X>0 电感 L=X/ω；X<0 电容 C=−1/(ωX)。"""
    omega = 2.0 * math.pi * f_hz
    if x_ohm >= 0.0:
        return {"kind": "L", "value_si": x_ohm / omega, "unit": "H", "reactance_ohm": x_ohm}
    return {"kind": "C", "value_si": -1.0 / (omega * x_ohm), "unit": "F", "reactance_ohm": x_ohm}


def _shunt_to_lc(b_siemens: float, f_hz: float) -> dict:
    """并联电纳 B(S) → 元件。B>0 电容 C=B/ω；B<0 电感 L=−1/(ωB)。"""
    omega = 2.0 * math.pi * f_hz
    if b_siemens >= 0.0:
        return {"kind": "C", "value_si": b_siemens / omega, "unit": "F",
                "susceptance_siemens": b_siemens}
    return {"kind": "L", "value_si": -1.0 / (omega * b_siemens), "unit": "H",
            "susceptance_siemens": b_siemens}


def _l_network(source_ohm: float, load_ohm: float, f_hz: float) -> dict:
    if not math.isfinite(source_ohm) or not math.isfinite(load_ohm):
        raise ValueError("源/负载阻抗必须是有限正实数")
    if source_ohm <= 0.0 or load_ohm <= 0.0:
        raise ValueError("源/负载阻抗必须是正实数（本工具只做实阻抗到实阻抗的匹配）")
    if abs(source_ohm - load_ohm) < 1e-9:
        raise ValueError("源阻抗等于负载阻抗，无需匹配")

    r_high = max(source_ohm, load_ohm)
    r_low = min(source_ohm, load_ohm)
    q = math.sqrt(r_high / r_low - 1.0)

    x_mag = q * r_low        # 串联电抗幅值，接低阻侧
    b_mag = q / r_high       # 并联电纳幅值，接高阻侧
    series_side = "source" if source_ohm == r_low else "load"
    shunt_side = "load" if load_ohm == r_high else "source"

    # 低通解：串联电感(+X) + 并联电容(+B)
    low_pass_series = _series_to_lc(x_mag, f_hz)
    low_pass_shunt = _shunt_to_lc(b_mag, f_hz)
    # 高通解：串联电容(−X) + 并联电感(−B)
    high_pass_series = _series_to_lc(-x_mag, f_hz)
    high_pass_shunt = _shunt_to_lc(-b_mag, f_hz)

    def _sol(series: dict, shunt: dict, label: str) -> dict:
        return {
            "type": label,
            "series": {**series, "side": series_side},
            "shunt": {**shunt, "side": shunt_side},
        }

    return {
        "source_impedance_ohm": source_ohm,
        "load_impedance_ohm": load_ohm,
        "q": round(q, 4),
        "solutions": [
            _sol(low_pass_series, low_pass_shunt, "low_pass"),
            _sol(high_pass_series, high_pass_shunt, "high_pass"),
        ],
    }


@mcp.tool()
def design_matching_network(
    source_impedance_ohm: float,
    load_impedance_ohm: float,
    freq_ghz: float,
) -> dict[str, Any]:
    """L 型匹配网络合成：已知源/负载实阻抗与频率，给出两元件匹配网络（低通/高通两解）。

    用法："把 50Ω 源匹配到 100Ω 负载，工作在 2.4GHz"

    只做**实阻抗 → 实阻抗**的两元件 L 匹配（覆盖绝大多数"50Ω 匹配到某阻值"的场景）。
    元件值按串联接低阻侧、并联接高阻侧给出；每个解含低通（串 L 并 C）与高通（串 C 并 L）
    两种，工程师按需选。源/负载带虚部、π/T 三元件网络（需指定 Q）暂不支持。

    Args:
        source_impedance_ohm: 源阻抗实部（Ω，>0）。
        load_impedance_ohm: 负载阻抗实部（Ω，>0，≠ 源阻抗）。
        freq_ghz: 工作频率（GHz，>0）。

    Returns:
        {"success": true, "q": ..., "solutions": [{"type": "low_pass"/"high_pass",
         "series": {"kind": "L"/"C", "value_si", "unit", "reactance_ohm", "side"},
         "shunt":  {"kind", "value_si", "unit", "susceptance_siemens", "side"}}]}
    """
    try:
        if not math.isfinite(freq_ghz) or freq_ghz <= 0.0:
            raise ValueError("freq_ghz 必须是正有限数")
        result = _l_network(float(source_impedance_ohm), float(load_impedance_ohm),
                            freq_ghz * 1e9)
    except (ValueError, TypeError) as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    result["success"] = True
    return result


def _z_in(series: dict, shunt: dict, load_ohm: float, f_hz: float) -> complex:
    """从源侧往里看：[串联 jX] 再 [并联 jB ∥ R_load]，返回输入阻抗（自检用）。"""
    omega = 2.0 * math.pi * f_hz
    x = series["reactance_ohm"]
    b = shunt["susceptance_siemens"]
    z_shunt = 1.0 / (1.0 / load_ohm + 1j * b)
    return 1j * x + z_shunt


if __name__ == "__main__":
    # 50Ω → 100Ω：Q=1，串 X=50Ω（接源侧）、并 B=0.01S（接负载侧）。
    # 低通解 Z_in = j50 + (100 ∥ 1/(j0.01)) = j50 + 50−j50 = 50Ω，与源共轭匹配。
    net = _l_network(50.0, 100.0, 2.4e9)
    lp = net["solutions"][0]
    z = _z_in(lp["series"], lp["shunt"], 100.0, 2.4e9)
    assert abs(z.real - 50.0) < 1e-6 and abs(z.imag) < 1e-6, z
    # 高通解同理成立
    hp = net["solutions"][1]
    z = _z_in(hp["series"], hp["shunt"], 100.0, 2.4e9)
    assert abs(z.real - 50.0) < 1e-6 and abs(z.imag) < 1e-6, z
    print("matching_network self-check passed:", lp["series"]["kind"], hp["series"]["kind"])
