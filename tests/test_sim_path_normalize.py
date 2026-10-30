"""目录路径规范化回归测试（2026-10-21）。

背景：`detect_ads_path()` 原本只规范化自动探测结果 —— 用户/agent 显式传
`C:/Program Files\\Keysight/ADS2020_update2` 这种混合斜杠时不做处理，exe 解析失败会
回退到硬编码 `C:/Program Files/Keysight/ADS2020`。`normalize_dir()` 把混合斜杠统一为
Windows 反斜杠，供 run_tr_simulation / run_mixer_simulation（turbocharts_exe）使用。
"""

from __future__ import annotations

from pathlib import Path

from servers.simulation_tool.config import normalize_dir


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
