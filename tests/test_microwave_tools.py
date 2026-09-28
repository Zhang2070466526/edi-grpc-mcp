"""微波工具回归测试：跨工具一致性 + 已修缺陷的锚点。

背景（2026-09-28 修复，两条都是真调发现的）：

1. ``wilkinson_designer.microstrip_z_and_eeff()`` 的 ``u > 1`` 分支把分母写成
   ``u * 1.393``（Hammerstad/Jensen 应为 ``u + 1.393``），使 ≥1 的低阻段线宽偏宽
   25%~35%（如 er=4.3/h=0.8mm 的 80Ω 臂：1.1172mm，应约 0.6mm）。因为该函数同时
   供二分法求解线宽使用，"用错公式算出的线宽再代回错公式"是自洽的，所以**纯闭环
   断言抓不到**它 —— 必须与独立实现或文献锚点比对（见下面用例 2/3）。

2. ``tools.calculate_transline`` 的分析方向（给 width 算阻抗）没设线长，而模型里
   损耗 = 「每米损耗 × PHYS_LEN」⇒ 分析方向恒回 0（假零）；综合方向因
   ``synthesize()`` 自带 ANG_L=90° 而正常。用例 4 把两个方向钉成同一个数。

容差口径：两个实现的微带闭式不同（``transline.py`` 是 KiCad 移植，含导体厚度/
色散修正；``wilkinson_designer.py`` 是纯 Hammerstad），实测同 Z0 下线宽差 3.5%~5.5%，
故取 8% 上限；锚点值取自修复后的实测（可与 ``calculate_transline`` 互相印证）。
"""
import math

from servers.microwave.tools import calculate_transline, design_wilkinson

ER, H, F = 4.3, 0.8, 2.45


def test_50ohm_microstrip_anchor():
    """文献锚点：0.8mm/er4.3 上 50Ω 微带线宽 ≈ 1.525mm。"""
    r = calculate_transline(transline_type="microstrip", er=ER, freq=F, h=H, z0=50.0)
    assert r["success"] is True
    assert math.isclose(r["width_mm"], 1.5249, rel_tol=0.02), r


def test_wilkinson_widths_agree_with_transline():
    """design_wilkinson 每级线宽必须与 calculate_transline 在同一 Z0 下的线宽同量级。

    修复前 80Ω 臂差 86%、62.5Ω 臂差 44%，本用例会红。
    """
    d = design_wilkinson(h_mm=H, er=ER, f0_ghz=F, n_sections=2)
    assert d["success"] is True
    assert math.isclose(d["arm_z_lines"][0], 79.99440572929922, rel_tol=1e-4)
    for sec in d["sections"]:
        ref = calculate_transline(transline_type="microstrip", er=ER, freq=F,
                                  h=H, z0=sec["z_arm"])
        assert math.isclose(sec["width_mm"], ref["width_mm"], rel_tol=0.08), (sec, ref)


def test_wilkinson_widths_smooth_across_er():
    """εr 微调不得让线宽跳变（修复前 4.3→4.6 出现 47% 跳变：根跨过 u=1 分支）。"""
    widths = [design_wilkinson(h_mm=H, er=er, f0_ghz=F,
                               n_sections=2)["sections"][0]["width_mm"]
              for er in (4.2, 4.3, 4.6)]
    for a, b in zip(widths, widths[1:]):
        assert 0.8 < a / b < 1.25, widths


def test_transline_loss_not_fake_zero():
    """同一几何：给 z0（综合）与给 width（分析）必须报同一个导体损耗，且不为 0。"""
    syn = calculate_transline(transline_type="microstrip", er=ER, freq=F, h=H,
                             z0=79.99440572929922)
    ana = calculate_transline(transline_type="microstrip", er=ER, freq=F, h=H,
                              width=syn["width_mm"])
    assert syn["loss_conductor_db"] > 0.0
    assert math.isclose(ana["loss_conductor_db"], syn["loss_conductor_db"],
                        rel_tol=0.01), (syn, ana)
    assert math.isclose(ana["z0"], 79.99440572929922, rel_tol=1e-4), ana


def test_transline_width_z0_roundtrip():
    """width→z0→width 闭环（含低阻段），防止两个方向再次不对称。"""
    for z0 in (50.0, 62.50437082963006, 79.99440572929922, 100.0):
        w = calculate_transline(transline_type="microstrip", er=ER, freq=F, h=H,
                                z0=z0)["width_mm"]
        back = calculate_transline(transline_type="microstrip", er=ER, freq=F, h=H,
                                   width=w)["z0"]
        assert math.isclose(back, z0, rel_tol=1e-3), (z0, w, back)
