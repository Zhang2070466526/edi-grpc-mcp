"""Tests for servers/simulation_tool — 独立仿真工具 exe 包装（纯函数 + 校验路径）。

成功路径依赖本机 exe（Report_Exporter.exe / TR_Simulator.exe），不在单测里跑；
这里只测不触发子进程的纯逻辑与参数校验。
"""

from __future__ import annotations

from servers.simulation_tool.config import _decode_text, fmt_errors
from servers.simulation_tool.report_export import export_simulation_report
from servers.simulation_tool.tr_simulation import run_tr_simulation


# ── config 纯函数 ──

def test_decode_utf8():
    assert _decode_text("成功".encode("utf-8")) == "成功"


def test_decode_gbk_fallback():
    assert _decode_text("成功".encode("gbk")) == "成功"


def test_decode_utf8_bom():
    assert _decode_text(b"\xef\xbb\xbf" + '{"a":1}'.encode("utf-8")) == '{"a":1}'


def test_decode_empty():
    assert _decode_text(b"") == ""


def test_fmt_errors():
    assert fmt_errors([]) == ""
    assert "坏了" in fmt_errors([{"code": "X", "message": "坏了"}])
    # 无 message 时用 code 兜底
    assert "[Y] Y" in fmt_errors([{"code": "Y"}])


# ── export_simulation_report 校验路径（不触发 exe）──

def test_report_rejects_non_dict():
    r = export_simulation_report([], "D:/reports/run001")
    assert r["error_code"] == "INVALID_REPORT_JSON"


def test_report_rejects_empty_output_dir():
    r = export_simulation_report({"model_name": "x"}, "")
    assert r["error_code"] == "INVALID_OUTPUT_DIR"


def test_report_rejects_missing_schematic(tmp_path):
    r = export_simulation_report(
        {"model_name": "x"}, str(tmp_path / "out"),
        schematic=str(tmp_path / "nope.png"),
    )
    assert r["error_code"] == "FILE_NOT_FOUND"


# ── run_tr_simulation 校验路径（不触发 exe）──

def test_tr_rejects_missing_epp(tmp_path):
    r = run_tr_simulation(str(tmp_path / "nope.epp"), [{"name": "链路1"}])
    assert r["error_code"] == "FILE_NOT_FOUND"


def test_tr_rejects_empty_links(tmp_path):
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    r = run_tr_simulation(str(epp), [])
    assert r["error_code"] == "NO_LINKS"


def test_tr_rejects_bad_threads(tmp_path):
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], threads=0)
    assert r["error_code"] == "INVALID_PARAMETERS"
