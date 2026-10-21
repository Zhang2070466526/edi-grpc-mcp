"""仿真产物收集（artifacts / report_artifacts）回归测试。

背景（2026-10-21 实测）：SimulationTool 每次跑完都会在结果目录顶层生成
``<工程名>_仿真报告.pdf`` / ``.docx``，但明细文件在 ``<链路>/conditions/<工况>/``
子目录里。旧实现按路径字符串排序后截断 40 条 —— 子目录先把名额吃满，报告被
静默截断，调用方完全不知道报告已生成。这里锁住「报告必须排最前 + 单独返回」。
"""

from __future__ import annotations

from pathlib import Path

from servers.utils import SIM_REPORT_SUFFIXES, collect_simulation_artifacts

_KEY_EXT = {".csv": "csv", ".png": "image", ".raw": "raw", ".log": "log",
            ".pdf": "pdf", ".docx": "docx"}


def _make_result_tree(root: Path, condition_count: int = 60) -> Path:
    """造一个像真实仿真输出的目录：顶层有报告 + 明细工况里有大量 raw/log。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "SIPFC_仿真报告.pdf").write_bytes(b"%PDF-1.4 stub")
    (root / "SIPFC_仿真报告.docx").write_bytes(b"PK stub")
    (root / "schematic.png").write_bytes(b"\x89PNG stub")
    (root / "09_指标判定表.md").write_text("table", encoding="utf-8")

    conditions = root / "Ka双通道下变频" / "conditions"
    for i in range(condition_count):
        cond = conditions / f"CH1_变频增益_{i}GHz"
        (cond / "history").mkdir(parents=True)
        (cond / "history" / "result.raw").write_bytes(b"raw")
        (cond / "netlist.log").write_text("netlist", encoding="utf-8")
    return root


def test_reports_come_first_and_are_not_truncated(tmp_path):
    root = _make_result_tree(tmp_path / "simulate_1")

    artifacts, report_artifacts, truncated = collect_simulation_artifacts(
        str(root), "run_mixer_simulation", _KEY_EXT, max_artifacts=40)

    # 报告单独给出，且两类都齐
    names = [a["name"] for a in report_artifacts]
    assert names == ["SIPFC_仿真报告.pdf", "SIPFC_仿真报告.docx"]

    # 明细远超 40 个 ⇒ 必然截断，但报告必须在 artifacts 里（旧实现会丢）
    assert truncated is True
    artifact_names = [a["name"] for a in artifacts]
    assert "SIPFC_仿真报告.pdf" in artifact_names
    assert "SIPFC_仿真报告.docx" in artifact_names
    # 第 0 项是目录，紧随其后就是两份报告
    assert artifacts[0]["type"] == "directory"
    assert artifact_names[1:3] == ["SIPFC_仿真报告.pdf", "SIPFC_仿真报告.docx"]


def test_shallow_files_precede_deep_ones(tmp_path):
    root = _make_result_tree(tmp_path / "simulate_2", condition_count=2)

    artifacts, _, _ = collect_simulation_artifacts(
        str(root), "run_mixer_simulation", _KEY_EXT)

    # schematic.png（顶层）应排在 conditions/*/history/result.raw（深层）之前
    names = [a["name"] for a in artifacts]
    assert names.index("schematic.png") < names.index("result.raw")


def test_tr_style_key_ext_without_report_suffix(tmp_path):
    """TR 的旧 _KEY_EXT 不含 .pdf/.docx —— 补进去之前报告永远不会出现在 artifacts。"""
    root = _make_result_tree(tmp_path / "simulate_3", condition_count=1)
    legacy = {".csv": "csv", ".png": "image", ".raw": "raw", ".log": "log"}

    artifacts, report_artifacts, _ = collect_simulation_artifacts(
        str(root), "run_tr_simulation", legacy)

    assert report_artifacts == []
    assert all(a["type"] != "pdf" for a in artifacts)

    artifacts, report_artifacts, _ = collect_simulation_artifacts(
        str(root), "run_tr_simulation", _KEY_EXT)
    assert len(report_artifacts) == 2


def test_missing_or_empty_path_is_safe(tmp_path):
    for bad in ("", str(tmp_path / "not_exist")):
        artifacts, report_artifacts, truncated = collect_simulation_artifacts(
            bad, "run_mixer_simulation", _KEY_EXT)
        assert artifacts == [] and report_artifacts == [] and truncated is False


def test_report_suffixes_constant_covers_both_formats():
    assert ".pdf" in SIM_REPORT_SUFFIXES
    assert ".docx" in SIM_REPORT_SUFFIXES
