"""混频杂散分析 —— C++ mixerspuranalyzer.cpp 的纯 Python 重写。

只重写核心算法（analyze / analyzeMixer / mergeRanges），不涉及 Qt 界面层。
数据结构用 dict/list 替代 C++ 的 QString/QVector/QPair。

MCP 封装层（`analyze_mixer_spur`）额外做入参归一化与校验：按说明文档补默认值并写 warnings、
relation 与 direction 匹配校验、IF 候选点严格升序校验。**这些是 C++ GUI 靠界面约束、analyzer 本身
不做的守卫；算法层（analyze / analyzeMixer / mergeRanges / absoluteRange / isDesiredMixingProduct）
与 mixerspuranalyzer.cpp 逐行一致，不要为了"结果更好看"改它**——包括 `absoluteRange` 把 ±LO 折叠成
同一区间导致 `m=0,n=1` 时 sign=±1 各报一条同范围碰撞（C++ 原版行为）。

字段命名：C++ 的 camelCase 转 snake_case（rfCenterMHz → rf_center_mhz）。
direction/relation 用字符串："receive"/"transmit"、"low_side"/"high_side"/"sum"/"diff_lo"/"diff_if"。
"""
from __future__ import annotations

import math
from typing import Any

from servers import mcp
from servers.utils import error_response

_DIRECTIONS = ("receive", "transmit")

_RECEIVE_RELATIONS = ("low_side", "high_side")
_TRANSMIT_RELATIONS = ("sum", "diff_lo", "diff_if")

# 单个 Mixer 的缺省值，与「混频杂散分析说明.md」一致（对应 C++ MixerSpurInput 的成员初值）。
# relation 不在此表：它的缺省随 direction 走（receive→low_side、transmit→sum），见 _normalize_mixers。
_MIXER_DEFAULTS: dict[str, Any] = {
    "name": "",
    "enabled": True,
    "rf_center_mhz": 1000.0,
    "rf_bandwidth_mhz": 0.0,
    "if_bandwidth_mhz": 1.0,
    "rf_power_dbm": -10.0,
    "lo_power_dbm": 7.0,
}
_MIXER_FREQ_FIELDS = ("rf_center_mhz", "rf_bandwidth_mhz", "if_bandwidth_mhz")
_MIXER_POWER_FIELDS = ("rf_power_dbm", "lo_power_dbm")
# 频率字段里必须严格 > 0 的只有 rf_center；rf/if 带宽允许 0（单频点）
_MIXER_FREQ_STRICT_POSITIVE = ("rf_center_mhz",)


# ── 辅助函数（对应 .cpp 的匿名 namespace）──────────────────────────

def _ranges_overlap(a_min: float, a_max: float, b_min: float, b_max: float) -> bool:
    return a_max >= b_min and a_min <= b_max


def _absolute_range(values: list[float]) -> tuple[float, float]:
    src_min, src_max = min(values), max(values)
    if src_min <= 0.0 and src_max >= 0.0:
        return 0.0, max(abs(src_min), abs(src_max))
    return min(abs(src_min), abs(src_max)), max(abs(src_min), abs(src_max))


def _calculate_lo(rf_mhz: float, if_mhz: float, direction: str, relation: str) -> float:
    if direction == "receive":
        if relation == "low_side":
            return rf_mhz - if_mhz
        if relation == "high_side":
            return rf_mhz + if_mhz
        return -1.0
    # transmit
    if relation == "sum":
        return rf_mhz - if_mhz
    if relation == "diff_lo":
        return rf_mhz + if_mhz
    if relation == "diff_if":
        return if_mhz - rf_mhz
    return -1.0


def _is_desired_product(m: int, n: int, sign: int, direction: str, relation: str) -> bool:
    """目标混频产物本身（应排除）：接收 |RF-LO|、发射和频 IF+LO、发射差频 |IF-LO|。"""
    if m != 1 or n != 1:
        return False
    if direction == "receive":
        return sign == -1
    if relation == "sum":
        return sign == 1
    return sign == -1


# ── 单 Mixer 分析（对应 analyzeMixer）──────────────────────────────

def _analyze_mixer(mixer: dict, if_center_mhz: float, max_order: int,
                   direction: str) -> dict:
    result: dict = {
        "mixer": dict(mixer),
        "passed": False,
        "error": "",
        "lo_center_mhz": 0.0,
        "collisions": [],
    }
    if max_order < 1:
        result["error"] = "最大杂散阶次必须大于 0"
        return result

    rf_min = mixer["rf_center_mhz"] - mixer["rf_bandwidth_mhz"] / 2.0
    rf_max = mixer["rf_center_mhz"] + mixer["rf_bandwidth_mhz"] / 2.0
    if_min = if_center_mhz - mixer["if_bandwidth_mhz"] / 2.0
    if_max = if_center_mhz + mixer["if_bandwidth_mhz"] / 2.0

    if rf_min < 0.0 or if_min <= 0.0:
        result["error"] = "RF 或 IF 频率范围无效"
        return result

    result["lo_center_mhz"] = _calculate_lo(
        mixer["rf_center_mhz"], if_center_mhz, direction, mixer["relation"])
    if result["lo_center_mhz"] < 0.0:
        result["error"] = "目标变频关系产生了负 LO 频率"
        return result

    # 目标输出频带：接收模式 = IF 频带，发射模式 = RF 频带
    target_min = if_min if direction == "receive" else rf_min
    target_max = if_max if direction == "receive" else rf_max

    for m in range(max_order + 1):
        for n in range(max_order + 1):
            if m == 0 and n == 0:
                continue
            if m + n > max_order:
                continue
            for sign in (-1, 1):
                if _is_desired_product(m, n, sign, direction, mixer["relation"]):
                    continue
                spur_values: list[float] = []
                for rf in (rf_min, rf_max):
                    for cur_if in (if_min, if_max):
                        lo = _calculate_lo(rf, cur_if, direction, mixer["relation"])
                        if lo < 0.0:
                            continue
                        source = rf if direction == "receive" else cur_if
                        spur_values.append(m * source + sign * n * lo)
                if not spur_values:
                    continue
                spur_min, spur_max = _absolute_range(spur_values)
                if _ranges_overlap(spur_min, spur_max, target_min, target_max):
                    result["collisions"].append({
                        "m": m, "n": n, "sign": sign,
                        "min_mhz": spur_min, "max_mhz": spur_max,
                    })

    result["passed"] = (not result["error"]) and (not result["collisions"])
    return result


def _merge_ranges(values: list[float], step: float) -> list[tuple[float, float]]:
    """把升序的连续通过点合并成频率范围（对应 mergeRanges，不排序不去重）。"""
    ranges: list[tuple[float, float]] = []
    if not values:
        return ranges
    range_start = values[0]
    previous = values[0]
    epsilon = max(1e-9, step * 1.01)
    for current in values[1:]:
        if current - previous <= epsilon:
            previous = current
            continue
        ranges.append((range_start, previous))
        range_start = current
        previous = current
    ranges.append((range_start, previous))
    return ranges


# ── 入参归一化（MCP 封装层：按说明文档补默认值 + 校验）─────────────

def _normalize_mixers(mixers: list[dict], direction: str) -> tuple[list[dict] | None, list[str], str]:
    """按「混频杂散分析说明.md」补全单个 Mixer 的缺省字段并校验。

    返回 (归一化后的列表, 缺省字段提示, 错误文案)；出错时列表为 None。
    `enabled=false` 的 Mixer 放宽校验（非法字段回落到默认值），避免陪葬整批分析。
    """
    allowed = _RECEIVE_RELATIONS if direction == "receive" else _TRANSMIT_RELATIONS
    # 与 C++ GUI 一致（切方向后下拉框重建、setCurrentIndex(0)）：receive→low_side、transmit→sum
    default_relation = allowed[0]
    normalized: list[dict] = []
    notes: list[str] = []

    for index, mixer in enumerate(mixers):
        enabled_raw = mixer.get("enabled", _MIXER_DEFAULTS["enabled"])
        if not isinstance(enabled_raw, bool):
            return None, [], f"mixers[{index}].enabled 必须是布尔值（收到 {enabled_raw!r}）"
        enabled = enabled_raw
        relation_provided = "relation" in mixer
        relation_raw = mixer.get("relation", default_relation)
        relation = str(relation_raw or default_relation).lower()
        if relation not in allowed:
            if enabled:
                return None, [], (
                    f"mixers[{index}].relation={relation_raw!r} 不合法：direction={direction} 时"
                    f"只能是 {' / '.join(allowed)}")
            relation = default_relation  # disabled 时回落到当前方向的缺省关系，不落非法值

        item: dict[str, Any] = {
            "name": str(mixer.get("name", _MIXER_DEFAULTS["name"])),
            "enabled": enabled,
            "relation": relation,
        }
        if enabled and not relation_provided:
            notes.append(f"mixers[{index}] 未提供 relation，按 direction={direction} 的缺省关系 "
                         f"{default_relation} 计算")
        for field in _MIXER_FREQ_FIELDS + _MIXER_POWER_FIELDS:
            default = _MIXER_DEFAULTS[field]
            raw = mixer.get(field, default)
            value = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
            ok = value is not None and math.isfinite(value)
            if ok and field in _MIXER_FREQ_STRICT_POSITIVE:
                ok = value > 0.0
            elif ok and field in _MIXER_FREQ_FIELDS:
                ok = value >= 0.0
            if not ok:
                if enabled:
                    rule = ("正数" if field in _MIXER_FREQ_STRICT_POSITIVE
                            else "非负" if field in _MIXER_FREQ_FIELDS
                            else "有限")
                    return None, [], f"mixers[{index}].{field} 必须是{rule}数值（收到 {raw!r}）"
                item[field] = float(default)
                continue
            item[field] = value
            # 只对参与计算的频率字段喊话；功率字段仅展示、不参与碰撞计算，不刷 warnings
            if field in _MIXER_FREQ_FIELDS and field not in mixer:
                notes.append(f"mixers[{index}] 未提供 {field}，按说明文档默认值 {default} 计算")
        normalized.append(item)

    return normalized, notes, ""


def _mixer_error_warnings(mixers: list[dict], if_candidates: list[float],
                          max_order: int, direction: str) -> list[str]:
    """`require_all_mixers=False` 的坑：`any_of(passed)` 会把「算不出来」的 Mixer 当成没通过。

    C++ 语义如此（判定不改），但这种 Mixer 根本没参与碰撞计算，必须显式喊出来，
    免得调用方把"某路参数错/负 LO"误读成"该路无杂散"。
    """
    warnings: list[str] = []
    for index, mixer in enumerate(mixers):
        errors = sorted({r["error"] for r in
                         (_analyze_mixer(mixer, c, max_order, direction) for c in if_candidates)
                         if r["error"]})
        if errors:
            warnings.append(
                f"mixers[{index}]（{mixer.get('name') or '未命名'}）在部分候选点计算失败："
                f"{'；'.join(errors)}——require_all_mixers=false 时按「不通过」计入判断，"
                f"别误当它通过了")
    return warnings


# ── 批量分析（对应 analyze）───────────────────────────────────────

def _analyze(input_data: dict) -> dict:
    """批量混频杂散分析。输入/输出字段见「混频杂散分析说明.md」。"""
    result: dict = {
        "valid": False,
        "error": "",
        "direction": input_data.get("direction", "receive"),
        "require_all_mixers": input_data.get("require_all_mixers", True),
        "enabled_mixer_count": 0,
        "if_candidates_mhz": list(input_data.get("if_candidates_mhz", [])),
        "passed_if_candidates_mhz": [],
        "passed_if_ranges_mhz": [],
        "candidate_results": [],
        "chart_passed_if_candidates_mhz": [],
        "chart_passed_if_ranges_mhz": [],
    }
    max_order = input_data.get("max_order", 1)
    if_candidates = result["if_candidates_mhz"]
    chart_candidates = list(input_data.get("chart_if_candidates_mhz", []))
    if_candidate_step = input_data.get("if_candidate_step_mhz", 1.0)
    chart_candidate_step = input_data.get("chart_candidate_step_mhz", 1.0)
    direction = result["direction"]
    require_all = result["require_all_mixers"]
    maximum_detail = input_data.get("maximum_detail_count", 12)

    if max_order < 1:
        result["error"] = "最大杂散阶次必须大于 0"
        return result
    if not if_candidates:
        result["error"] = "IF 候选点不能为空"
        return result
    if not math.isfinite(if_candidate_step) or if_candidate_step <= 0.0:
        result["error"] = "主 IF 扫描步长必须大于 0"
        return result
    if chart_candidates and (not math.isfinite(chart_candidate_step) or chart_candidate_step <= 0.0):
        result["error"] = "图表 IF 扫描步长必须大于 0"
        return result

    enabled_mixers = [m for m in input_data.get("mixers", []) if m.get("enabled", True)]
    result["enabled_mixer_count"] = len(enabled_mixers)
    if not enabled_mixers:
        result["error"] = "至少启用一个 Mixer"
        return result

    def _analyze_candidate(if_center: float) -> dict:
        mixer_results = [
            _analyze_mixer(m, if_center, max_order, direction) for m in enabled_mixers
        ]
        if require_all:
            passed = all(r["passed"] for r in mixer_results)
        else:
            passed = any(r["passed"] for r in mixer_results)
        return {"if_center_mhz": if_center, "passed": passed,
                "mixer_results": mixer_results}

    for if_center in if_candidates:
        candidate = _analyze_candidate(if_center)
        if candidate["passed"]:
            result["passed_if_candidates_mhz"].append(if_center)
        retain = maximum_detail < 0 or len(result["candidate_results"]) < maximum_detail
        if retain:
            result["candidate_results"].append(candidate)

    for if_center in chart_candidates:
        if _analyze_candidate(if_center)["passed"]:
            result["chart_passed_if_candidates_mhz"].append(if_center)

    result["passed_if_ranges_mhz"] = _merge_ranges(
        result["passed_if_candidates_mhz"], if_candidate_step)
    result["chart_passed_if_ranges_mhz"] = _merge_ranges(
        result["chart_passed_if_candidates_mhz"], chart_candidate_step)
    result["valid"] = True
    return result


@mcp.tool()
def analyze_mixer_spur(
    mixers: list,
    if_candidates_mhz: list,
    max_order: int = 1,
    direction: str = "receive",
    require_all_mixers: bool = True,
    chart_if_candidates_mhz: list | None = None,
    if_candidate_step_mhz: float = 1.0,
    chart_candidate_step_mhz: float = 1.0,
    maximum_detail_count: int = 12,
) -> dict[str, Any]:
    """混频杂散分析：枚举 m×源频 ± n×LO 的杂散组合，判断是否落入目标输出频带。

    用法："分析这个混频器在 IF 100~200MHz 范围内哪些候选点没有杂散碰撞"

    算法：对每个 IF 候选点算 LO，枚举 |m|·源频 ± |n|·LO（m,n≥0，m+n≤max_order），
    排除目标混频产物本身，杂散频率范围与目标输出频带（接收=IF、发射=RF）重叠即碰撞。

    Args:
        mixers: Mixer 数组（至少一个 enabled），每项 {name, enabled, relation, rf_center_mhz,
            rf_bandwidth_mhz, if_bandwidth_mhz, rf_power_dbm, lo_power_dbm}；
            relation 取 low_side/high_side（接收）或 sum/diff_lo/diff_if（发射），
            缺省随 direction 走（接收=low_side、发射=sum，与 C++ GUI 切方向后的默认项一致）。
            其余缺省字段按说明文档补全（rf_center_mhz 1000、rf_bandwidth_mhz 0、
            if_bandwidth_mhz 1、rf_power_dbm -10、lo_power_dbm 7）；补了哪些字段会写进
            返回的 warnings。`rf_center_mhz` 必须 > 0、`enabled` 必须是布尔值；
            relation 显式传了与 direction 不匹配的值才报错。
        if_candidates_mhz: 主 IF 候选点数组（MHz，非空、严格升序，重复或乱序报错）。
        max_order: 最大杂散阶次（m+n≤max_order），默认 1。
        direction: receive（杂散源=RF、输出=IF）或 transmit（杂散源=IF、输出=RF）。
        require_all_mixers: 多 Mixer 判定规则，True=全部通过才通过、False=任一通过即通过。
        chart_if_candidates_mhz: 图表扫描点（可选，只收集通过点与范围、不存逐点明细）。
        if_candidate_step_mhz: 合并主通过范围用的步长（MHz），默认 1。
        chart_candidate_step_mhz: 合并图表范围用的步长（MHz），默认 1。
        maximum_detail_count: 保留明细的候选点数量，默认 12（<0=全部、0=不保留）。

    Returns:
        {"valid": true, "enabled_mixer_count": N, "passed_if_candidates_mhz": [...],
         "passed_if_ranges_mhz": [[起,止],...], "candidate_results": [...],
         "chart_passed_if_candidates_mhz": [...], "chart_passed_if_ranges_mhz": [...],
         "detail_truncated": false, ["warnings": [...]]}
        `detail_truncated=true` 只在 `maximum_detail_count > 0` 且明细没放全时出现（同时给一条 warning）；
        `0` 是「按请求不保存明细」（C++ 口径），不算截断、不报警；`<0` 全留。
        明细不全时 passed_if_candidates_mhz 仍然完整；要全明细传 maximum_detail_count=-1。
        另有两类诊断提示（只写 warnings，不改判定）：require_all_mixers=false 时点名哪几路 Mixer
        计算失败（负 LO / 频率范围无效，它们按"不通过"计入，别误当该路无杂散）；
        扫描步长小于候选点最小间隔时提示「不会合并成连续区间」。
        candidate_results 每项 {if_center_mhz, passed, mixer_results}，
        mixer_results 每项 {lo_center_mhz, passed, error, collisions[{m,n,sign,min_mhz,max_mhz}]}。
    """
    if not isinstance(mixers, list) or not mixers:
        return error_response("INVALID_PARAMETERS", "mixers 必须是非空数组")
    if not all(isinstance(m, dict) for m in mixers):
        return error_response("INVALID_PARAMETERS", "mixers 每项必须是对象")
    if not isinstance(if_candidates_mhz, list) or not if_candidates_mhz:
        return error_response("INVALID_PARAMETERS", "if_candidates_mhz 必须是非空数组")
    if not all(isinstance(x, (int, float)) for x in if_candidates_mhz):
        return error_response("INVALID_PARAMETERS", "if_candidates_mhz 每项必须是数值")
    if_candidates = list(if_candidates_mhz)
    chart_candidates = list(chart_if_candidates_mhz or [])
    if not all(isinstance(x, (int, float)) for x in chart_candidates):
        return error_response("INVALID_PARAMETERS", "chart_if_candidates_mhz 每项必须是数值")
    direction = (direction or "receive").lower()
    if direction not in _DIRECTIONS:
        return error_response("INVALID_PARAMETERS",
                              f"direction 必须是 {', '.join(_DIRECTIONS)} 之一")
    # merge_ranges 不排序不去重，乱序输入会静默算出错误范围，这里直接拦
    for label, values in (("if_candidates_mhz", if_candidates),
                          ("chart_if_candidates_mhz", chart_candidates)):
        if any(b <= a for a, b in zip(values, values[1:])):
            return error_response(
                "INVALID_PARAMETERS",
                f"{label} 必须严格升序且不含重复点（范围合并不排序不去重），"
                f"当前前几个值为 {values[:5]}")

    normalized_mixers, notes, mixer_error = _normalize_mixers(mixers, direction)
    if normalized_mixers is None:
        return error_response("INVALID_PARAMETERS", mixer_error)

    result = _analyze({
        "mixers": normalized_mixers,
        "if_candidates_mhz": if_candidates,
        "chart_if_candidates_mhz": chart_candidates,
        "if_candidate_step_mhz": if_candidate_step_mhz,
        "chart_candidate_step_mhz": chart_candidate_step_mhz,
        "max_order": max_order,
        "direction": direction,
        "require_all_mixers": bool(require_all_mixers),
        "maximum_detail_count": maximum_detail_count,
    })
    if not result["valid"]:
        return error_response("INVALID_PARAMETERS", result["error"])
    # 以下两条只是诊断提示，判定仍按 C++ 语义（any_of / mergeRanges 不改）
    if not require_all_mixers:
        notes.extend(_mixer_error_warnings(normalized_mixers, if_candidates, max_order, direction))
    for label, values, step in (("if_candidates_mhz", if_candidates, if_candidate_step_mhz),
                                ("chart_if_candidates_mhz", chart_candidates, chart_candidate_step_mhz)):
        # 只在「像在做扫描」（≥5 个点）且步长够不到下一点时提示；手挑 2~3 个稀疏点不啰嗦
        if len(values) >= 5:
            gap = min(b - a for a, b in zip(values, values[1:]))
            if step * 1.01 < gap:
                notes.append(f"{label} 的扫描步长 {step} 小于候选点最小间隔 {gap}："
                             f"merge_ranges 容差为 step×1.01，不会合并成连续区间（会按点各成一段）")
    # 明细截断要喊出来：candidate_results 受 maximum_detail_count 限制，别让调用方以为拿全了。
    # C++ 口径（混频杂散计算.txt 115-123 行）：<0=全留、0=按请求不保存、>0=最多保存 N 条；
    # 因此只有「给了正的上限但没放全」才算截断，0 是调用方显式要求，不报警。
    total = len(result["if_candidates_mhz"])
    retained = len(result["candidate_results"])
    result["detail_truncated"] = maximum_detail_count > 0 and retained < total
    if result["detail_truncated"]:
        notes.append(f"明细被截断：{total} 个候选点只回前 {retained} 个的 collisions 明细"
                     f"（maximum_detail_count={maximum_detail_count}）；要全部明细传 -1")
    if notes:
        result["warnings"] = notes
    result["success"] = True
    return result