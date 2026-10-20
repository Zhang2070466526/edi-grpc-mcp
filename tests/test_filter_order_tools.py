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
