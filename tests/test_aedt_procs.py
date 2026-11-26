"""AEDT 进程/版本识别 + com_child 失败分档 + 导出覆盖保护的回归测试。

覆盖 2026-11-26 的四处改动（现场机 star 的"查询全部 AEDT_NOT_RESPONDING"根因是
双版本 AEDT 下裸 ProgID 走 CurVer 连到了另一版本）：
  1. `aedt_procs.exe_version` 能从三种安装路径 + PE 文件版本推出 `YYYY.R`
  2. `progid_candidates` **只**给出确实在运行的版本（否则 Dispatch 会新建实例）
  3. `_com_reason/_com_error_code` 把子进程失败分成不同错误码
  4. `export_hfss_snp` 不再静默覆盖已存在的输出文件
"""

from __future__ import annotations

from pathlib import Path

import pytest

from servers.ansys import aedt_procs as A
from servers.ansys import project_manage as PM
from servers.ansys import run_analysis as RA
from servers.ansys import com_child as CC


@pytest.mark.parametrize("path,expected", [
    (r"C:\Program Files\AnsysEM\AnsysEM20.2\Win64\ansysedt.exe", "2020.2"),
    (r"C:\Program Files\AnsysEM\v242\Win64\ansysedt.exe", "2024.2"),
    (r"C:\Program Files\AnsysEM\AnsysEM2024R2\Win64\ansysedt.exe", "2024.2"),
    (r"D:\Ansys 2024\AnsysEM\Win64\ansysedt.exe", ""),      # 路径里没版本 → 交给 PE 版本兜底
])
def test_exe_version_from_path(path, expected):
    assert A.exe_version(path) == expected


def test_progid_candidates_only_running_versions():
    """只把"确实在跑"的版本排进候选：否则 Dispatch 会新建一个 AEDT。"""
    infos = {
        100: {"cmdline": "ansysedt.exe", "exe": "", "version": "2020.2", "is_helper": False},
        200: {"cmdline": "ansysedt.exe -Embedding", "exe": "", "version": "", "is_helper": True},
    }
    assert A.progid_candidates(infos) == ["Ansoft.ElectronicsDesktop.2020.2"]
    assert A.progid_candidates({}) == []                     # 没实例 → 不给版本化条目


def test_version_report_flags_mismatch(monkeypatch):
    monkeypatch.setattr(A, "curver", lambda: ("Ansoft.ElectronicsDesktop.2024.2", "2024.2"))
    infos = {100: {"cmdline": "x", "exe": "", "version": "2020.2", "is_helper": False}}
    rep = A.version_report(infos)
    assert rep["version_mismatch"] is True
    assert rep["aedt_versions_running"] == ["2020.2"]
    assert rep["curver_version"] == "2024.2"


def test_version_report_no_mismatch_when_versions_match(monkeypatch):
    monkeypatch.setattr(A, "curver", lambda: ("Ansoft.ElectronicsDesktop.2020.2", "2020.2"))
    infos = {100: {"cmdline": "x", "exe": "", "version": "2020.2", "is_helper": False}}
    assert A.version_report(infos)["version_mismatch"] is False


@pytest.mark.parametrize("err,code", [
    ("[version_mismatch] 版本不一致", "AEDT_VERSION_MISMATCH"),
    ("[shell_only] 只剩空壳", "AEDT_SHELL_ONLY"),
    ("[not_running] AEDT 未运行", "AEDT_NOT_RUNNING"),
    ("[permission_mismatch] 权限不一致", "AEDT_PERMISSION_MISMATCH"),
    ("[attach_failed] 连不上", "AEDT_NOT_RESPONDING"),
    ("旧式无标记的错误", "AEDT_NOT_RESPONDING"),            # 向后兼容
])
def test_com_error_code_mapping(err, code):
    assert PM._com_error_code(err) == code
    assert PM._strip_reason(err) in ("版本不一致", "只剩空壳", "AEDT 未运行", "权限不一致", "连不上", err)


def test_existing_snp_matches_placeholder(tmp_path):
    """`.sNp` 占位要按 `.s*p` 通配判重（导出前不知道端口数）。"""
    (tmp_path / "P_D.s2p").write_text("x", encoding="utf-8")
    assert RA._existing_snp(tmp_path / "P_D.sNp") is not None
    assert RA._existing_snp(tmp_path / "Other.sNp") is None
    assert RA._existing_snp(tmp_path / "P_D.s2p") is not None


def test_export_refuses_to_overwrite_explicit_path(tmp_path, monkeypatch):
    """显式给已存在的 output_path、又没传 overwrite → OUTPUT_ALREADY_EXISTS（不再静默覆盖）。"""
    aedt = tmp_path / "Demo.aedt"
    aedt.write_text("x", encoding="utf-8")
    existing = tmp_path / "out.s2p"
    existing.write_text("OLD", encoding="utf-8")

    monkeypatch.setattr(RA, "aedt_is_running", lambda: True)
    monkeypatch.setattr(RA, "_solved_evidence_or_error",
                        lambda *_a, **_k: ({"found": True, "files": [], "count": 1,
                                            "directory": str(tmp_path)}, None))

    res = RA.export_hfss_snp(str(aedt), "HFSSDesign1", "Setup1", "Sweep",
                             output_path=str(existing))
    assert res["success"] is False
    assert res["error_code"] == "OUTPUT_ALREADY_EXISTS"
    assert existing.read_text(encoding="utf-8") == "OLD"     # 原文件没被动
    # 额外字段统一挂在 details 下（error_response 的约定）
    assert res["details"]["existing_path"] == str(existing)
    assert res["details"]["suggested_path"].endswith(".s2p")


def test_export_overwrite_true_is_allowed(tmp_path, monkeypatch):
    """overwrite=True 时不拦（后续会真的去调 com_child，这里只验证不返回 OUTPUT_ALREADY_EXISTS）。"""
    aedt = tmp_path / "Demo.aedt"
    aedt.write_text("x", encoding="utf-8")
    existing = tmp_path / "out.s2p"
    existing.write_text("OLD", encoding="utf-8")

    monkeypatch.setattr(RA, "aedt_is_running", lambda: True)
    monkeypatch.setattr(RA, "_solved_evidence_or_error",
                        lambda *_a, **_k: ({"found": True, "files": [], "count": 1,
                                            "directory": str(tmp_path)}, None))
    monkeypatch.setattr(PM, "_call_com_child",
                        lambda *a, **k: (False, None, "[attach_failed] 测试桩：不连 AEDT"))
    monkeypatch.setattr(RA, "_call_com_child", PM._call_com_child, raising=False)

    res = RA.export_hfss_snp(str(aedt), "HFSSDesign1", "Setup1", "Sweep",
                             output_path=str(existing), overwrite=True)
    assert res.get("error_code") != "OUTPUT_ALREADY_EXISTS"
    assert res["success"] is False                             # 撞到桩的 COM 失败


# ── com_child 的失败分档：什么条件下抛哪个 reason（此前只有 ad-hoc 验过，套件不红） ──

GUI = {"cmdline": "ansysedt.exe", "exe": r"C:\x\AnsysEM20.2\Win64\ansysedt.exe",
       "version": "2020.2", "is_helper": False}
SHELL = {"cmdline": "ansysedt.exe -Embedding", "exe": "", "version": "", "is_helper": True}


def _com_boom(*_a, **_k):
    raise RuntimeError("COM 连不上（桩）")


def _patch_com(monkeypatch, *, curver_version: str = "2020.2",
               reaped: list[int] | None = None) -> None:
    """把所有 COM 通道打成必失败，并固定 CurVer / 空壳清理结果。"""
    from win32com.client import Dispatch, GetActiveObject

    monkeypatch.setattr(Dispatch, "__call__", _com_boom, raising=False)
    monkeypatch.setattr("win32com.client.Dispatch", _com_boom)
    monkeypatch.setattr("win32com.client.GetActiveObject", _com_boom)
    monkeypatch.setattr(CC, "_reap_orphan_shells", lambda infos: list(reaped or []))
    monkeypatch.setattr(A, "curver",
                        lambda: (f"Ansoft.ElectronicsDesktop.{curver_version}", curver_version))


def test_attach_reason_not_running(monkeypatch):
    monkeypatch.setattr(CC, "_infos", lambda: {})
    with pytest.raises(CC.ComChildError) as ei:
        CC._attach()
    assert ei.value.reason == "not_running"


def test_attach_reason_shell_only(monkeypatch):
    """只剩 -Embedding 空壳且清掉了 → shell_only（提示重开 AEDT）。"""
    monkeypatch.setattr(CC, "_infos", lambda: {200: dict(SHELL)})
    _patch_com(monkeypatch, reaped=[200])
    with pytest.raises(CC.ComChildError) as ei:
        CC._attach()
    assert ei.value.reason == "shell_only"
    assert "200" in str(ei.value)


def test_attach_reason_version_mismatch(monkeypatch):
    """运行实例 2020.2、CurVer 却指 2024.2，且版本化 ProgID 也没连上 → version_mismatch。"""
    monkeypatch.setattr(CC, "_infos", lambda: {100: dict(GUI)})
    _patch_com(monkeypatch, curver_version="2024.2")
    with pytest.raises(CC.ComChildError) as ei:
        CC._attach()
    assert ei.value.reason == "version_mismatch"
    assert "2024.2" in str(ei.value) and "2020.2" in str(ei.value)


def test_attach_reason_attach_failed_when_versions_match(monkeypatch):
    monkeypatch.setattr(CC, "_infos", lambda: {100: dict(GUI)})
    _patch_com(monkeypatch, curver_version="2020.2")
    with pytest.raises(CC.ComChildError) as ei:
        CC._attach()
    assert ei.value.reason == "attach_failed"


def test_cmd_state_reason_permission_mismatch(monkeypatch):
    """连到空壳、同时机器上有真 GUI → permission_mismatch（UAC 隔离的典型形态）。"""
    infos = {100: dict(GUI), 200: dict(SHELL)}

    class _Desk:
        def GetProjectList(self):
            return []

        def GetActiveProject(self):
            return None

        def GetProcessID(self):
            return 200                       # 连到了空壳

    monkeypatch.setattr(CC, "_infos", lambda: infos)
    monkeypatch.setattr(CC, "_attach", lambda infos=None: (_Desk(), "Ansoft.ElectronicsDesktop.2020.2"))
    with pytest.raises(CC.ComChildError) as ei:
        CC.cmd_state()
    assert ei.value.reason == "permission_mismatch"
    assert "100" in str(ei.value)            # 指出真实 GUI 是哪个 PID
