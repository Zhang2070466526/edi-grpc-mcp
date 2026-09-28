"""微波电路设计 MCP 工具 —— 传输线阻抗/宽度互算 + 威尔金森功分器设计。

封装 servers/microwave/ 下的纯计算脚本（transline.py 传输线核心、
wilkinson_designer.py 功分器设计），暴露为 MCP 工具供 AI 调用。
"""
from __future__ import annotations

import math
from typing import Any

from servers import mcp
from servers.utils import error_response

from servers.microwave.transline import (
    Microstrip, Stripline, GCPW, Coax, P, MM,
)
from servers.microwave.wilkinson_designer import design_wilkinson as _wilkinson

_TRANSLINE_CLASSES = {
    "microstrip": Microstrip,
    "stripline": Stripline,
    "gcpw": GCPW,
    "coax": Coax,
}


def _require_positive(value, name: str):
    """校验 value 为大于 0 的有限数值，非法返回错误字典，合法返回 None。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value) or value <= 0:
        return error_response("INVALID_PARAMETERS", f"{name} 必须是大于 0 的有限数值")
    return None


def _loss_attr(tl, names):
    """各传输线类的损耗属性名不一致（Microstrip 用 attenuation_*，其余用 loss_*），统一取值。"""
    for name in names:
        if hasattr(tl, name):
            return getattr(tl, name)
    return 0.0


_C0_MS = 299792458.0  # 光速 m/s


def _set_quarterwave_length(tl, f_hz, er):
    """把线长设为该几何在 f_hz 处的 λ/4（模型内部长度参数以米为单位）。

    必要性：模型里导体/介质损耗 = 「每米损耗 × PHYS_LEN」，而 `analyse()` 只按几何
    算阻抗、不推导线长 —— 不设线长时损耗恒等于 0（假零）。综合方向的 `synthesize()`
    自带 ANG_L=90°，故这里对齐同一口径：两个方向报的都是「一条 λ/4 段」的损耗。
    """
    eeff = getattr(tl, "eps_eff", None) or er  # Coax 为 TEM、无 eps_eff，等于 er
    tl.set(P.ANG_L, math.pi / 2.0)
    tl.set(P.PHYS_LEN, _C0_MS / f_hz / math.sqrt(eeff) / 4.0)


@mcp.tool()
def calculate_transline(
        transline_type: str,
        er: float,
        freq: float,
        width: float | None = None,
        z0: float | None = None,
        h: float | None = None,
        t: float = 0.035,
        gap: float | None = None,
        d_in: float | None = None,
        d_out: float | None = None,
        tand: float = 0.0,
) -> dict[str, Any]:
    """计算传输线的特性阻抗或线宽（微带 / 带状 / 接地共面波导 / 同轴）。

    用法："算 er=4.3、H=0.2mm、W=0.4mm 的微带线在 1GHz 的阻抗"

    给 width 算阻抗（分析）；给 z0 反解线宽（综合）。width 与 z0 二选一。

    Args:
        transline_type: 传输线类型 microstrip / stripline / gcpw / coax。
        er: 相对介电常数（>0）。
        freq: 工作频率 GHz（>0）。
        width: 线宽 mm（算阻抗时提供，与 z0 二选一）。
        z0: 特性阻抗 ohm（算线宽时提供，与 width 二选一）。
        h: 基板厚度 mm（microstrip/stripline/gcpw 必填；coax 忽略）。
        t: 导体厚度 mm，默认 0.035。
        gap: 接地共面波导槽宽 mm（仅 gcpw）。
        d_in: 同轴内导体直径 mm（仅 coax）。
        d_out: 同轴外导体内径 mm（仅 coax）。
        tand: 损耗角正切，默认 0。

    Returns:
        {"success": True, "z0": 阻抗 ohm, "width_mm": 线宽 mm, "eps_eff": 有效介电常数,
         "loss_conductor_db": 导体损耗 dB, "loss_dielectric_db": 介质损耗 dB}
    """
    transline_type = (transline_type or "").strip().lower()
    cls = _TRANSLINE_CLASSES.get(transline_type)
    if cls is None:
        return error_response(
            "INVALID_PARAMETERS",
            f"transline_type 必须是 {', '.join(sorted(_TRANSLINE_CLASSES))} 之一",
        )

    is_coax = transline_type == "coax"
    if is_coax:
        if (d_in is None) == (z0 is None):
            return error_response("INVALID_PARAMETERS", "coax 的 d_in 与 z0 必须二选一（给 d_in 算阻抗，给 z0 算内径）")
    elif (width is None) == (z0 is None):
        return error_response("INVALID_PARAMETERS", "width 与 z0 必须二选一（给 width 算阻抗，给 z0 算线宽）")

    if (err := _require_positive(er, "er")) is not None:
        return err
    if (err := _require_positive(freq, "freq")) is not None:
        return err

    # 按类型校验必需的几何参数
    if not is_coax:
        if h is None:
            return error_response("INVALID_PARAMETERS", f"{transline_type} 需要 h（基板厚度 mm）")
        if (err := _require_positive(h, "h")) is not None:
            return err
    else:
        if d_out is None:
            return error_response("INVALID_PARAMETERS", "coax 需要 d_out（外导体内径 mm）")
        if (err := _require_positive(d_out, "d_out")) is not None:
            return err
        if d_in is not None and (err := _require_positive(d_in, "d_in")) is not None:
            return err

    if transline_type == "gcpw" and gap is None:
        return error_response("INVALID_PARAMETERS", "gcpw 需要 gap（槽宽 mm）")

    if tand is not None and (not isinstance(tand, (int, float)) or isinstance(tand, bool)
                             or not math.isfinite(tand) or tand < 0):
        return error_response("INVALID_PARAMETERS", "tand 必须是非负有限数值")

    # 构造实例并设置公共参数
    tl = cls()
    f_hz = freq * 1e9
    tl.set(P.EPSILONR, er).set(P.FREQUENCY, f_hz).set(P.TAND, tand)
    # 公共默认：铜电导率 + 磁导率（TranslineBase 默认 0，须显式设，否则衰减/趋肤深度除零）
    tl.set(P.SIGMA, 5.8e7).set(P.MUR, 1.0).set(P.MURC, 1.0)
    if transline_type != "coax":
        h_m = h * MM
        t_m = t * MM
        tl.set(P.H, h_m).set(P.T, t_m)
    if transline_type == "microstrip":
        # 覆盖层上表面高度取极大值 = 无覆盖层；粗糙度取 0（KiCad 默认）
        tl.set(P.H_T, 1.0e20).set(P.ROUGH, 0.0)

    # 设置各类型特有参数
    if transline_type == "stripline":
        tl.set(P.STRIPLINE_A, (h_m - t_m) / 2.0)
    elif transline_type == "gcpw":
        if (err := _require_positive(gap, "gap")) is not None:
            return err
        tl.set(P.PHYS_S, gap * MM)
    elif is_coax:
        tl.set(P.PHYS_DIAM_OUT, d_out * MM)

    # 分析（尺寸→阻抗）或综合（阻抗→尺寸）
    if is_coax:
        if d_in is not None:
            tl.set(P.PHYS_DIAM_IN, d_in * MM)
            tl.analyse()
            _set_quarterwave_length(tl, f_hz, er)
            tl.analyse()
            result = {"z0": tl.z0}
        else:
            if (err := _require_positive(z0, "z0")) is not None:
                return err
            tl.set(P.Z0, z0).set(P.ANG_L, math.pi / 2.0)
            ok = tl.synthesize()
            if not ok:
                return error_response("SYNTHESIS_FAILED", "综合未收敛（几何参数可能超出该传输线的有效范围）")
            result = {"z0": tl.z0, "d_in_mm": tl.d_in / MM}
    else:
        if width is not None:
            if (err := _require_positive(width, "width")) is not None:
                return err
            tl.set(P.PHYS_WIDTH, width * MM)
            tl.analyse()
            _set_quarterwave_length(tl, f_hz, er)
            tl.analyse()
            result = {"z0": tl.z0}
        else:
            if (err := _require_positive(z0, "z0")) is not None:
                return err
            tl.set(P.Z0, z0).set(P.ANG_L, math.pi / 2.0)
            ok = tl.synthesize()
            if not ok:
                return error_response("SYNTHESIS_FAILED", "综合未收敛（几何参数可能超出该传输线的有效范围）")
            result = {"z0": tl.z0, "width_mm": tl.width / MM}

    result.update({
        "success": True,
        "eps_eff": getattr(tl, "eps_eff", er),  # Coax（TEM）无 eps_eff 属性，等于 er
        "loss_conductor_db": _loss_attr(tl, ("loss_conductor_db", "attenuation_conductor_db")),
        "loss_dielectric_db": _loss_attr(tl, ("loss_dielectric_db", "attenuation_dielectric_db")),
    })
    return result


@mcp.tool()
def design_wilkinson(
        h_mm: float,
        er: float,
        f0_ghz: float,
        n_sections: int,
        z0: float = 50.0,
        fbw: float | None = None,
        ripple_db: float | None = None,
) -> dict[str, Any]:
    """设计多级威尔金森功分器，输出各级微带线宽、线长和隔离电阻。

    用法："设计一个 FR4、0.8mm、2.45GHz、2 级的威尔金森功分器"

    Args:
        h_mm: 基板厚度 mm（>0）。
        er: 相对介电常数（>0）。
        f0_ghz: 中心频率 GHz（>0）。
        n_sections: 级数（每臂四分之一波长节数，>=1）。
        z0: 端口阻抗 ohm，默认 50。
        fbw: 相对带宽（如 1.0=100%），与 ripple_db 二选一；都不传则按默认回波损耗 20dB。
        ripple_db: 通带最小回波损耗 dB（Chebyshev 等波纹）。

    Returns:
        {"success": True, "sections": [{"width_mm":..., "length_mm":..., "resistor_ohm":...}],
         "arm_impedance_ohm": [...], ...}
    """
    if (err := _require_positive(h_mm, "h_mm")) is not None:
        return err
    if (err := _require_positive(er, "er")) is not None:
        return err
    if (err := _require_positive(f0_ghz, "f0_ghz")) is not None:
        return err
    if not isinstance(n_sections, int) or isinstance(n_sections, bool) or n_sections < 1:
        return error_response("INVALID_PARAMETERS", "n_sections 必须是 >=1 的整数")

    try:
        result = _wilkinson(h_mm=h_mm, er=er, f0_GHz=f0_ghz,
                            n_sections=n_sections, z0=z0,
                            fbw=fbw, ripple_db=ripple_db)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))

    return {"success": True, **result}
