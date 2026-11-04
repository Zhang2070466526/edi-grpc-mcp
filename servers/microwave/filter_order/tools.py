"""滤波器阶数评估 MCP 工具 —— 广义切比雪夫高通/低通/带阻 + 功分器/巴伦节数。

封装本包内四个阶数评估算法脚本（算法层只读，理论说明见各脚本头部）。
频率参数对外统一用 GHz（与 calculate_transline / design_chip / design_wilkinson 一致），
内部转 Hz 调算法层；返回体的频率字段同样以 GHz 给出。

四个算法均纯数值计算（不依赖 EDI/AEDT/CST），无 I/O，可直接离线调用。
"""
from __future__ import annotations

import importlib
import math
from typing import Any

from servers import mcp
from servers.utils import error_response

from .wilkinson_marchand_order import order_final as _wilkinson_order

_GHZ = 1e9


def _load_scipy_estimator(module_name: str, func_name: str):
    """惰性加载依赖 scipy 的阶数评估函数，返回 (func, None) 或 (None, 失败原因)。

    阶数评估算法脚本顶层 import scipy，而 Win7 现场 / 打包 exe 未必有可用的 scipy
    （requirements-win7.txt 无此项；PyInstaller 包内也可能收集不全或 DLL 加载失败）。
    顶层 import 会让 registry_server 整包 import 失败、全部工具不可用；改为调用时才加载，
    不可用时只有这 3 个工具报错，其余照常。

    失败原因必须回传给调用方：只回「未安装」会把「scipy 在包内但 import/DLL 失败」
    也当成未安装，打包形态下完全无法定位（2026-10-20 实测：exe 里 scipy 文件齐全却 import 失败）。
    """
    try:
        mod = importlib.import_module(f"servers.microwave.filter_order.{module_name}")
        return getattr(mod, func_name), None
    except Exception as exc:  # ImportError / OSError(DLL 加载失败) / AttributeError 都要带出来
        return None, f"{type(exc).__name__}: {exc}"


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
            f = float(s[0])
            if not math.isfinite(f) or f <= 0:
                raise ValueError("阻带频率必须是大于 0 的有限数值")
            out.append((f * _GHZ, A))
        else:
            f_lo, f_hi = float(s[0]), float(s[1])
            if not math.isfinite(f_lo) or not math.isfinite(f_hi) or f_lo <= 0 or f_hi <= 0:
                raise ValueError("阻带频率必须是大于 0 的有限数值")
            if f_lo >= f_hi:
                raise ValueError("阻带频段顺序反了：要求 f_lo < f_hi（写成 [下边, 上边, 抑制dB]）")
            out.append((f_lo * _GHZ, f_hi * _GHZ, A))
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
    """低通阶数估算：通带到 f_p、阻带在 f_p 之上，返回阶数与传输零点。高通用 filter_order_highpass、带阻用 filter_order_bandstop。

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
    _est, _why = _load_scipy_estimator("filter_order_estimator_lowpass", "estimate_lowpass_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", f"阶数评估需要 scipy 依赖，当前环境不可用（{_why}）")
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
    """高通阶数估算：通带自 f_p 向上、阻带在 f_p 之下，返回阶数与传输零点。低通用 filter_order_lowpass、带阻用 filter_order_bandstop。

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
    _est_module = "filter_order_estimator_highpass"
    try:
        specs_hz = _stopband_hz(stopband_specs)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    # 高通阻带必须整体落在 f_p 之下；在封装层先校验，scipy 不可用时也能给出参数错误
    for spec in specs_hz:
        f_edge = spec[1] if len(spec) == 3 else spec[0]
        if f_edge >= f_p_ghz * _GHZ:
            return error_response("INVALID_PARAMETERS",
                                  f"高通阻带频率 {f_edge / _GHZ:g} GHz 必须低于通带截止 {f_p_ghz:g} GHz")
    _est, _why = _load_scipy_estimator(_est_module, "estimate_highpass_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", f"阶数评估需要 scipy 依赖，当前环境不可用（{_why}）")
    try:
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
    """带阻阶数估算：阻带是 f_s1~f_s2 这一段，返回阶数与陷波零点。低通用 filter_order_lowpass、高通用 filter_order_highpass。

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
    # 先做参数解析/校验，再加载 scipy —— 否则 scipy 不可用时参数错误会被盖成依赖缺失
    try:
        if isinstance(stopband_specs, (int, float)) and not isinstance(stopband_specs, bool):
            specs = float(stopband_specs)
            if not math.isfinite(specs) or specs <= 0:
                raise ValueError("阻带抑制 A 必须是大于 0 的 dB 值")
        else:
            specs = _stopband_hz(stopband_specs)
        passband_hz = _passband_hz(passband_specs)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    # 通带位置校验也放封装层（算法层同样会查，但 scipy 不可用时算法层跑不到）
    if isinstance(passband_hz, tuple):
        _fp1, _fp2 = passband_hz[0], passband_hz[2]
    else:
        _fp1, _fp2 = passband_hz[0][0], passband_hz[1][0]
    if not (_fp1 < f_s1_ghz * _GHZ < f_s2_ghz * _GHZ < _fp2):
        return error_response("INVALID_PARAMETERS", "要求 f_p1 < f_s1 < f_s2 < f_p2")
    _est, _why = _load_scipy_estimator("filter_order_estimator_bandstop", "estimate_bandstop_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", f"阶数评估需要 scipy 依赖，当前环境不可用（{_why}）")
    try:
        result = _est(f_s1_ghz * _GHZ, f_s2_ghz * _GHZ, specs, passband_hz,
                      RL_dB=rl_db, Q_unloaded=q_unloaded, max_notches=max_notches)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    if not result.get("success"):
        return error_response("NO_SOLUTION", result.get("reason") or "未找到可行解（指标可能过紧）")

    def _pairs_ghz(pairs_hz):
        """把陷波频率对 [[f_下,f_上],...] (Hz) 转成 GHz。"""
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
            {
                "N": s["N"],
                "order_bandstop": s["order_bandstop"],
                "notches": s["notches"],
                "notch_freq_pairs_ghz": _pairs_ghz(s["notch_freq_pairs_Hz"]),
                "min_margin_db": s["min_margin_dB"]
            }
            for s in result["solutions"]
        ],
    }


@mcp.tool()
def filter_order_bandpass(
        f_p1_ghz: float,
        f_p2_ghz: float,
        stopband_specs: list,
        rl_db: float = -20.0,
        q_unloaded: float | None = None,
        max_zeros: int = 4,
) -> dict[str, Any]:
    """带通阶数估算：通带 f_p1~f_p2、阻带在两侧，返回阶数与传输零点。低通/高通/带阻分别用 filter_order_lowpass/highpass/bandstop。

    用法："评估一个通带 1.95~2.05GHz、1.85GHz 处抑制 ≥35dB、2.2GHz 处抑制 ≥25dB 的带通滤波器需要几阶"

    引入传输零点 k 可降低所需阶数 N，但受工程约束 k ≤ N/2；枚举在该约束被违反时停止，
    停止原因写入返回的 stop_note（solutions 表按 k 递增列出每个可行 k 的最小 N 与最差阻带裕量）。

    Args:
        f_p1_ghz: 通带下边频率 GHz（>0）。
        f_p2_ghz: 通带上边频率 GHz（> f_p1_ghz）。
        stopband_specs: 阻带规范列表，每项 [f, A]（抑制点，GHz/dB）或 [f_lo, f_hi, A]（抑制频段）。
            阻带频率须在通带之外（整体 < f_p1_ghz 或 > f_p2_ghz）。
        rl_db: 通带回波损耗 dB（负值），默认 -20。
        q_unloaded: 谐振器无载 Q 值（None=无损），默认 None。传值时按根左移模型计损耗
            （d_loss=1/(q_unloaded*fbw)，通带中心插损约 4.343*N*d_loss），
            pass_max_db 为通带内最大衰减（含该插损），阶数选择不受插损影响。
        max_zeros: 最大有限传输零点数（**个数**，非对数）。注意别与低通/高通/带阻的 max_notches
            （零点**对**数）混用。

    Returns:
        {"success": True, "N": 阶数, "k": 零点数, "zeros_ghz": [零点位置...],
         "f0_ghz": 中心频率, "fbw": 相对带宽, "passband_ripple_db": 通带纹波,
         "N_lower_bound_no_zeros": 无零点阶数下界, "stop_note": 枚举停止原因(None=枚举到 max_zeros 未停),
         "warnings": [使用提示...]（零点远离规范 / 含 Q 损耗口径）,
         "verify_rows": [{f_ghz, atten_db, required_db}]（每条阻带规范的 401 点网格最坏点，用于自证达标）,
         "solutions": [{k, N, zeros_ghz, min_margin_db, min_atten_db, pass_max_db}, ...]}
    """
    if (err := _require_positive(f_p1_ghz, "f_p1_ghz")) is not None:
        return err
    if (err := _require_positive(f_p2_ghz, "f_p2_ghz")) is not None:
        return err
    if f_p2_ghz <= f_p1_ghz:
        return error_response("INVALID_PARAMETERS", "要求 f_p2_ghz > f_p1_ghz")
    if not isinstance(rl_db, (int, float)) or isinstance(rl_db, bool) or not math.isfinite(rl_db) or rl_db >= 0:
        return error_response("INVALID_PARAMETERS", "rl_db 必须是负值（回波损耗 dB）")
    if q_unloaded is not None and (err := _require_positive(q_unloaded, "q_unloaded")) is not None:
        return err
    if not isinstance(max_zeros, int) or isinstance(max_zeros, bool) or max_zeros < 0:
        return error_response("INVALID_PARAMETERS", "max_zeros 必须是 >=0 的整数")
    # 先解析/校验阻带规范再加载 scipy —— scipy 不可用时参数错误仍能给出，不被盖成依赖缺失
    try:
        specs_hz = _stopband_hz(stopband_specs)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    # 空列表会一路走到算法层 np.min(空数组) 被吞成「无可行解」，误导成指标不可达
    if not specs_hz:
        return error_response("INVALID_PARAMETERS",
                              "stopband_specs 不能为空，至少给一个抑制点 [f, A] 或抑制频段 [f_lo, f_hi, A]")
    # 阻带端点恰好落在通带边：该点衰减只有纹波量级，物理上不可能满足抑制，
    # 算法层会静默返回 NO_SOLUTION（"no feasible solution"），这里直接点明可改之处
    f_p1_hz, f_p2_hz = f_p1_ghz * _GHZ, f_p2_ghz * _GHZ
    for spec in specs_hz:
        f_lo = spec[0]
        f_hi = spec[1] if len(spec) == 3 else spec[0]
        for f_edge, which, bound in ((f_hi, "左", f_p1_hz), (f_lo, "右", f_p2_hz)):
            if abs(f_edge - bound) <= 1e-9 * max(bound, 1.0):
                return error_response("INVALID_PARAMETERS",
                                      f"阻带端点 {f_edge / _GHZ:g} GHz 与通带边重合："
                                      f"通带边处衰减只有纹波量级，无法满足抑制要求，"
                                      f"请把该端点改为{'严格小于' if which == '左' else '严格大于'} "
                                      f"{bound / _GHZ:g} GHz")
    # 带通阻带必须在通带两侧（与算法层 expand_stopband_specs 的左右侧判定一致）
    for spec in specs_hz:
        f_lo = spec[0]
        f_hi = spec[1] if len(spec) == 3 else spec[0]
        if len(spec) == 3 and f_lo >= f_hi:
            return error_response("INVALID_PARAMETERS",
                                  f"抑制频段 [{f_lo / _GHZ:g}, {f_hi / _GHZ:g}] 顺序反了："
                                  f"要求 f_lo < f_hi（写成 [下边, 上边, 抑制dB]）")
        if not (f_hi <= f_p1_ghz * _GHZ or f_lo >= f_p2_ghz * _GHZ):
            return error_response("INVALID_PARAMETERS",
                                  f"带通阻带 {f_lo / _GHZ:g}~{f_hi / _GHZ:g} GHz 与通带 "
                                  f"{f_p1_ghz:g}~{f_p2_ghz:g} GHz 重叠，阻带须在通带两侧")
    _est, _why = _load_scipy_estimator("filter_order_estimator_bandpass", "estimate_filter_order")
    if _est is None:
        return error_response("DEPENDENCY_MISSING", f"阶数评估需要 scipy 依赖，当前环境不可用（{_why}）")
    try:
        result = _est(f_p1_ghz * _GHZ, f_p2_ghz * _GHZ, specs_hz,
                      RL_dB=rl_db, Q_unloaded=q_unloaded, max_zeros=max_zeros, verbose=False)
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    if not result.get("success"):
        reason = result.get("reason") or "未找到可行解（指标可能过紧）"
        # hint 按原因给方向：通带波纹超门限与 max_zeros 无关，别让调用方改错参数
        if "通带波纹" in reason:
            hint = "通带被 Q 损耗压扁：提高谐振器 Q 或放宽回波损耗后重试（增大 max_zeros 无效）"
        elif "阻带" in reason:
            hint = "阻带裕量不足：放宽抑制要求或增大 max_zeros 后重试"
        else:
            hint = "放宽抑制/回波损耗，或调大 max_zeros 后重试"
        return error_response("NO_SOLUTION", reason, hint=hint)

    def _zeros_ghz(zeros_hz):
        return [_hz_to_ghz(z) for z in zeros_hz]

    # ---- 使用提示（只做提示，不参与任何数值计算） ----
    # 说明：算法（demo 口径）本身不限制零点位置，这里只把「落在规范范围之外」的零点如实列出。
    # 判定容差是提示用的启发式规则（规范外扩 10% 带宽；单点规范外扩 5%），并在文案里写明，
    # 便于自己复核；它不是算法结论。
    def _spec_windows() -> list[tuple[float, float]]:
        wins = []
        for spec in specs_hz:
            lo = spec[0]
            hi = spec[1] if len(spec) == 3 else spec[0]
            tol = 0.10 * (hi - lo) if hi > lo else 0.05 * hi
            wins.append((lo - tol, hi + tol))
        return wins

    def _near_spec(f_hz: float) -> bool:
        return any(lo <= f_hz <= hi for lo, hi in _spec_windows())

    warnings: list[str] = []
    far_zeros = [z for z in result["zeros_bandpass_Hz"] if not _near_spec(z)]
    if far_zeros:
        spec_txt = "；".join(
            f"{spec[0] / _GHZ:g}~{(spec[1] if len(spec) == 3 else spec[0]) / _GHZ:g} GHz"
            for spec in specs_hz)
        warnings.append(
            "零点 " + ", ".join(f"{z / _GHZ:.4f}" for z in far_zeros) + " GHz 不在任何阻带规范范围内"
            f"（规范：{spec_txt}；判定容差为规范外扩 10% 带宽、单点规范外扩 5%）。"
            "算法本身不限制零点位置，这些零点对抑制的作用需自行确认；"
            "也可对比 solutions 表里更小 k 那行的零点分布")
    if any(len(spec) == 3 for spec in specs_hz):
        warnings.append("抑制频段的最坏点见 verify_rows（每条规范一行，401 点网格复核）；"
                        "如需按某一具体频率校核，可直接传该频率的单点规范")
    if q_unloaded is not None:
        warnings.append("pass_max_db 是含 Q 损耗的通带最大衰减（根左移模型，d_loss=1/(q_unloaded*fbw)，"
                        "中心插损约 4.343*N*d_loss）；插损由 Q 与相对带宽决定，与阶数选择无关")

    return {
        "success": True,
        "N": result["N"],
        "k": result["k"],
        "zeros_ghz": _zeros_ghz(result["zeros_bandpass_Hz"]),
        "f0_ghz": _hz_to_ghz(result["f0"]),
        "fbw": result["FBW"],
        "passband_ripple_db": result["passband_ripple_dB"],
        "N_lower_bound_no_zeros": round(result["N_lower_bound_no_zeros"], 3),
        "stop_note": result.get("stop_note"),
        "warnings": warnings,
        "verify_rows": [
            {"f_ghz": _hz_to_ghz(v["f_Hz"]), "atten_db": v["atten_dB"], "required_db": v["required_dB"]}
            for v in result.get("verify_rows", [])
        ],
        "solutions": [
            {
                "k": s["k"],
                "N": s["N"],
                "zeros_ghz": _zeros_ghz(s["zeros_bandpass_Hz"]),
                "min_margin_db": s["min_margin_dB"],
                "min_atten_db": s["min_atten_dB"],
                "pass_max_db": s["pass_max_dB"],
            }
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
