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


# ---------------------------------------------------------------------------
# 2026-11-10 事故：EDI config.ini 写着 ADS2026，MCP 却一直传 ADS2020 → RAW_NOT_FOUND。
# 根因两级：①探测只 glob、不看 config.ini；②多命中时 sorted(hits)[0] 取字典序最小，
#          'ADS2020_update2' < 'ADS2026_Update1'，装了新版也永远选 2020。
# 修法优先级：显式 ads_path(调用侧) > EDI_ADS_PATH > config.ini[ads] > glob 取最高版本。
# ---------------------------------------------------------------------------

from servers.simulation_tool import config as sim_config  # noqa: E402


def _installs(tmp_path: Path) -> tuple[Path, Path]:
    """造两个 ADS 安装目录：老版本 + 新版本（新版本号更大，但字典序更靠后）。"""
    old = tmp_path / "ADS2020_update2"
    new = tmp_path / "ADS2026_Update1"
    old.mkdir()
    new.mkdir()
    return old, new


def _edi_root(tmp_path: Path, hpeesof_dir: str | None) -> Path:
    """造 EDI 安装根；hpeesof_dir 非 None 时同时写 config/config.ini。"""
    edi = tmp_path / "EDI"
    if hpeesof_dir is None:
        edi.mkdir()
    else:
        (edi / "config").mkdir(parents=True)
        (edi / "config" / "config.ini").write_text(
            f"[ads]\nhpeesof_dir={hpeesof_dir}\n", encoding="utf-8")
    return edi


def _isolate(monkeypatch, tmp_path: Path, edi: Path) -> None:
    """把探测限制在 tmp_path 内：清环境变量、只 glob tmp 下的 ADS*、config 指向假 EDI 根。"""
    monkeypatch.delenv(sim_config._ADS_ENV, raising=False)
    monkeypatch.setattr(sim_config, "_ADS_GLOB_CANDIDATES",
                        ((tmp_path / "ADS*").as_posix(),))
    monkeypatch.setattr(sim_config, "EDI_INSTALL_DIR", str(edi))


def test_multiple_installs_picks_highest_version(monkeypatch, tmp_path):
    """回归：old 字典序在前，旧实现 sorted()[0] 会选它；现在必须选版本号更高的 new。"""
    old, new = _installs(tmp_path)
    _isolate(monkeypatch, tmp_path, _edi_root(tmp_path, None))
    assert Path(detect_ads_path()).name == new.name, "多版本并存时没选最高版本"


def test_edi_config_wins_over_glob(monkeypatch, tmp_path):
    """config.ini 说用 old（GUI 里配的），glob 最高是 new ⇒ 必须听 config.ini 的。"""
    old, new = _installs(tmp_path)
    _isolate(monkeypatch, tmp_path, _edi_root(tmp_path, old.as_posix()))
    assert Path(detect_ads_path()).name == old.name


def test_env_wins_over_edi_config(monkeypatch, tmp_path):
    """运维覆盖：EDI_ADS_PATH 指向 old，config.ini 指向 new ⇒ 环境变量优先。"""
    old, new = _installs(tmp_path)
    _isolate(monkeypatch, tmp_path, _edi_root(tmp_path, new.as_posix()))
    monkeypatch.setenv(sim_config._ADS_ENV, old.as_posix())
    assert Path(detect_ads_path()).name == old.name


def test_broken_edi_config_falls_back_to_glob(monkeypatch, tmp_path):
    """config.ini 指向不存在的目录 ⇒ 不许报错，降级到 glob 取最高版本。"""
    old, new = _installs(tmp_path)
    _isolate(monkeypatch, tmp_path, _edi_root(tmp_path, (tmp_path / "ADS9999_gone").as_posix()))
    assert Path(detect_ads_path()).name == new.name


def test_no_config_no_glob_returns_empty(monkeypatch, tmp_path):
    """什么都没有时返回空串，由调用侧报 PATH_NOT_FOUND，而不是抛异常。"""
    _isolate(monkeypatch, tmp_path, _edi_root(tmp_path, None))
    assert detect_ads_path() == ""
