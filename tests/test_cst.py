"""测试 CST 工具层 —— 作业调度（子进程协议）、工具信封、查询结构。

不依赖本机 CST：子进程与执行器都打桩，只验证协议与信封。
执行器本身的纯函数/3.6 语法守在 tests/test_cst_worker.py。
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 查询工具返回结构（mock cst_runner.snapshot） ──

def _snap(**overrides):
    snap = {"status": "RUNNING", "message": "等待执行", "error": None,
            "started_at": 1.0, "finished_at": None, "result": None}
    snap.update(overrides)
    return snap


class TestSolveQuery:
    def test_running(self):
        from servers.cst import simulate
        with patch.object(simulate.cst_runner, "snapshot", return_value=_snap()):
            r = simulate.cst_solve_query("t1")
        assert r["success"] is True
        assert r["completed"] is False
        assert r["message"] == "求解进行中"

    def test_succeeded(self):
        from servers.cst import simulate
        snap = _snap(status="SUCCEEDED", finished_at=2.0, result="C:/x.cst")
        with patch.object(simulate.cst_runner, "snapshot", return_value=snap):
            r = simulate.cst_solve_query("t1")
        assert r["completed"] is True
        assert r["model_path"] == "C:/x.cst"

    def test_failed(self):
        from servers.cst import simulate
        snap = _snap(status="FAILED", finished_at=2.0, error="run_solver returned False")
        with patch.object(simulate.cst_runner, "snapshot", return_value=snap):
            r = simulate.cst_solve_query("t1")
        assert r["success"] is False
        assert r["error_code"] == "CST_SOLVE_FAILED"

    def test_not_found(self):
        from servers.cst import simulate
        with patch.object(simulate.cst_runner, "snapshot", return_value=None):
            r = simulate.cst_solve_query("t1")
        assert r["success"] is False
        assert r["error_code"] == "TASK_NOT_FOUND"


class TestExportFarfieldQuery:
    def test_running(self):
        from servers.cst import result_export
        with patch.object(result_export.cst_runner, "snapshot", return_value=_snap()):
            r = result_export.cst_export_farfield_query("t1")
        assert r["completed"] is False
        assert r["message"] == "导出进行中"

    def test_succeeded(self):
        from servers.cst import result_export
        snap = _snap(status="SUCCEEDED", finished_at=2.0, result=["C:/f1.txt"])
        with patch.object(result_export.cst_runner, "snapshot", return_value=snap):
            r = result_export.cst_export_farfield_query("t1")
        assert r["completed"] is True
        assert r["farfield_txts"] == ["C:/f1.txt"]

    def test_failed(self):
        from servers.cst import result_export
        snap = _snap(status="FAILED", finished_at=2.0, error="execute_vba_code returned False")
        with patch.object(result_export.cst_runner, "snapshot", return_value=snap):
            r = result_export.cst_export_farfield_query("t1")
        assert r["success"] is False
        assert r["error_code"] == "CST_EXPORT_FARFIELD_FAILED"

    def test_not_found(self):
        from servers.cst import result_export
        with patch.object(result_export.cst_runner, "snapshot", return_value=None):
            r = result_export.cst_export_farfield_query("t1")
        assert r["error_code"] == "TASK_NOT_FOUND"

    def test_empty_result_message_is_honest(self):
        """success=true + 空列表 不该说成「已导出」（模型没配 farfield 监视器）。"""
        from servers.cst import result_export
        snap = _snap(status="SUCCEEDED", finished_at=2.0, result=[])
        with patch.object(result_export.cst_runner, "snapshot", return_value=snap):
            r = result_export.cst_export_farfield_query("t1")
        assert r["success"] is True
        assert r["farfield_txts"] == []
        assert "没有远场结果" in r["message"]

    def test_nonempty_result_keeps_original_message(self):
        from servers.cst import result_export
        snap = _snap(status="SUCCEEDED", finished_at=2.0, result=["C:/f.txt"])
        with patch.object(result_export.cst_runner, "snapshot", return_value=snap):
            r = result_export.cst_export_farfield_query("t1")
        assert r["message"] == "远场方向图已导出"

    def test_running_message_untouched(self):
        from servers.cst import result_export
        with patch.object(result_export.cst_runner, "snapshot", return_value=_snap()):
            r = result_export.cst_export_farfield_query("t1")
        assert r["message"] == "导出进行中"


# ── CST 解释器定位 ──

class TestCstPythonPath:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        from servers.cst import cst_api
        fake = tmp_path / "python.exe"
        fake.write_text("x")
        monkeypatch.setenv("CST_PYTHON_PATH", str(fake))
        assert cst_api.cst_python_path() == str(fake)

    def test_env_override_missing_file_raises(self, tmp_path, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", str(tmp_path / "nope.exe"))
        with pytest.raises(RuntimeError) as exc:
            cst_api.cst_python_path()
        assert "CST_PYTHON_PATH" in str(exc.value)

    def test_registry_fallback(self, tmp_path, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.delenv("CST_PYTHON_PATH", raising=False)
        py = tmp_path / "python" / "python.exe"
        py.parent.mkdir(parents=True)
        py.write_text("x")
        monkeypatch.setattr(cst_api, "_cst_install_dir", lambda: str(tmp_path))
        assert cst_api.cst_python_path() == str(py)

    def test_registry_fallback_missing_raises(self, tmp_path, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.delenv("CST_PYTHON_PATH", raising=False)
        monkeypatch.setattr(cst_api, "_cst_install_dir", lambda: str(tmp_path))
        with pytest.raises(RuntimeError) as exc:
            cst_api.cst_python_path()
        assert "CST_PYTHON_PATH" in str(exc.value)


# ── 子进程作业协议 ──

def _fake_runner(write_result=None, returncode=0, out=b"", err=b"", inspect=None):
    """构造 subprocess.run 替身：按作业文件生成结果文件，并记录调用现场。

    注意：形参名不能叫 stdout/stderr —— 那会覆盖被调用方传进来的 PIPE 哨兵值，
    导致 CompletedProcess 里拿到的不是本替身想给的字节。
    """
    def fake_run(cmd, **kwargs):
        if inspect is not None:
            inspect["cmd"] = cmd
            inspect["timeout"] = kwargs.get("timeout")
        if write_result is not None:
            job = json.loads(Path(cmd[cmd.index("--job") + 1]).read_text(encoding="utf-8"))
            if inspect is not None:
                inspect["job"] = job
            Path(job["result_path"]).write_text(json.dumps(write_result), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, out, err)
    return fake_run


class TestRunCstJob:
    def test_success_returns_result_field(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", __file__)  # 任意存在的文件当解释器
        info = {}
        monkeypatch.setattr(cst_api.subprocess, "run",
                            _fake_runner({"success": True, "op": "solve", "result": "C:/x.cst"}, inspect=info))
        assert cst_api.run_cst_job("solve", model_path="C:/x.cst") == "C:/x.cst"
        # 作业参数应原样落到 job.json，并带上 op 与 result_path
        assert info["job"]["op"] == "solve"
        assert info["job"]["model_path"] == "C:/x.cst"
        assert info["job"]["result_path"].endswith("result.json")
        # 命令行 = [python, cst_worker.py, --job, <path>]
        assert info["cmd"][1].endswith("cst_worker.py")
        assert info["cmd"][2] == "--job"
        assert info["timeout"] == cst_api.JOB_TIMEOUT_SECONDS

    def test_failure_payload_raises_with_reason(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", __file__)
        monkeypatch.setattr(cst_api.subprocess, "run", _fake_runner({
            "success": False, "op": "solve", "error": "run_solver returned False",
            "traceback": "Traceback (most recent call last):\n  ...",
        }))
        with pytest.raises(RuntimeError) as exc:
            cst_api.run_cst_job("solve", model_path="C:/x.cst")
        assert "run_solver returned False" in str(exc.value)
        assert "Traceback" in str(exc.value)

    def test_no_result_file_uses_process_output(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", __file__)
        monkeypatch.setattr(cst_api.subprocess, "run",
                            _fake_runner(None, returncode=1, err=b"ImportError: cst gate"))
        with pytest.raises(RuntimeError) as exc:
            cst_api.run_cst_job("solve", model_path="C:/x.cst")
        assert "未产出结果" in str(exc.value)
        assert "ImportError" in str(exc.value)

    def test_timeout_raises(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", __file__)

        def fake_run(cmd, stdout=None, stderr=None, timeout=None):
            raise subprocess.TimeoutExpired(cmd, timeout or 1)

        monkeypatch.setattr(cst_api.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError) as exc:
            cst_api.run_cst_job("solve", model_path="C:/x.cst", timeout=30)
        assert "超时" in str(exc.value)

    def test_temp_job_dir_is_removed(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", __file__)
        info = {}
        monkeypatch.setattr(cst_api.subprocess, "run",
                            _fake_runner({"success": True, "op": "solve", "result": "r"}, inspect=info))
        cst_api.run_cst_job("solve", model_path="C:/x.cst")
        job_dir = str(Path(info["job"]["result_path"]).parent)
        assert not Path(job_dir).exists()

    def test_missing_python_raises_before_spawn(self, monkeypatch):
        from servers.cst import cst_api
        monkeypatch.setenv("CST_PYTHON_PATH", "C:/definitely/not/here/python.exe")
        called = []
        monkeypatch.setattr(cst_api.subprocess, "run", lambda *a, **k: called.append(a))
        with pytest.raises(RuntimeError):
            cst_api.run_cst_job("solve", model_path="C:/x.cst")
        assert called == []


# ── 工具层 -> 作业调度 ──

class TestJobDispatch:
    def test_simulator_solve_dispatches_solve_op(self):
        from servers.cst import simulate
        with patch.object(simulate, "run_cst_job", return_value="C:/writable.cst") as job:
            assert simulate.CstSimulator().solve("C:/x.cst") == "C:/writable.cst"
        job.assert_called_once_with("solve", model_path="C:/x.cst")

    def test_solve_async_returns_task_id(self, tmp_path):
        from servers.cst import simulate
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(simulate._simulator, "submit_solve", return_value="cst123"):
            r = simulate.cst_solve_async(str(model))
        assert r["success"] is True
        assert r["task_id"] == "cst123"
        assert r["status"] == "QUEUED"

    def test_solve_async_queue_full(self, tmp_path):
        from servers.cst import simulate
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(simulate._simulator, "submit_solve", return_value=None):
            r = simulate.cst_solve_async(str(model))
        assert r["success"] is False
        assert r["error_code"] == "CST_QUEUE_FULL"

    def test_solve_async_rejects_missing_file(self, tmp_path):
        from servers.cst import simulate
        r = simulate.cst_solve_async(str(tmp_path / "nope.cst"))
        assert r["success"] is False

    def test_export_snp_dispatches_with_abspath(self):
        from servers.cst import result_export
        with patch.object(result_export, "run_cst_job", return_value="C:/out/m.s2p") as job:
            path = result_export.CstResultExporter().export_snp("C:/x.cst", "C:/out", 2)
        assert path == "C:/out/m.s2p"
        kwargs = job.call_args[1]
        assert job.call_args[0][0] == "export_snp"
        assert kwargs["output_dir"] == "C:/out"
        assert kwargs["port_count"] == 2
        assert kwargs["model_path"].lower().endswith("x.cst")

    def test_export_farfield_dispatches(self):
        from servers.cst import result_export
        with patch.object(result_export, "run_cst_job", return_value=["C:/out/f.txt"]) as job:
            out = result_export.CstResultExporter().export_farfield("C:/x.cst", "C:/out")
        assert out == ["C:/out/f.txt"]
        assert job.call_args[0][0] == "export_farfield"

    def test_export_snp_tool_wraps_failure(self, tmp_path):
        from servers.cst import result_export
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(result_export._result_exporter, "export_snp",
                          side_effect=RuntimeError("No S-Parameter results found")):
            r = result_export.cst_export_snp(str(model))
        assert r["success"] is False
        assert r["error_code"] == "CST_EXPORT_FAILED"
        assert "No S-Parameter" in r["message"]

    def test_export_snp_tool_success(self, tmp_path):
        from servers.cst import result_export
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(result_export._result_exporter, "export_snp", return_value="C:/o/m.s2p"):
            r = result_export.cst_export_snp(str(model))
        assert r["success"] is True
        assert r["snp_path"] == "C:/o/m.s2p"

    def test_export_farfield_defaults_output_dir_to_model_dir(self, tmp_path):
        """output_dir 留空时按模型所在目录投递（不依赖后台线程时序：拦 submit）。"""
        from servers.cst import result_export
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(result_export.cst_runner, "submit", return_value="t1") as submit:
            r = result_export.cst_export_farfield(str(model))
        assert r["success"] is True
        assert submit.call_args[0][2] == str(tmp_path)

    def test_export_farfield_queue_full(self, tmp_path):
        from servers.cst import result_export
        model = tmp_path / "m.cst"
        model.write_text("x")
        with patch.object(result_export.cst_runner, "submit", return_value=None):
            r = result_export.cst_export_farfield(str(model))
        assert r["error_code"] == "CST_QUEUE_FULL"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
