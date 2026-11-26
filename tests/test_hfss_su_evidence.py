"""HFSS 求解证据判定：*_SU.txt 的 Success 1 + mtime 过滤 + 导出预检。"""
import os
import time

from servers.ansys.run_analysis import _hfss_su_evidence, _solved_evidence_or_error


def _mk(tmp_path, design="HFSSDesign1"):
    d = tmp_path / "P.aedtresults" / f"{design}.results"
    d.mkdir(parents=True)
    return d


def test_su_evidence_found_without_time_filter(tmp_path):
    d = _mk(tmp_path)
    (d / "DV1_S61_V1_F1_SU.txt").write_text("Success 1\nNumTets 100\n", encoding="utf-8")
    su = _hfss_su_evidence(str(tmp_path / "P.aedtresults"), "HFSSDesign1")
    assert su["found"] is True and su["count"] == 1
    assert su["files"][0].endswith("DV1_S61_V1_F1_SU.txt")


def test_su_evidence_filters_stale_files_by_mtime(tmp_path):
    """上一次求解留下的旧 _SU.txt 不能当本次成功证据。"""
    d = _mk(tmp_path)
    old = d / "DV1_S61_V1_F1_SU.txt"
    old.write_text("Success 1\n", encoding="utf-8")
    past = time.time() - 3600
    os.utime(old, (past, past))
    new = d / "DV1_S61_V2_F2_SU.txt"
    new.write_text("Success 1\n", encoding="utf-8")

    now = time.time()
    su = _hfss_su_evidence(str(tmp_path / "P.aedtresults"), "HFSSDesign1", since_ts=now - 60)
    assert su["count"] == 1 and su["files"][0].endswith("V2_F2_SU.txt")

    su_all = _hfss_su_evidence(str(tmp_path / "P.aedtresults"), "HFSSDesign1")
    assert su_all["count"] == 2


def test_su_evidence_rejects_non_success(tmp_path):
    d = _mk(tmp_path)
    (d / "DV1_S61_V1_F1_SU.txt").write_text("Success 0\nNumTets 100\n", encoding="utf-8")
    assert _hfss_su_evidence(str(tmp_path / "P.aedtresults"), "HFSSDesign1")["found"] is False


def test_su_evidence_missing_directory(tmp_path):
    su = _hfss_su_evidence(str(tmp_path / "absent"), "HFSSDesign1")
    assert su["found"] is False and su["count"] == 0


def test_solved_precheck_blocks_export_without_evidence(tmp_path):
    aedt = tmp_path / "P.aedt"
    aedt.write_text("<aedt/>", encoding="utf-8")
    su, err = _solved_evidence_or_error(str(aedt), "HFSSDesign1")
    assert su is None
    assert err["success"] is False and err["error_code"] == "SOLVE_REQUIRED"


def test_solved_precheck_passes_with_evidence(tmp_path):
    aedt = tmp_path / "P.aedt"
    aedt.write_text("<aedt/>", encoding="utf-8")
    d = _mk(tmp_path)
    (d / "DV1_S61_V1_F1_SU.txt").write_text("Success 1\n", encoding="utf-8")
    su, err = _solved_evidence_or_error(str(aedt), "HFSSDesign1")
    assert err is None and su["found"] is True
