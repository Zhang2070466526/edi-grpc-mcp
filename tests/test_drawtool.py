"""drawtool 工具测试（mock run_exe，不真调 exe）。"""

from __future__ import annotations

import json
from pathlib import Path

from servers.simulation_tool.xy_chart import draw_chart


def test_draw_chart_invalid_params():
    assert draw_chart([], "D:/out")["success"] is False
    assert draw_chart([{"points": [[0.0, 1.0]]}], "")["success"] is False
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out",
                      image_format="bmp")["success"] is False
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out",
                      file_stem="a/b")["success"] is False


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
    """写配置失败/跑 exe 失败都不能把临时目录漏在 %TEMP%。"""
    import glob
    import tempfile

    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "edi_drawtool_*")))

    def _run(exe_path, args, timeout_seconds):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("servers.simulation_tool.xy_chart.run_exe", _run)
    assert draw_chart([{"points": [[0.0, 1.0]]}], "D:/out")["success"] is False

    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "edi_drawtool_*")))
    assert after == before


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
