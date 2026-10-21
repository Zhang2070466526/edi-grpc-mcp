"""失败诊断：把 ADS/HSPICE 原文带进返回（而不是只留 exe 的归纳结论）。

背景（2026-10-21 真实案例）：ADS 报
    "The bin file of the model are incorrect" / "User-compiled model's analyze_nl
     function returned error"（模型 DLL 与 ADS 版本不匹配），
exe 却归纳成「模型 .ds 文件缺失，需 open_edi_project 补模型后重跑」——按那句去补 .ds 是白忙。
"""

from __future__ import annotations

from servers.simulation_tool.mixer_simulation import (
    _MAX_DIAG_CONDITIONS,
    _collect_ads_diagnostics,
)

_ADS_ERROR = """hpeesofsim (*) 512.update2.0 Feb 27 2020, MINT version 5
Parsing the netlist ...
Warning detected by hpeesofsim during circuit set up.
    The bin file of the model are incorrect. Please ensure to use the latest version of the model.
Error detected by hpeesofsim in AmplifierDevice during circuit set up.
    User-compiled model's analyze_nl function returned error"""

_ADS_OK = """hpeesofsim (*) 512.update2.0 Feb 27 2020, MINT version 5
Parsing the netlist ...
Simulation finished."""


def _make_run(tmp_path, *, failed: int = 2, ok: int = 1):
    root = tmp_path / "simulate_1_1"
    root.mkdir()
    (root / "simulation.log").write_text("步骤开始：工况执行\n重试工况 变频增益 35GHz\n", encoding="utf-8")
    conds = root / "L1" / "conditions"
    for i in range(failed):
        d = conds / f"CH1_变频增益_{33 + i}GHz"
        d.mkdir(parents=True)
        (d / "ads_output.log").write_text(_ADS_ERROR, encoding="utf-8")
    for i in range(ok):
        d = conds / f"CH1_变频增益_OK{i}"
        d.mkdir(parents=True)
        (d / "ads_output.log").write_text(_ADS_OK, encoding="utf-8")
    return root


def test_extracts_ads_error_text(tmp_path):
    d = _collect_ads_diagnostics(str(_make_run(tmp_path)))
    assert d["ads_output"], "必须带回 ADS 原文"
    assert "analyze_nl function returned error" in d["ads_output"][0]["ads_output"]
    # first_error 取第一处真因（含其缩进续行）；完整原文仍在 ads_output 里
    assert "bin file of the model are incorrect" in d["first_error"]
    assert "工况执行" in d["simulation_log_tail"]


def test_skips_successful_conditions(tmp_path):
    d = _collect_ads_diagnostics(str(_make_run(tmp_path, failed=1, ok=3)))
    names = [c["condition"] for c in d["ads_output"]]
    assert names == ["CH1_变频增益_33GHz"], names


def test_caps_number_of_conditions(tmp_path):
    d = _collect_ads_diagnostics(str(_make_run(tmp_path, failed=6)))
    assert len(d["ads_output"]) == _MAX_DIAG_CONDITIONS


def test_missing_or_empty_path_is_safe(tmp_path):
    assert _collect_ads_diagnostics("") == {}
    assert _collect_ads_diagnostics(str(tmp_path / "nope")) == {}
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _collect_ads_diagnostics(str(empty)) == {}


def test_hint_points_at_diagnostics():
    """失败信封的 hint 要指向 details.ads_output（agent 照着看就行）。"""
    import inspect

    from servers.simulation_tool import mixer_simulation

    src = inspect.getsource(mixer_simulation.run_mixer_simulation.fn
                            if hasattr(mixer_simulation.run_mixer_simulation, "fn")
                            else mixer_simulation.run_mixer_simulation)
    assert "details.ads_output" in src
