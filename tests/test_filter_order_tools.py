"""滤波器阶数评估工具回归测试。

锚点来自封装后实测（与 demo 脚本头注释的镜像自检互相印证）：
  低通 f_p=1GHz 阻带 2~3GHz@40dB 与高通 f_p=1GHz 阻带 1/3~1/2GHz@40dB 的原型问题
  完全相同（Ω∈[2,3]），应给出相同 N，且零点互为倒数（低通 2.0GHz ↔ 高通 0.5GHz）。
"""
import math

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
