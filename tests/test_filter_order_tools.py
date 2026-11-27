"""滤波器阶数评估工具回归测试。

锚点来自封装后实测（与 demo 脚本头注释的镜像自检互相印证）：
  低通 f_p=1GHz 阻带 2~3GHz@40dB 与高通 f_p=1GHz 阻带 1/3~1/2GHz@40dB 的原型问题
  完全相同（Ω∈[2,3]），应给出相同 N，且零点互为倒数（低通 2.0GHz ↔ 高通 0.5GHz）。
"""
import builtins
import math
import sys

from servers.microwave.filter_order.tools import (
    filter_order_lowpass,
    filter_order_highpass,
    filter_order_bandpass,
    filter_order_bandstop,
    wilkinson_marchand_order,
)


def test_lowpass_order_anchor():
    """f_p=1GHz、2~3GHz@40dB 低通 → N=5、1 对零点@2GHz。"""
    r = filter_order_lowpass(1.0, [[2.0, 3.0, 40.0]])
    assert r["success"] is True
    assert r["N"] == 5
    assert r["notches"] == 1
    assert math.isclose(r["notches_ghz"][0], 2.0, rel_tol=1e-3), r


def test_highpass_mirrors_lowpass():
    """高通（阻带 1/3~1/2GHz）与低通（阻带 2~3GHz）原型相同：N 一致、零点互为倒数。"""
    hp = filter_order_highpass(1.0, [[1.0 / 3.0, 0.5, 40.0]])
    lp = filter_order_lowpass(1.0, [[2.0, 3.0, 40.0]])
    assert hp["success"] is True and lp["success"] is True
    assert hp["N"] == lp["N"]
    assert math.isclose(hp["notches_ghz"][0], 1.0 / lp["notches_ghz"][0], rel_tol=1e-3), (hp, lp)


def test_bandstop_order_and_physical_order():
    """阻带 2.1~2.2GHz 陷波≥20dB、两侧通带≤1dB → 原型 N=3，物理阶数 2N=6。"""
    r = filter_order_bandstop(2.1, 2.2, 20.0, [[2.0, 1.0], [2.3, 1.0]], q_unloaded=500)
    assert r["success"] is True
    assert r["N"] == 3
    assert r["order_bandstop"] == 2 * r["N"]
    assert r["notches"] == 1
    # 陷波频率对落在阻带 [2.1, 2.2] 内
    lo, hi = r["notch_freq_pairs_ghz"][0]
    assert 2.1 <= lo <= hi <= 2.2, r


def test_bandpass_order_anchor():
    """通带 1.95~2.05GHz、左 1.85GHz@35dB、右 2.2GHz@25dB → N=4，无零点最优。"""
    r = filter_order_bandpass(1.95, 2.05, [[1.85, 35.0], [2.20, 25.0]])
    assert r["success"] is True
    assert r["N"] == 4
    assert r["k"] == 0
    assert r["zeros_ghz"] == []
    assert math.isclose(r["f0_ghz"], 2.0, rel_tol=1e-3), r
    # solutions 按 k 递增枚举，k=0/1 均可行且同为 N=4
    assert [s["k"] for s in r["solutions"]] == [0, 1]
    # 枚举在 k=2 处因 k>N/2 停止，stop_note 点名原因
    assert r["stop_note"] and "N/2" in r["stop_note"], r


def test_bandpass_rejects_stopband_in_passband():
    """阻带落在通带内 / f_p2<=f_p1 返回 INVALID_PARAMETERS。"""
    r = filter_order_bandpass(1.95, 2.05, [[2.0, 35.0]])
    assert r["error_code"] == "INVALID_PARAMETERS" and "重叠" in r["message"]
    assert filter_order_bandpass(2.05, 1.95, [[1.85, 35.0]])["error_code"] == "INVALID_PARAMETERS"


def test_bandpass_rejects_empty_and_edge_coincident_stopband():
    """空规范 / 阻带端点恰好落在通带边 → 参数错（不能落到 NO_SOLUTION）。"""
    r = filter_order_bandpass(1.95, 2.05, [])
    assert r["error_code"] == "INVALID_PARAMETERS" and "不能为空" in r["message"]
    for specs, word in (([[1.95, 40.0]], "严格小于"), ([[2.05, 40.0]], "严格大于"),
                        ([[1.80, 1.95, 40.0]], "严格小于")):
        r = filter_order_bandpass(1.95, 2.05, specs)
        assert r["error_code"] == "INVALID_PARAMETERS" and word in r["message"], r


def test_bandpass_warns_far_zeros_and_rejects_reversed_band():
    """逆序抑制频段 → 参数错；零点漂到规范外 / 抑制频段 → warnings 提示；干净算例 warnings 为空。"""
    r = filter_order_bandpass(1.95, 2.05, [[2.30, 2.10, 30.0]])
    assert r["error_code"] == "INVALID_PARAMETERS" and "顺序反了" in r["message"], r

    r = filter_order_bandpass(1.9, 2.1, [[1.80, 1.88, 60.0], [2.12, 2.20, 20.0]])
    assert r["success"] is True, r
    assert any("不在任何阻带规范范围内" in w for w in r["warnings"]), r["warnings"]

    r = filter_order_bandpass(1.95, 2.05, [[1.85, 35.0], [2.20, 25.0]])
    assert r["warnings"] == [], r["warnings"]


def test_bandpass_with_q_insertion_loss_and_verify_rows():
    """含 Q 插损应接近 4.343*N*d_loss（此前 s=0 重归一化把它抵消到≈0）；verify_rows 覆盖每条规范。"""
    specs = [[26.0, 40.0], [27.0, 40.0]]
    r = filter_order_bandpass(26.4, 26.5, specs, q_unloaded=1000)
    assert r["success"] is True, r
    d_loss = 1.0 / (1000.0 * r["fbw"])
    expected = 4.343 * r["N"] * d_loss
    got = r["solutions"][0]["pass_max_db"]
    assert 0.7 * expected <= got <= 1.3 * expected, (got, expected)

    # 无损时 pass_max 应回到纹波量级（不能凭空多出插损）
    r0 = filter_order_bandpass(26.4, 26.5, specs)
    assert abs(r0["solutions"][0]["pass_max_db"] - r0["passband_ripple_db"]) < 1e-3, r0["solutions"][0]

    # verify_rows：每条阻带规范一行，且实测衰减 ≥ 要求
    vr = r["verify_rows"]
    assert len(vr) == len(specs), vr
    assert all(v["atten_db"] >= v["required_db"] for v in vr), vr


def test_bandpass_no_solution_hint_matches_reason():
    """NO_SOLUTION 的 hint 必须与 message 同向：通带波纹超限不得再劝改 max_zeros。"""
    r = filter_order_bandpass(26.4, 26.5, [[26.0, 40.0], [27.0, 40.0]], q_unloaded=500)
    assert r["error_code"] == "NO_SOLUTION" and "通带波纹" in r["message"], r
    assert "提高谐振器 Q" in r["hint"] and "增大 max_zeros 无效" in r["hint"], r["hint"]

    r = filter_order_bandpass(1.95, 2.05, [[2.0501, 40.0]], max_zeros=0)
    assert r["error_code"] == "NO_SOLUTION" and "阻带最紧处" in r["message"], r
    assert "增大 max_zeros" in r["hint"], r["hint"]


def test_wilkinson_marchand_order():
    """频率比 6:1 宽带功分器 → 3 节。"""
    r = wilkinson_marchand_order(6.0)
    assert r["success"] is True
    assert r["order"] == 3


def test_invalid_params_rejected():
    """非法参数返回错误字典而非抛异常。"""
    assert filter_order_lowpass(0.0, [[2.0, 40.0]])["success"] is False
    assert filter_order_lowpass(1.0, [[2.0, 40.0]], rl_db=10.0)["success"] is False
    assert wilkinson_marchand_order(1.0)["success"] is False  # ratio 必须 >1
    # bandstop 通带位置校验在封装层（scipy 不可用也要给出参数错误，别盖成依赖缺失）
    assert filter_order_bandstop(2.1, 2.2, 20, [3.0, 1, 3.5, 1])["error_code"] == "INVALID_PARAMETERS"


def test_lowpass_rejects_invalid_stopband():
    """阻带频率 ≤ f_p、抑制值 ≤0 返回明确错误（非 NO_SOLUTION / numpy 原文）。"""
    r = filter_order_lowpass(1.0, [[0.5, 40.0]])
    assert r["error_code"] == "INVALID_PARAMETERS" and "必须高于通带截止" in r["message"]

    r = filter_order_lowpass(1.0, [[2.0, -40.0]])
    assert r["error_code"] == "INVALID_PARAMETERS" and "大于 0" in r["message"]


def test_scipy_missing_returns_dependency_error(monkeypatch):
    """scipy 缺失时阶数评估返回 DEPENDENCY_MISSING，其余工具不受影响。"""
    real_import = builtins.__import__

    def _no_scipy(name, *a, **kw):
        if name.startswith("scipy"):
            raise ImportError("no scipy")
        return real_import(name, *a, **kw)

    # 清除已缓存的阶数评估模块，让 importlib 重新 import 触发 scipy 缺失
    # 用 monkeypatch.delitem 而非 del：测试结束自动恢复，避免状态泄漏到后续用例
    for mod in [m for m in list(sys.modules)
                if m.startswith("servers.microwave.filter_order.filter_order_estimator")]:
        monkeypatch.delitem(sys.modules, mod)

    monkeypatch.setattr(builtins, "__import__", _no_scipy)
    r = filter_order_lowpass(1.0, [[2.0, 40.0]])
    assert r["error_code"] == "DEPENDENCY_MISSING"
    assert "no scipy" in r["message"]        # 必须把真实 ImportError 原文带出来（打包形态靠它定位）
    assert filter_order_highpass(1.0, [[0.5, 40.0]])["error_code"] == "DEPENDENCY_MISSING"
    assert filter_order_bandstop(2.1, 2.2, 20, [2, 1, 2.3, 1])["error_code"] == "DEPENDENCY_MISSING"
    assert wilkinson_marchand_order(6)["success"] is True   # 不依赖 scipy


def test_all_filter_order_tools_share_zeros_key():
    """四个阶数估算工具统一用 zeros_ghz；带阻是频段对，故 zeros_are_bands=True。"""
    lp = filter_order_lowpass(1.0, [[2.0, 3.0, 40.0]])
    hp = filter_order_highpass(1.0, [[0.4, 0.5, 40.0]])
    bp = filter_order_bandpass(1.8, 2.2, [[1.5, 30.0]])
    bs = filter_order_bandstop(1.8, 2.2, [[1.8, 2.2, 30.0]], [1.5, 3.0, 2.6, 3.0])
    for r in (lp, hp, bp, bs):
        assert "zeros_ghz" in r, r
        assert "zeros_are_bands" in r, r
    assert lp["zeros_are_bands"] is False and bs["zeros_are_bands"] is True
    assert lp["zeros_ghz"] == lp["notches_ghz"]
    assert bs["zeros_ghz"] == bs["notch_freq_pairs_ghz"]
