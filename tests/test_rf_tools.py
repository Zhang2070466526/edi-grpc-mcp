"""射频链路计算工具回归测试（算法 + MCP 封装）。

算法期望值来自标准 RF 公式（动态范围）与杂散枚举手算（C++ demo 的 Python 重写对照）。
"""
from __future__ import annotations

import math

from servers.rf_analysis.mixer_spur import analyze_mixer_spur
from servers.rf_analysis.rf_dynamic_range import calculate_rf_dynamic_range


def _near(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=0.0, abs_tol=tol)


def _mixer(rf, relation="low_side", rf_bw=0.0, if_bw=0.0):
    return {"name": "M1", "enabled": True, "relation": relation,
            "rf_center_mhz": rf, "rf_bandwidth_mhz": rf_bw,
            "if_bandwidth_mhz": if_bw}


# ── 动态范围 ─────────────────────────────────────────────────────

def test_dynamic_range_formula():
    # NF=3dB, P1dB=-10dBm, BW=1MHz, SNR=10dB
    # 噪声底=-174+60+3=-111、灵敏度=-101、动态范围=91
    r = calculate_rf_dynamic_range(3.0, -10.0, 1.0, "mhz", 10.0)
    assert r["success"] is True
    assert _near(r["noise_floor_dbm"], -111.0), r
    assert _near(r["sensitivity_dbm"], -101.0), r
    assert _near(r["dynamic_range_db"], 91.0), r
    assert "meets_target" not in r


def test_dynamic_range_target():
    r = calculate_rf_dynamic_range(3.0, -10.0, 1.0, "mhz", 10.0,
                                   target_dynamic_range_db=80.0)
    assert r["meets_target"] is True
    assert _near(r["margin_db"], 11.0), r
    assert _near(r["required_sensitivity_dbm"], -90.0), r


# ── 混频杂散 ─────────────────────────────────────────────────────

def test_spur_no_collision():
    # 接收低边 RF=1000 IF=100 → LO=900，maxOrder=1 无杂散落 IF
    res = analyze_mixer_spur([_mixer(1000.0)], [100.0], max_order=1)
    assert res["success"] is True
    assert res["passed_if_candidates_mhz"] == [100.0], res
    lo = res["candidate_results"][0]["mixer_results"][0]["lo_center_mhz"]
    assert _near(lo, 900.0), res


def test_spur_collision():
    # 接收低边 RF=900 IF=300 → LO=600；(m=1,n=2,sign=-1): 900-1200=-300 → |300|=IF 碰撞
    res = analyze_mixer_spur([_mixer(900.0)], [300.0], max_order=3)
    assert res["success"] is True
    assert res["passed_if_candidates_mhz"] == [], res
    colls = res["candidate_results"][0]["mixer_results"][0]["collisions"]
    assert any(c["m"] == 1 and c["n"] == 2 and c["sign"] == -1 for c in colls), colls


def test_spur_negative_lo():
    # 发射 diff_if：LO=IF-RF，RF=1000 IF=100 → LO=-900 < 0
    res = analyze_mixer_spur([_mixer(1000.0, relation="diff_if")], [100.0],
                             max_order=2, direction="transmit")
    mr = res["candidate_results"][0]["mixer_results"][0]
    assert mr["error"] == "目标变频关系产生了负 LO 频率" and mr["passed"] is False, mr


def test_spur_range_merge():
    res = analyze_mixer_spur([_mixer(1000.0)], [100.0, 101.0, 102.0, 200.0],
                             max_order=1, if_candidate_step_mhz=1.0)
    assert res["passed_if_ranges_mhz"] == [(100.0, 102.0), (200.0, 200.0)], res


def test_invalid_params():
    assert analyze_mixer_spur([], [100.0])["success"] is False
    assert analyze_mixer_spur([_mixer(1000.0)], [])["success"] is False
    assert analyze_mixer_spur([_mixer(1000.0)], [100.0], direction="bad")["success"] is False
    assert calculate_rf_dynamic_range(-1.0, 0.0, 1.0, "mhz")["success"] is False  # NF 负


# ── 入参归一化与校验（缺省默认值 / relation / 升序）──────────────

def test_spur_missing_fields_use_documented_defaults():
    # 只给 rf_center：其余按说明文档默认值补全，并逐条写进 warnings（不静默）
    res = analyze_mixer_spur([{"name": "M1", "rf_center_mhz": 1000.0}], [100.0], max_order=1)
    assert res["success"] is True, res
    mixer = res["candidate_results"][0]["mixer_results"][0]["mixer"]
    assert mixer["relation"] == "low_side"
    assert _near(mixer["rf_bandwidth_mhz"], 0.0)
    assert _near(mixer["if_bandwidth_mhz"], 1.0)
    assert any("rf_bandwidth_mhz" in w for w in res["warnings"]), res["warnings"]
    assert any("relation" in w for w in res["warnings"]), res["warnings"]
    # 功率字段只展示不参与计算，不进 warnings
    assert not any("power_dbm" in w for w in res["warnings"]), res["warnings"]


def test_spur_relation_must_match_direction():
    # 接收方向给发射专用 relation → 参数错误（不再误报"负 LO 频率"）
    res = analyze_mixer_spur([_mixer(1000.0, relation="sum")], [100.0])
    assert res["success"] is False
    assert res["error_code"] == "INVALID_PARAMETERS"
    assert "relation" in res["message"], res


def test_spur_candidates_must_be_strictly_ascending():
    assert analyze_mixer_spur([_mixer(1000.0)], [200.0, 100.0])["success"] is False
    dup = analyze_mixer_spur([_mixer(1000.0)], [100.0, 100.0])
    assert dup["success"] is False and "升序" in dup["message"], dup
    chart = analyze_mixer_spur([_mixer(1000.0)], [100.0],
                               chart_if_candidates_mhz=[300.0, 200.0])
    assert chart["success"] is False and "chart_if_candidates_mhz" in chart["message"], chart


def test_spur_mixer_field_validation():
    bad = analyze_mixer_spur([{"name": "M1", "rf_center_mhz": "1GHz"}], [100.0])
    assert bad["success"] is False and "rf_center_mhz" in bad["message"], bad
    neg = analyze_mixer_spur([{"name": "M1", "rf_center_mhz": 1000.0,
                              "if_bandwidth_mhz": -1.0}], [100.0])
    assert neg["success"] is False and "if_bandwidth_mhz" in neg["message"], neg


def test_spur_disabled_mixer_tolerated():
    # enabled=false 的 Mixer 字段非法也不陪葬整批
    res = analyze_mixer_spur(
        [{"name": "BAD", "enabled": False, "relation": "sum", "rf_center_mhz": "x"},
         _mixer(1000.0)], [100.0], max_order=1)
    assert res["success"] is True, res
    assert res["enabled_mixer_count"] == 1, res


def test_spur_relation_default_follows_direction():
    # 发射方向省略 relation → 缺省 sum（与 C++ GUI 切方向后的默认项一致），LO = RF − IF
    res = analyze_mixer_spur([{"name": "M1", "rf_center_mhz": 30000.0}], [1000.0],
                             max_order=1, direction="transmit")
    assert res["success"] is True, res
    mr = res["candidate_results"][0]["mixer_results"][0]
    assert mr["mixer"]["relation"] == "sum", mr["mixer"]
    assert _near(mr["lo_center_mhz"], 29000.0), mr
    assert any("relation" in w and "transmit" in w for w in res["warnings"]), res["warnings"]


def test_spur_disabled_relation_falls_back_to_direction_default():
    # 禁用 Mixer 的非法 relation 回落到当前方向缺省值（transmit→sum），不陪葬整批
    res = analyze_mixer_spur(
        [{"name": "BAD", "enabled": False, "relation": "low_side", "rf_center_mhz": 30000.0},
         {"name": "OK", "relation": "sum", "rf_center_mhz": 30000.0}],
        [1000.0], max_order=1, direction="transmit")
    assert res["success"] is True, res
    assert res["enabled_mixer_count"] == 1, res


def test_spur_rf_center_must_be_positive():
    res = analyze_mixer_spur([{"name": "M1", "rf_center_mhz": 0.0}], [100.0])
    assert res["success"] is False and "正数" in res["message"], res


def test_spur_enabled_must_be_bool():
    res = analyze_mixer_spur([{"name": "M1", "enabled": "false", "rf_center_mhz": 1000.0}],
                             [100.0])
    assert res["success"] is False and "布尔" in res["message"], res


def test_spur_lo_leakage_reported_for_both_signs_like_cpp():
    """C++ 口径锁定：absoluteRange 把 ±LO 折成同一区间，m=0,n=1 会各出一条同范围碰撞，勿去重。

    对照 servers/c++demo/.../mixerspuranalyzer.cpp 的 absoluteRange + analyzeMixer：
    RF=1000、IF=500（带宽全 0）→ LO=500 恰好落在 IF 带内，sign=-1/+1 都命中同一区间 [500,500]。
    """
    res = analyze_mixer_spur(
        [{"name": "M1", "relation": "low_side", "rf_center_mhz": 1000.0,
          "rf_bandwidth_mhz": 0.0, "if_bandwidth_mhz": 0.0}], [500.0], max_order=1)
    mr = res["candidate_results"][0]["mixer_results"][0]
    got = {(c["m"], c["n"], c["sign"], c["min_mhz"], c["max_mhz"]) for c in mr["collisions"]}
    assert got == {(0, 1, -1, 500.0, 500.0), (0, 1, 1, 500.0, 500.0)}, got
    assert mr["passed"] is False
