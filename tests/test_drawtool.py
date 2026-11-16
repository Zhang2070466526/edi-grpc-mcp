"""drawtool 工具测试（mock run_exe，不真调 exe）。"""

from __future__ import annotations

import json
from pathlib import Path

from servers.simulation_tool.xy_chart import draw_chart


def test_draw_chart_invalid_params():
    assert draw_chart([], "D:/out")["success"] is False
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out",
                      image_format="bmp")["success"] is False
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out",
                      file_stem="a/b")["success"] is False


def test_draw_chart_empty_image_path_uses_default_dir(monkeypatch, tmp_path):
    """image_path 留空 ⇒ 落到默认产物目录（原断言「空即失败」已过时）。

    2026-11-16 起：不传输出路径统一落到 `result_dir()`（默认 `~/mcp_result`），
    与「工具产物统一放一个目录」的设计一致，不再算参数错误。
    """
    defaults = tmp_path / "mcp_result"
    captured: dict = {}

    def _run(exe_path, args, timeout_seconds):
        captured["config"] = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        img = Path(captured["config"]["image_path"]) / "chart.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"\x89PNG")
        return {"returncode": 0, "stdout": "", "stderr": "",
                "json": {"status": "success", "image_path": str(img), "error_code": None},
                "json_error": ""}

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    monkeypatch.setattr("servers.utils.result_dir", lambda: str(defaults))
    r = draw_chart([{"points": [[0.0, 1.0]]}], "")
    assert r["success"] is True, r.get("error_code") or r.get("message")
    used = Path(captured["config"]["image_path"])
    assert used.is_relative_to(defaults), f"应落在默认目录 {defaults} 下，实际 {used}"


def test_draw_chart_rejects_bad_windows_filenames():
    """file_stem 决定临时配置名→输出图片名，必须按 Windows 文件名规则拦在写盘之前。"""
    payload = [{"points": [[0.0, 1.0]]}]
    for bad in ("a??", "a:b", "a|b", "a*b", "a<b", "a>b", "a\"b",
                "chart.", "chart ", "CON", "nul", "com1", "a\tb"):
        r = draw_chart(payload, "D:/out", file_stem=bad)
        assert r["success"] is False, bad
        assert r["error_code"] == "INVALID_PARAMETERS", bad
    # 正常名字照旧放行（只测校验函数，不真调 exe）
    from servers.simulation_tool.xy_chart import _file_stem_error
    for good in ("gain_25C", "chart.v2", "三温度增益", "a-b_c"):
        assert _file_stem_error(good) == "", good


def test_draw_chart_needs_elevation(monkeypatch):
    """WinError 740：服务未提权时 Windows 直接拒绝启动 requireAdministrator 的 exe。"""
    def _run(exe_path, args, timeout_seconds):
        exc = OSError(740, "请求的操作需要提升。")
        exc.winerror = 740
        raise exc

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    r = draw_chart([{"points": [[0.0, 1.0]]}], "D:/out")
    assert r["success"] is False
    assert r["error_code"] == "EXE_NEEDS_ELEVATION"
    assert "管理员" in r["hint"]


def test_draw_chart_no_temp_dir_leak(monkeypatch, tmp_path):
    """写配置失败/跑 exe 失败都不能把临时目录漏在本用例的临时根下。

    2026-11-13：原来 glob 的是系统 %TEMP%，全仓顺序执行时会被别的用例创建的
    `edi_drawtool_*` 干扰（before/after 集合不等 ⇒ 偶发失败，单独跑却绿）。
    改成把 `tempfile.tempdir` 指向 tmp_path，只统计本用例自己建的目录。
    """
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def _dirs():
        return {p.name for p in tmp_path.glob("edi_drawtool_*")}

    before = _dirs()
    assert before == set()

    def _run(exe_path, args, timeout_seconds):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out")["success"] is False

    assert _dirs() == before


def test_draw_chart_success(monkeypatch, tmp_path):
    out = tmp_path / "plots"
    img = out / "chart.png"
    captured: dict = {}

    def _run(exe_path, args, timeout_seconds):
        captured["exe_path"] = exe_path
        captured["config"] = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        return {"returncode": 0,
                "stdout": json.dumps({"status": "success", "message": "绘图完成",
                                      "image_path": str(img), "error_code": None}),
                "stderr": "",
                "json": {"status": "success", "message": "绘图完成",
                         "image_path": str(img), "error_code": None},
                "json_error": ""}

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    r = draw_chart([{"name": "g", "points": [[1.0, 2.0]]}], str(out), chart_title="t")

    assert r["success"] is True
    assert r["image_path"] == str(img)
    cfg = captured["config"]
    assert cfg["chart_title"] == "t"
    assert cfg["series"][0]["name"] == "g"
    assert cfg["show_window"] is False       # 默认后台
    assert cfg["image_path"] == str(out)     # image_path 转绝对路径


def test_draw_chart_failure(monkeypatch):
    def _run(exe_path, args, timeout_seconds):
        return {"returncode": 1,
                "stdout": json.dumps({"status": "error", "message": "image_path 必须是目录",
                                      "image_path": None, "error_code": 1}),
                "stderr": "",
                "json": {"status": "error", "message": "image_path 必须是目录",
                         "image_path": None, "error_code": 1},
                "json_error": ""}

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    r = draw_chart([{"points": [[1.0, 2.0]]}], "D:/out")
    assert r["success"] is False
    assert r["error_code"] == "DRAW_FAILED"
