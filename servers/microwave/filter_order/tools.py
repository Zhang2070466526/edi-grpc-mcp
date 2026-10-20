"""滤波器阶数评估 MCP 工具 —— 广义切比雪夫高通/低通/带阻 + 功分器/巴伦节数。

封装本包内四个阶数评估算法脚本（算法层只读，理论说明见各脚本头部）。
频率参数对外统一用 GHz（与 calculate_transline / design_chip / design_wilkinson 一致），
内部转 Hz 调算法层；返回体的频率字段同样以 GHz 给出。

四个算法均纯数值计算（不依赖 EDI/AEDT/CST），无 I/O，可直接离线调用。
"""
from __future__ import annotations

import math
from typing import Any

from servers import mcp
from servers.utils import error_response

import importlib

from .wilkinson_marchand_order import order_final as _wilkinson_order

_GHZ = 1e9


def _load_scipy_estimator(module_name: str, func_name: str):
    """惰性加载依赖 scipy 的阶数评估函数；scipy 缺失时返回 None（不抛 ImportError）。

    阶数评估算法脚本顶层 import scipy，而 Win7 现场可能不装 scipy（requirements-win7.txt
    无此项）。顶层 import 会让 registry_server 整包 import 失败、全部工具不可用；改为
    调用时才加载，scipy 缺失时只有这 3 个工具返回依赖缺失，其余照常。
    """
    try:
        mod = importlib.import_module(f"servers.microwave.filter_order.{module_name}")
        return getattr(mod, func_name)
    except ImportError:
        return None


def _require_positive(value, name: str):
    """校验 value 为大于 0 的有限数值，非法返回错误字典，合法返回 None。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(value) or value <= 0:
        return error_response("INVALID_PARAMETERS", f"{name} 必须是大于 0 的有限数值")
    return None


def _hz_to_ghz(v: float) -> float:
    """Hz → GHz，round 到 MHz 精度（工程上零点频率不需要更高精度）。"""
    return round(v / _GHZ, 6)


def _stopband_hz(specs):
    """阻带规范 GHz → Hz。每项 [f, A]（抑制点）或 [f_lo, f_hi, A]（抑制频段）。"""
    out = []
    for s in specs:
        if not isinstance(s, (list, tuple)) or len(s) not in (2, 3):
            raise ValueError("阻带规范每项必须是 [f, A]（抑制点）或 [f_lo, f_hi, A]（抑制频段）")
        A = float(s[-1])
        if not math.isfinite(A) or A <= 0:
            raise ValueError("阻带抑制 A 必须是大于 0 的 dB 值")
        if len(s) == 2:
            out.append((float(s[0]) * _GHZ, A))
        else:
            out.append((float(s[0]) * _GHZ, float(s[1]) * _GHZ, A))
    return out


def _passband_hz(passband_specs):
    """带阻两侧通带要求 GHz → Hz。两种形式（与 demo 脚本一致）：
    [f_p1, A_p1, f_p2, A_p2] 或 [[f_p1, A_p1], [f_p2, A_p2]]。"""
    ps = list(passband_specs)
    if len(ps) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in ps):
        return (float(ps[0]) * _GHZ, float(ps[1]), float(ps[2]) * _GHZ, float(ps[3]))
    if len(ps) == 2 and all(isinstance(x, (list, tuple)) and len(x) == 2 for x in ps):
        return [(float(f) * _GHZ, float(a)) for f, a in ps]
    raise ValueError("通带要求必须是 [f_p1, A_p1, f_p2, A_p2] 或 [[f_p1, A_p1], [f_p2, A_p2]]")


def _check_common(f_p_ghz: float, rl_db: float, q_unloaded, max_notches: int):
    """低通/高通共用的参数校验，返回错误字典或 None。"""
    if (err := _require_positive(f_p_ghz, "f_p_ghz")) is not None:
        return err
    if not isinstance(rl_db, (int, float)) or isinstance(rl_db, bool) or not math.isfinite(rl_db) or rl_db >= 0:
        return error_response("INVALID_PARAMETERS", "rl_db 必须是负值（回波损耗 dB）")
    if q_unloaded is not None and (err := _require_positive(q_unloaded, "q_unloaded")) is not None:
        return err
    if not isinstance(max_notches, int) or isinstance(max_notches, bool) or max_notches < 0:
        return error_response("INVALID_PARAMETERS", "max_notches 必须是 >=0 的整数")
    return None


def _format_filter_result(result: dict) -> dict[str, Any]:
    """低通/高通结果整理：Hz 频率字段统一转 GHz。"""
    return {
        "success": True,
        "N": result["N"],
        "notches": result["notches"],
        "notches_ghz": [_hz_to_ghz(f) for f in result["notches_Hz"]],
        "verify_rows": [
            {"f_ghz": _hz_to_ghz(f), "atten_db": a, "required_db": A}
            for f, a, A in result["verify_rows"]
        ],
        "solutions": [
            {"N": s["N"], "notches": s["notches"],
             "notches_ghz": [_hz_to_ghz(f) for f in s["notches_Hz"]],
             "min_margin_db": s["min_margin_dB"]}
            for s in result["solutions"]
        ],
    }


@mcp.tool()
def filter_order_lowpass(
        f_p_ghz: float,
        stopband_specs: list,
        rl_db: float = -20.0,
        q_unloaded: float | None = None,
        max_notches: int = 4,
) -> dict[str, Any]:
    """估计广义切比雪夫低通滤波器的阶数与有限传输零点。

    用法："评估一个通带截止 1GHz、2~3GHz 处抑制 ≥40dB 的低通滤波器需要几阶"

    stopband_specs 每项 [f, A]（阻带抑制点，GHz/dB）或 [f_lo, f_hi, A]（阻带频段）。

    Args:
        f_p_ghz: 通带截止频率 GHz（>0）。
        stopband_specs: 阻带规范列表，每项 [f, A] 或 [f_lo, f_hi, A]（频率 GHz，抑制 dB）。
            频段形式 [f_lo, f_hi, A] 在 verify_rows 里只回该频段内最差点（衰减最小处）1 行，非两端点。
        rl_db: 通带回波损耗 dB（负值），默认 -20。
        q_unloaded: 谐振器无载 Q 值（None=无损），默认 None。
        max_notches: 最大有限传输零点对数，默认 4。

    Returns:
        {"success": True, "N": 阶数, "notches": 零点对数, "notches_ghz": [...],
         "verify_rows": [...], "solutions": [...]}
    """
    if (err := _check_common(f_p_ghz, rl_db, q_unloaded, max_notches)) is not None:
        return err
    try:
        specs_hz = _stopband_hz(stopband_specs)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    for spec in specs_hz:
        if spec[0] <= f_p_ghz * _GHZ:
            return error_response("INVALID_PARAMETERS",
                                  f"低通阻带频率 {spec[0] / _GHZ:g} GHz 必须高于通带截止 {f_p_ghz:g} GHz")
    _est = _load_scipy_estimator("filter_order_estimator_lowpass", "estimate_lowpass_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", "阶数评估需要 scipy 依赖，当前环境未安装")
    try:
        result = _est(f_p_ghz * _GHZ, specs_hz, RL_dB=rl_db, Q_unloaded=q_unloaded,
                      max_notches=max_notches, verbose=False)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    if not result.get("success"):
        return error_response("NO_SOLUTION", "未找到可行解（指标可能过紧，试放宽抑制或回波损耗）")
    return _format_filter_result(result)


@mcp.tool()
def filter_order_highpass(
        f_p_ghz: float,
        stopband_specs: list,
        rl_db: float = -20.0,
        q_unloaded: float | None = None,
        max_notches: int = 4,
) -> dict[str, Any]:
    """估计广义切比雪夫高通滤波器的阶数与有限传输零点。

    用法："评估一个通带 1GHz 起、0.3~0.5GHz 处抑制 ≥40dB 的高通滤波器需要几阶"

    高通阻带在 f < f_p 一侧；stopband_specs 每项 [f, A] 或 [f_lo, f_hi, A]（均须 < f_p_ghz）。

    Args:
        f_p_ghz: 通带截止频率 GHz（阻带位于其下侧，>0）。
        stopband_specs: 阻带规范列表，每项 [f, A] 或 [f_lo, f_hi, A]（频率 GHz，抑制 dB，均 < f_p_ghz）。
            频段形式 [f_lo, f_hi, A] 在 verify_rows 里只回该频段内最差点（衰减最小处）1 行，非两端点。
        rl_db: 通带回波损耗 dB（负值），默认 -20。
        q_unloaded: 谐振器无载 Q 值（None=无损），默认 None。
        max_notches: 最大有限传输零点对数，默认 4。

    Returns:
        {"success": True, "N": 阶数, "notches": 零点对数, "notches_ghz": [...],
         "verify_rows": [...], "solutions": [...]}
    """
    if (err := _check_common(f_p_ghz, rl_db, q_unloaded, max_notches)) is not None:
        return err
    _est = _load_scipy_estimator("filter_order_estimator_highpass", "estimate_highpass_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", "阶数评估需要 scipy 依赖，当前环境未安装")
    try:
        specs_hz = _stopband_hz(stopband_specs)
        result = _est(f_p_ghz * _GHZ, specs_hz, RL_dB=rl_db, Q_unloaded=q_unloaded,
                      max_notches=max_notches, verbose=False)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    if not result.get("success"):
        return error_response("NO_SOLUTION", "未找到可行解（指标可能过紧，试放宽抑制或回波损耗）")
    return _format_filter_result(result)


@mcp.tool()
def filter_order_bandstop(
        f_s1_ghz: float,
        f_s2_ghz: float,
        stopband_specs: float | list,
        passband_specs: list,
        rl_db: float = -20.0,
        q_unloaded: float | None = None,
        max_notches: int = 4,
) -> dict[str, Any]:
    """估计广义切比雪夫带阻滤波器的阶数与陷波零点。

    用法："评估一个阻带 2.1~2.2GHz 陷波 ≥20dB、两侧通带 ≤1dB 的带阻滤波器需要几阶"

    Args:
        f_s1_ghz: 阻带下边频率 GHz（>0）。
        f_s2_ghz: 阻带上边频率 GHz（> f_s1_ghz）。
        stopband_specs: 阻带抑制要求——浮点数 A（整个阻带抑制 ≥A dB），
            或列表 [[f, A]] / [[f_lo, f_hi, A]]（抑制点/子带，频率 GHz）。
        passband_specs: 两侧通带要求 [f_p1, A_p1, f_p2, A_p2] 或 [[f_p1, A_p1], [f_p2, A_p2]]
            （f_p1 < f_s1 下通带边、f_p2 > f_s2 上通带边，衰减 ≤ A_p dB）。
        rl_db: 通带回波损耗 dB（负值），默认 -20。
        q_unloaded: 谐振器无载 Q 值（None=无损），默认 None。
        max_notches: 最大有限传输零点对数，默认 4。

    Returns:
        {"success": True, "N": 原型阶数, "order_bandstop": 物理阶数(2N),
         "notches": 零点对数, "notch_freq_pairs_ghz": [[下,上],...],
         "f0_ghz": 阻带中心, "fbw": 相对带宽, ...}
    """
    if (err := _require_positive(f_s1_ghz, "f_s1_ghz")) is not None:
        return err
    if (err := _require_positive(f_s2_ghz, "f_s2_ghz")) is not None:
        return err
    if f_s2_ghz <= f_s1_ghz:
        return error_response("INVALID_PARAMETERS", "要求 f_s2_ghz > f_s1_ghz")
    if not isinstance(rl_db, (int, float)) or isinstance(rl_db, bool) or not math.isfinite(rl_db) or rl_db >= 0:
        return error_response("INVALID_PARAMETERS", "rl_db 必须是负值（回波损耗 dB）")
    if q_unloaded is not None and (err := _require_positive(q_unloaded, "q_unloaded")) is not None:
        return err
    if not isinstance(max_notches, int) or isinstance(max_notches, bool) or max_notches < 0:
        return error_response("INVALID_PARAMETERS", "max_notches 必须是 >=0 的整数")
    _est = _load_scipy_estimator("filter_order_estimator_bandstop", "estimate_bandstop_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", "阶数评估需要 scipy 依赖，当前环境未安装")
    try:
        if isinstance(stopband_specs, (int, float)) and not isinstance(stopband_specs, bool):
            specs = float(stopband_specs)
            if not math.isfinite(specs) or specs <= 0:
                raise ValueError("阻带抑制 A 必须是大于 0 的 dB 值")
        else:
            specs = _stopband_hz(stopband_specs)
        passband_hz = _passband_hz(passband_specs)
        result = _est(f_s1_ghz * _GHZ, f_s2_ghz * _GHZ, specs, passband_hz,
                      RL_dB=rl_db, Q_unloaded=q_unloaded, max_notches=max_notches)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    if not result.get("success"):
        return error_response("NO_SOLUTION", result.get("reason") or "未找到可行解（指标可能过紧）")

    def _pairs_ghz(pairs_hz):
        return [[_hz_to_ghz(lo), _hz_to_ghz(hi)] for lo, hi in pairs_hz]

    return {
        "success": True,
        "N": result["N"],
        "order_bandstop": result["order_bandstop"],
        "notches": result["notches"],
        "notch_freq_pairs_ghz": _pairs_ghz(result["notch_freq_pairs_Hz"]),
        "f0_ghz": _hz_to_ghz(result["f0"]),
        "fbw": result["FBW"],
        "passband_ripple_db": result["passband_ripple_dB"],
        "solutions": [
            {"N": s["N"], "order_bandstop": s["order_bandstop"], "notches": s["notches"],
             "notch_freq_pairs_ghz": _pairs_ghz(s["notch_freq_pairs_Hz"]),
             "min_margin_db": s["min_margin_dB"]}
            for s in result["solutions"]
        ],
    }


@mcp.tool()
def wilkinson_marchand_order(
        ratio: float,
        rl_db: float = 15.0,
) -> dict[str, Any]:
    """估计 Wilkinson 功分器 / Marchand 巴伦的节数（阶数）。

    用法："评估一个频率比 6:1 的宽带功分器需要几节"

    逻辑：切比雪夫变阻综合公式对 N≥2 有效；公式给出 N=1 时用单节三端口精确响应
    校验带边隔离度，达标则 N=1，否则升为 N=2。

    Args:
        ratio: 频率比（高/低），>1。
        rl_db: 带边隔离度指标 dB，默认 15。

    Returns:
        {"success": True, "order": N}
    """
    if (err := _require_positive(ratio, "ratio")) is not None:
        return err
    if ratio <= 1.0:
        return error_response("INVALID_PARAMETERS", "ratio 必须是 >1 的频率比（高/低）")
    if not isinstance(rl_db, (int, float)) or isinstance(rl_db, bool) or not math.isfinite(rl_db) or rl_db <= 0:
        return error_response("INVALID_PARAMETERS", "rl_db 必须是正数（隔离度指标 dB）")
    try:
        n = _wilkinson_order(float(ratio), float(rl_db))
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    return {"success": True, "order": int(n)}
