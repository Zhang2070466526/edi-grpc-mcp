"""com_child 启动契约的回归测试（2026-11-26 冻结态故障）。

故障现场：PyInstaller 打包后包内没有解释器，父进程却拼
`edi_mcp_server.exe <com_child.py> state` → exe 的 argparse 报
`unrecognized arguments`、exit=2，于是 export_snp / open / state 全挂。
这里把两条启动路径的契约钉住：冻结态走 `exe --com-child <task>`，源码态走
`<venv python> com_child.py <task>`；并验证结果回传不只依赖 stdout。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from servers.ansys import com_child
from servers.ansys import project_manage as PM


def test_frozen_mode_uses_exe_hidden_entry(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = PM._com_child_cmd("state", ())
    assert cmd == [sys.executable, "--com-child", "state"]


def test_frozen_mode_passes_task_args(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = PM._com_child_cmd("export_snp", ("a.aedt", "HFSSDesign1"))
    assert cmd[:3] == [sys.executable, "--com-child", "export_snp"]
    assert cmd[3:] == ["a.aedt", "HFSSDesign1"]


def test_source_mode_uses_script_with_interpreter(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = PM._com_child_cmd("state", ())
    assert cmd[1].endswith("com_child.py") and cmd[2] == "state"


def test_frozen_cwd_is_exe_dir(monkeypatch) -> None:
    """cwd 不能落到 _internal（与包内 3.12 DLL 同目录，会污染外部解释器）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert PM._com_child_cwd() == str(Path(sys.executable).parent)


def test_emit_writes_result_file(tmp_path, monkeypatch) -> None:
    """窗口模式 stdout 可能是 None：结果必须能只靠文件传回来。"""
    out = tmp_path / "result.json"
    monkeypatch.setenv(com_child._OUT_ENV, str(out))
    code = com_child._emit({"ok": True, "result": {"x": 1}}, 0)
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"ok": True, "result": {"x": 1}}


def test_emit_survives_broken_stdout(tmp_path, monkeypatch) -> None:
    """sys.stdout is None（console=False 冻结产物）时不能炸。"""
    out = tmp_path / "result.json"
    monkeypatch.setenv(com_child._OUT_ENV, str(out))
    monkeypatch.setattr(sys, "stdout", None)
    assert com_child._emit({"ok": False, "error": "boom"}, 1) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["error"] == "boom"
