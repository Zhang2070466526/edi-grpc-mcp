"""Tests for servers/simulation_tool — 独立仿真工具 exe 包装（纯函数 + 校验路径）。

成功路径依赖本机 exe（Report_Exporter.exe / TR_Simulator.exe），不在单测里跑；
这里只测不触发子进程的纯逻辑与参数校验。
"""

from __future__ import annotations

import json
from pathlib import Path

from servers.chat.service import ERROR_CODES
from servers.simulation_tool import report_export, tr_simulation
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


def test_report_absolutizes_relative_image_paths(tmp_path, monkeypatch):
    """回归（2026-10-09）：exe 按「JSON 文件所在目录」解析相对图片路径，而本工具把 JSON
    写在临时目录 ⇒ 相对路径必然找不到图（实测 FileNotFoundError: 图片不存在：…\Temp\…）。
    这里断言「送给 exe 的 JSON 里图片路径已经全部是绝对路径」。
    """
    img = tmp_path / "gain.png"
    img.write_bytes(b"\x89PNG")

    captured: dict = {}

    def fake_run_exe(exe_path, args, timeout_seconds):
        json_path = args[args.index("--json-file") + 1]
        captured["payload"] = json.loads(Path(json_path).read_text(encoding="utf-8"))
        return {"returncode": 0, "stdout": "{}", "stderr": "", "json_error": "",
                "json": {"success": True, "report_dir": str(tmp_path / "out"),
                         "pdf_path": "", "docx_path": "", "report_data_path": "",
                         "schematic_path": "", "chart_count": 1, "errors": []}}

    monkeypatch.setattr(report_export, "run_exe", fake_run_exe)
    r = export_simulation_report(
        {"model_name": "x",
         "schematic_path": "images/sch.png",
         "charts": [{"link": "发射链路1", "indicator": "输出增益", "image_path": "images/gain.png"}],
         "chart_paths": ["images/legacy.png"]},
        str(tmp_path / "out"))

    assert r["success"] is True
    payload = captured["payload"]
    assert Path(payload["schematic_path"]).is_absolute()
    assert Path(payload["charts"][0]["image_path"]).is_absolute()
    assert Path(payload["chart_paths"][0]).is_absolute()


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


def test_tr_rejects_when_grpc_offline(tmp_path, monkeypatch):
    """回归：EDI gRPC 未就绪时提前拦掉（否则进 exe 要白等 ~11s，还会在 exe 旁留一个空目录）。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: False)
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["error_code"] == "EDA_GRPC_OFFLINE"
    assert r["hint"]


def test_tr_rejects_missing_turbocharts_exe(tmp_path, monkeypatch):
    """回归：EDI 目录缺 turbocharts_app.exe（exe 文档的前置条件）⇒ 提前报 PATH_NOT_FOUND。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["error_code"] == "PATH_NOT_FOUND"
    assert "turbocharts_app.exe" in r["message"]


def test_tr_timeout_is_retryable(tmp_path, monkeypatch):
    """回归：超时属「修好可重跑」⇒ 必须带 retryable/hint（默认是 do_not_retry）。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)

    def boom(*a, **k):
        raise RuntimeError("超时了")
    monkeypatch.setattr(tr_simulation, "run_exe", boom)
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["error_code"] == "TOOL_TIMEOUT"
    assert r["retryable"] is True and r["hint"]


def test_report_rejects_existing_output_files(tmp_path):
    """回归：输出目录已有三件套 ⇒ 语义化错误码 OUTPUT_FILE_BUSY（不再等 exe 白跑一次）。"""
    out = tmp_path / "out"
    out.mkdir()
    (out / "document.pdf").write_bytes(b"%PDF")
    r = export_simulation_report({"model_name": "x"}, str(out))
    assert r["error_code"] == "OUTPUT_FILE_BUSY"
    assert "document.pdf" in r["message"] and r["hint"]


def test_report_timeout_is_retryable(monkeypatch, tmp_path):
    """回归：报告导出超时同样要 retryable。"""
    def boom(*a, **k):
        raise RuntimeError("超时了")
    monkeypatch.setattr(report_export, "run_exe", boom)
    r = export_simulation_report({"model_name": "x"}, str(tmp_path / "o"))
    assert r["error_code"] == "TOOL_TIMEOUT" and r["retryable"] is True


def _stub_run(json_obj):
    """造一个「假 exe」：run_exe 返回固定 JSON，不真启动子进程。"""
    def _run(exe_path, args, timeout_seconds):
        return {"returncode": 0, "stdout": json.dumps(json_obj, ensure_ascii=False), "stderr": "",
                "json": json_obj, "json_error": ""}
    return _run


# ── exe 返回各种结果时的分支覆盖（ST-3：用「假 exe」，不依赖本机 exe）──

def test_tr_exe_failure_maps_to_structured_error(tmp_path, monkeypatch):
    """exe rc≠0 ⇒ SIMULATION_EXECUTION_FAILED + details.tr_errors 原文 + 可执行 hint。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(tr_simulation, "run_exe", lambda *a, **k: {
        "returncode": 1, "stdout": "", "stderr": "", "json_error": "",
        "json": {"simulation_path": str(tmp_path), "errors": [
            {"code": "UNKNOWN_INDICATOR", "stage": "parameter", "link": "链路1",
             "indicator": "乱写的指标", "message": "不支持的指标"}], "results": []}})
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["error_code"] == "SIMULATION_EXECUTION_FAILED"
    assert "[UNKNOWN_INDICATOR]" in r["message"] and r["hint"]
    assert r["details"]["tr_errors"][0]["stage"] == "parameter"


def test_tr_partial_failure_keeps_data_and_warns(tmp_path, monkeypatch):
    """部分指标失败：exe rc=0 但 errors 非空 ⇒ success 仍 True，但必须透传 errors + warnings。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(tr_simulation, "run_exe", _stub_run({
        "simulation_path": str(tmp_path),
        "results": [{"link": "链路1", "electrical_parameters": [
            {"indicator": "输出增益", "minimum": "39.5", "typical": "", "maximum": "40.8", "unit": "dB"}]}],
        "errors": [{"code": "RESULT_VALUE_MISSING", "stage": "extract", "link": "链路1",
                    "indicator": "噪声系数", "message": "空数值"}]}))
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["success"] is True and len(r["results"]) == 1
    assert r["errors"][0]["code"] == "RESULT_VALUE_MISSING"
    assert r["warnings"] and "1 项指标失败" in r["warnings"][0]


def test_tr_artifacts_list_result_files(tmp_path, monkeypatch):
    """ST-5：结果目录只给目录拿不到下载链接 ⇒ 关键文件（CSV/PNG/日志）也要进 artifacts。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    res_dir = tmp_path / "simulate_x"
    (res_dir / "链路1").mkdir(parents=True)
    (res_dir / "链路1" / "输出增益.csv").write_text("freq,gain\n1,2\n")
    (res_dir / "链路1" / "输出增益.png").write_bytes(b"\x89PNG")
    (res_dir / "simulation.log").write_text("log")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(tr_simulation, "run_exe", _stub_run(
        {"simulation_path": str(res_dir), "results": [], "errors": []}))
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    types = [a["type"] for a in r["artifacts"]]
    assert types.count("csv") == 1 and types.count("image") == 1 and "log" in types
    assert "directory" in types


def test_tr_bad_json_stdout_is_reported(tmp_path, monkeypatch):
    """exe 没输出合法 JSON ⇒ 明确报「stdout 不是合法 JSON」而不是裸异常。"""
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    monkeypatch.setattr(tr_simulation, "tcp_port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(tr_simulation, "run_exe", lambda *a, **k: {
        "returncode": 0, "stdout": "not json", "stderr": "",
        "json": None, "json_error": "stdout 不是合法 JSON: Expecting value"})
    r = run_tr_simulation(str(epp), [{"name": "链路1"}], edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["error_code"] == "SIMULATION_EXECUTION_FAILED"
    assert "不是合法 JSON" in r["message"]


def test_report_success_builds_artifacts_and_links(tmp_path, monkeypatch):
    """报告成功路径：三件套进 artifacts，pdf→inline、docx→attachment 两条链接都给出。"""
    out = tmp_path / "out"                  # 假 exe「写」出来的三件套
    out.mkdir()
    for n in ("document.pdf", "document.docx", "report.json"):
        (out / n).write_bytes(b"x")
    monkeypatch.setattr(report_export, "run_exe", _stub_run({
        "success": True, "report_dir": str(out), "pdf_path": str(out / "document.pdf"),
        "docx_path": str(out / "document.docx"), "report_data_path": str(out / "report.json"),
        "schematic_path": "", "chart_count": 2, "errors": []}))
    # 入参目录必须是空的（否则会先被 OUTPUT_FILE_BUSY 预检拦下）
    r = export_simulation_report({"model_name": "x"}, str(tmp_path / "arg_dir"))
    assert r["success"] is True
    assert sorted(a["type"] for a in r["artifacts"]) == ["docx", "json", "pdf"]
    assert set(r["links"]) == {"pdf", "docx"}
    assert r["links"]["pdf"]["download_url"] and r["links"]["docx"]["download_url"]
    assert r["chart_count"] == 2


def test_new_error_codes_registered_in_chat_dictionary():
    """回归：新工具用到的错误码必须进 chat 的 ERROR_CODES 词典（否则界面显示英文裸码）。"""
    for code in ("INVALID_REPORT_JSON", "INVALID_OUTPUT_DIR", "EXE_NOT_FOUND", "REPORT_EXPORT_FAILED",
                 "OUTPUT_FILE_BUSY", "NO_LINKS", "LINK_JSON_INVALID", "SIMULATION_EXECUTION_FAILED",
                 "EDA_GRPC_OFFLINE", "TOOL_TIMEOUT"):
        assert code in ERROR_CODES, f"{code} 未登记进 ERROR_CODES"
