"""测试 CST 模块 —— 共享函数、静态解析、查询工具返回结构、导出流程（mock）。

不依赖本机 CST 环境：纯函数直接测，会话/结果 API 用 mock 打桩。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 共享函数（cst_api.py） ──

class TestStatusPayload:
    def test_running_fields(self):
        from servers.cst.cst_api import status_payload
        snap = {"status": "RUNNING", "message": "等待执行", "error": None,
                "started_at": 1.0, "finished_at": None}
        p = status_payload("t1", snap)
        assert p["success"] is True
        assert p["task_id"] == "t1"
        assert p["status"] == "RUNNING"
        assert p["completed"] is False
        assert p["message"] == "等待执行"
        assert p["error"] is None
        assert p["started_at"] == 1.0
        assert p["finished_at"] is None

    def test_completed_flag_from_finished_at(self):
        from servers.cst.cst_api import status_payload
        snap = {"status": "SUCCEEDED", "message": "done", "error": None,
                "started_at": 1.0, "finished_at": 2.0}
        p = status_payload("t1", snap)
        assert p["completed"] is True


class TestToWritableCopy:
    def test_writable_returns_self(self, tmp_path, monkeypatch):
        from servers.cst.cst_api import to_writable_copy
        f = tmp_path / "m.cst"
        f.write_text("x")
        monkeypatch.setattr("servers.cst.cst_api.os.access", lambda *a: True)
        assert to_writable_copy(str(f)) == str(f)

    def test_readonly_copies_to_temp(self, tmp_path, monkeypatch):
        from servers.cst.cst_api import to_writable_copy
        f = tmp_path / "model.cst"
        f.write_text("x")
        monkeypatch.setattr("servers.cst.cst_api.os.access", lambda *a: False)
        monkeypatch.setattr("servers.cst.cst_api.tempfile.gettempdir", lambda: str(tmp_path))
        copied = to_writable_copy(str(f))
        assert copied != str(f)
        assert str(tmp_path) in copied
        assert Path(copied).is_file()


# ── CstResultExporter 静态解析方法 ──

class TestSParamIndices:
    def test_plain(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._s_param_indices("S2,1") == (2, 1)

    def test_with_path(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._s_param_indices("1D Results\\S-Parameters\\S1,1") == (1, 1)

    def test_with_spaces(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._s_param_indices("S1, 1") == (1, 1)

    def test_invalid_returns_zero(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._s_param_indices("Sxx") == (0, 0)

    def test_orders_by_port(self):
        from servers.cst.result_export import CstResultExporter
        items = ["S2,1", "S1,2", "S1,1"]
        assert sorted(items, key=CstResultExporter._s_param_indices) == ["S1,1", "S1,2", "S2,1"]


class TestFreqUnit:
    def test_ghz(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._freq_unit("Frequency (GHz)") == "GHz"

    def test_default_hz(self):
        from servers.cst.result_export import CstResultExporter
        assert CstResultExporter._freq_unit("") == "Hz"


# ── 远场结果检测（mock 结果树） ──

class TestHasFarfieldResult:
    def _mock_3d_results(self, exporter, tree_items):
        mock_pf = MagicMock()
        mock_3d = MagicMock()
        mock_3d.get_tree_items.return_value = tree_items
        return patch.object(exporter, "_open_3d_results", return_value=(mock_pf, mock_3d))

    def test_true_when_farfield_in_tree(self):
        from servers.cst.result_export import CstResultExporter
        exporter = CstResultExporter()
        with self._mock_3d_results(exporter, ["1D Results\\Farfields\\farfield (f=1)"]):
            assert exporter._has_farfield_result("x.cst") is True

    def test_false_when_only_s_parameter(self):
        from servers.cst.result_export import CstResultExporter
        exporter = CstResultExporter()
        with self._mock_3d_results(exporter, ["1D Results\\S-Parameters\\S1,1"]):
            assert exporter._has_farfield_result("x.cst") is False


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


# ── 远场导出流程（mock 会话，验证「检测→求解→导出→关闭」分支） ──

class TestExportFarfieldFlow:
    def test_solves_when_unsolved(self, tmp_path):
        from servers.cst.result_export import CstResultExporter
        exporter = CstResultExporter()
        exporter._api = MagicMock()
        mock_de, mock_prj = MagicMock(), MagicMock()
        mock_prj.modeler.run_solver.return_value = True
        exporter._api.open_session.return_value = (mock_de, mock_prj)

        with patch.object(exporter, "_has_farfield_result", return_value=False), \
             patch.object(exporter, "_run_farfield_vba", return_value=["f1.txt"]), \
             patch("servers.cst.result_export.to_writable_copy", return_value="writable.cst"):
            result = exporter.export_farfield("x.cst", str(tmp_path))

        assert result == ["f1.txt"]
        mock_prj.modeler.run_solver.assert_called_once()
        mock_prj.save.assert_called_once()
        exporter._api.close_session.assert_called_once_with(mock_de, mock_prj)

    def test_skips_solve_when_solved(self, tmp_path):
        from servers.cst.result_export import CstResultExporter
        exporter = CstResultExporter()
        exporter._api = MagicMock()
        mock_de, mock_prj = MagicMock(), MagicMock()
        exporter._api.open_session.return_value = (mock_de, mock_prj)

        with patch.object(exporter, "_has_farfield_result", return_value=True), \
             patch.object(exporter, "_run_farfield_vba", return_value=["f1.txt"]), \
             patch("servers.cst.result_export.to_writable_copy", return_value="writable.cst"):
            result = exporter.export_farfield("x.cst", str(tmp_path))

        assert result == ["f1.txt"]
        mock_prj.modeler.run_solver.assert_not_called()
        mock_prj.save.assert_not_called()
        exporter._api.close_session.assert_called_once_with(mock_de, mock_prj)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
