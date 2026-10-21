"""目录路径规范化回归测试（2026-10-21）。

背景：`detect_ads_path()` 原本只规范化自动探测结果 —— 用户/agent 显式传
`C:/Program Files\\Keysight/ADS2020_update2` 这种混合斜杠时不做处理，exe 解析失败会
回退到硬编码 `C:/Program Files/Keysight/ADS2020`，报「Can not find ...ADS2020」。
现在两个仿真工具对显式传参也走 `normalize_dir()`。
"""

from __future__ import annotations

from pathlib import Path

from servers.simulation_tool import mixer_simulation as mod
from servers.simulation_tool.config import normalize_dir

_LINKS = [{
    "name": "L", "direction": "down", "f_rf": [33, 37], "f_if": 3.9, "bw": 0.4,
    "pin": -30, "p_lo": 4,
    "channels": [{"name": "CH1", "rf_port": 1, "if_port": 2, "mixer": "MIX1"}],
    "stages": [{"mixer": "MIX1", "lo_port": 5, "relation": "diff",
                "injection": "low", "f_if": 3.9}],
}]


def test_normalize_dir_fixes_mixed_slashes(tmp_path):
    mixed = str(tmp_path).replace("\\", "/", 1)
    assert "/" in mixed and "\\" in mixed, f"构造不出混合斜杠：{mixed}"
    normalized = normalize_dir(mixed)
    assert normalized == str(Path(mixed))
    # 规范化后统一为 Windows 反斜杠：不应再残留正斜杠（UNC 前缀除外）
    assert "/" not in normalized or normalized.startswith("//")


def test_normalize_dir_handles_empty_and_whitespace():
    assert normalize_dir("") == ""
    assert normalize_dir("   ") == ""
    assert normalize_dir(None) == ""  # type: ignore[arg-type]


def test_explicit_mixed_slash_ads_path_reaches_exe_normalized(tmp_path, monkeypatch):
    """显式传混合斜杠 ads_path 时，传给 exe 的 --ads 必须是规范化后的路径。"""
    edi = tmp_path / "EDI"
    edi.mkdir()
    (edi / "turbocharts_app.exe").write_bytes(b"stub")
    ads = tmp_path / "ADS"
    ads.mkdir()
    sim = tmp_path / "sim"
    sim.mkdir()

    captured: dict = {}

    def fake_run(exe, args, timeout):
        captured["args"] = list(args)
        return {
            "json": {"simulation_path": str(sim), "errors": [], "results": [
                {"link": "L/CH1", "electrical_parameters": [
                    {"indicator": "变频增益", "minimum": "25.2", "typical": "26.4",
                     "maximum": "27.1", "unit": "dB", "note": ""}]}]},
            "stdout": "", "stderr": "", "returncode": 0, "json_error": None,
        }

    monkeypatch.setattr(mod, "require_file", lambda p, exts: (p, None))
    monkeypatch.setattr(mod, "tcp_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mod, "run_exe", fake_run)

    mixed_ads = str(ads).replace("\\", "/", 1)
    resp = mod.run_mixer_simulation(
        epp_path="C:/fake/p.epp", links=_LINKS, indicators="变频增益",
        edi_path=str(edi), ads_path=mixed_ads, timeout_seconds=5,
    )

    assert resp["success"] is True
    idx = captured["args"].index("--ads")
    assert captured["args"][idx + 1] == str(Path(mixed_ads))
    # 规范化后不应再有混合斜杠
    normalized = captured["args"][idx + 1]
    assert normalized.count("/") == 0 or normalized.startswith("\\\\")
