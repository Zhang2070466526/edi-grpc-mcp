"""detect_ads_path() 的路径规范化回归（2026-10-21 真实事故）。

事故：Windows 上 glob('C:/Program Files/Keysight/ADS*') 返回
    'C:/Program Files/Keysight\\ADS2020_update2'   ← 混合斜杠
原样传给 mixer_simulation.exe 后，它解析失败并回退到硬编码的
'C:/Program Files/Keysight/ADS2020'（实际装的是 ADS2020_update2），
于是每个工况的 ads_output.log 只写一句 "Can not find ...ADS2020"，
整条链路报 SIMULATION_EXECUTION_FAILED。规范化后仿真正常出结果。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.simulation_tool.config import detect_ads_path


def test_ads_path_has_no_mixed_separators():
    path = detect_ads_path()
    if not path:
        pytest.skip("本机未安装 ADS，跳过")
    assert "/" not in path or "\\" not in path, f"斜杠混用会害 exe 找不到 ADS：{path!r}"
    assert "ADS" in path.upper()


def test_ads_path_points_to_existing_dir():
    path = detect_ads_path()
    if not path:
        pytest.skip("本机未安装 ADS，跳过")
    assert Path(path).is_dir(), f"探测到的 ADS 目录不存在：{path!r}"
