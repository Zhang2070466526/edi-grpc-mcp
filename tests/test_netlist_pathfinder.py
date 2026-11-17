"""find_netlist_paths 校验与解析路径测试（mock run_exe，不真调 exe）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from servers.simulation_tool import netlist_pathfinder as np


@pytest.fixture(autouse=True)
def _isolate_output_dir(tmp_path, monkeypatch):
    """默认产物目录换成 tmp —— 否则每个用例都会往真实的 %TEMP%/mcp_result 里写文件。"""
    monkeypatch.setattr(np, "default_result_path",
                        lambda name, ext: str(tmp_path / f"{name}_auto{ext}"))


def _files(tmp_path):
    netlist = tmp_path / "netlist.log"
    netlist.write_text("Port:IN Common 0 Num=1\nPort:OUT BranchB 0 Num=2\n", encoding="utf-8")
    ep = tmp_path / "schematic.ep"
    ep.write_text("(schematic)", encoding="utf-8")
    return str(netlist), str(ep)


def _stub_run(json_obj, rc=0):
    def _run(exe_path, args, timeout_seconds):
        return {"returncode": rc, "stdout": json.dumps(json_obj, ensure_ascii=False),
                "stderr": "", "json": json_obj, "json_error": ""}
    return _run


_RESULT = {
    "netlist_path": "x/netlist.log", "ep_path": "x/schematic.ep",
    "ports": [1, 2],
    "results": [{
        "input_port": 1, "output_port": 2, "status": "ok", "complete": True,
        "path_count": 1, "paths": [{"path_id": "p1", "nodes": [], "steps": [],
                                     "switch_settings": {}, "configurable": True, "errors": []}],
        "errors": [], "search_states": 3,
    }],
    "errors": [],
}


def test_rejects_empty_pairs(tmp_path):
    n, e = _files(tmp_path)
    r = np.find_netlist_paths(n, e, [])
    assert r["error_code"] == "INVALID_PARAMETERS"


def test_rejects_non_positive_port(tmp_path):
    n, e = _files(tmp_path)
    r = np.find_netlist_paths(n, e, [{"input": 0, "output": 2}])
    assert r["error_code"] == "INVALID_PARAMETERS"
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": "2"}])
    assert r["error_code"] == "INVALID_PARAMETERS"


def test_rejects_same_port(tmp_path):
    n, e = _files(tmp_path)
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 1}])
    assert r["error_code"] == "INVALID_PARAMETERS"


def test_rejects_missing_files(tmp_path):
    _n, e = _files(tmp_path)
    r = np.find_netlist_paths(str(tmp_path / "nope.log"), e, [{"input": 1, "output": 2}])
    assert r["error_code"] == "FILE_NOT_FOUND"


def test_rejects_bad_budget(tmp_path):
    n, e = _files(tmp_path)
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}], max_paths=-1)
    assert r["error_code"] == "INVALID_PARAMETERS"


def test_success_passthrough_and_summary(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["success"] is True
    assert r["summary"]["ports"] == [1, 2]
    assert r["summary"]["total_paths"] == 1
    assert r["summary"]["pairs"][0]["status"] == "ok"
    assert r["result"] is _RESULT  # 全量透传


def test_pairs_joined_into_dash_format(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    captured = {}
    monkeypatch.setattr(np, "run_exe", lambda exe, args, t: (
        captured.update(pairs=args[args.index("--pairs") + 1]),
        _stub_run(_RESULT)(exe, args, t),
    )[1])
    np.find_netlist_paths(n, e, [{"input": 1, "output": 2}, {"input": 2, "output": 1}])
    assert captured["pairs"] == "1-2,2-1"


def test_nonzero_rc_maps_to_failure(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT, rc=1))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["success"] is False
    assert "未完全成功" in r["message"]


def test_incomplete_pair_in_summary(tmp_path, monkeypatch):
    """某端口对 complete=false 时，摘要要如实反映，且 message 点名不完整。"""
    n, e = _files(tmp_path)
    res = {"ports": [1, 2],
           "results": [{"input_port": 1, "output_port": 2, "status": "incomplete",
                        "complete": False, "path_count": 5, "paths": [], "errors": []}],
           "errors": []}
    monkeypatch.setattr(np, "run_exe", _stub_run(res, rc=1))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["success"] is False
    assert r["summary"]["pairs"][0]["complete"] is False
    assert "不完整" in r["message"]


def test_configuration_error_is_named(tmp_path, monkeypatch):
    """路径存在但开关状态算不出（如位宽不足）时，message 要点名这一档，不能只说“未完全成功”。"""
    n, e = _files(tmp_path)
    res = {"ports": [1, 2],
           "results": [{"input_port": 1, "output_port": 2, "status": "configuration_error",
                        "complete": True, "path_count": 1, "paths": [], "errors": []}],
           "errors": []}
    monkeypatch.setattr(np, "run_exe", _stub_run(res, rc=1))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["success"] is False
    assert r["summary"]["pairs"][0]["status"] == "configuration_error"
    assert "开关状态无法确定" in r["message"]


def test_timeout_is_retryable(tmp_path, monkeypatch):
    n, e = _files(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("超时了")
    monkeypatch.setattr(np, "run_exe", boom)
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["error_code"] == "TOOL_TIMEOUT" and r["retryable"] is True


def test_output_written_to_default_dir(tmp_path, monkeypatch):
    """不传 output_path → 落 default_result_path（默认产物目录）并回传该路径。"""
    n, e = _files(tmp_path)
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    out = Path(r["output_path"])
    assert out.name == "find_netlist_paths_auto.json"
    assert json.loads(out.read_text(encoding="utf-8")) == _RESULT


def test_output_explicit_path(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT))
    out = tmp_path / "sub" / "r.json"
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}], output_path=str(out))
    assert Path(r["output_path"]) == out.resolve()
    assert json.loads(out.read_text(encoding="utf-8")) == _RESULT


def test_output_path_must_be_file(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}], output_path=str(tmp_path))
    assert r["error_code"] == "INVALID_PARAMETERS"


def test_output_write_failure_is_structured(tmp_path, monkeypatch):
    n, e = _files(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")          # 父路径是文件 → mkdir 必失败
    monkeypatch.setattr(np, "run_exe", _stub_run(_RESULT))
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}],
                              output_path=str(blocker / "r.json"))
    assert r["error_code"] == "WRITE_FAILED"


def test_failure_result_still_written(tmp_path, monkeypatch):
    """rc=1（如位宽不足）也要落盘：results 里的部分路径与 errors 是排障依据。"""
    n, e = _files(tmp_path)
    res = {"ports": [1, 2],
           "results": [{"input_port": 1, "output_port": 2, "status": "configuration_error",
                        "complete": True, "path_count": 1, "paths": [], "errors": []}],
           "errors": []}
    monkeypatch.setattr(np, "run_exe", _stub_run(res, rc=1))
    out = tmp_path / "bad.json"
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}], output_path=str(out))
    assert r["success"] is False
    assert json.loads(out.read_text(encoding="utf-8")) == res


def test_exe_needs_elevation(tmp_path, monkeypatch):
    """WinError 740（exe 清单要求管理员）要转成结构化错误码，不能抛原始 OSError。"""
    n, e = _files(tmp_path)
    from servers.simulation_tool.config import ExeNeedsElevation

    def boom(*a, **k):
        raise ExeNeedsElevation("exe 需要提权")
    monkeypatch.setattr(np, "run_exe", boom)
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["error_code"] == "EXE_NEEDS_ELEVATION"
    assert r["retryable"] is True and r["details"]["winerror"] == 740


def test_exe_not_found(tmp_path, monkeypatch):
    n, e = _files(tmp_path)

    def boom(*a, **k):
        raise FileNotFoundError("exe 不存在")
    monkeypatch.setattr(np, "run_exe", boom)
    r = np.find_netlist_paths(n, e, [{"input": 1, "output": 2}])
    assert r["error_code"] == "EXE_NOT_FOUND"
