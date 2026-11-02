"""目录路径规范化回归测试（2026-10-21）。

背景：`detect_ads_path()` 原本只规范化自动探测结果 —— 用户/agent 显式传
`C:/Program Files\\Keysight/ADS2020_update2` 这种混合斜杠时不做处理，exe 解析失败会
回退到硬编码 `C:/Program Files/Keysight/ADS2020`。`normalize_dir()` 把混合斜杠统一为
Windows 反斜杠，供 run_tr_simulation / run_mixer_simulation（turbocharts_exe）使用。
"""

from __future__ import annotations

import sys
from pathlib import Path

from servers.simulation_tool.config import _find_edi_root, normalize_dir


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


def test_find_edi_root_walks_up_in_frozen_mode(tmp_path, monkeypatch):
    """打包态：exe 部署在 EDI 根的任意深度，往上找 simulation_tool 定位 EDI 根。"""
    edi = tmp_path / "EDI"
    (edi / "simulation_tool").mkdir(parents=True)
    exe_dir = edi / "edi-agent" / "service" / "optional-mcps" / "edi-mcp"
    exe_dir.mkdir(parents=True)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "edi_mcp_server.exe"))

    assert _find_edi_root() == str(edi)


def test_find_edi_root_returns_empty_in_source_mode():
    """源码态不往上找（仓库自身有 servers/simulation_tool 会误判），返回空串回退硬编码。"""
    assert _find_edi_root() == ""
