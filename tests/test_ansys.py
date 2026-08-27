"""测试 ANSYS HFSS 模块 —— 队列迁移后的逻辑（mock COM/AEDT，不依赖真实 AEDT）。"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _patch_com():
    """屏蔽 COM 初始化，返回两个 patch（CoInitialize / CoUninitialize）。"""
    return (
        patch("servers.ansys.run_analysis.pythoncom.CoInitialize"),
        patch("servers.ansys.run_analysis.pythoncom.CoUninitialize"),
    )


class TestValidateSetups:
    def test_project_not_open(self):
        from servers.ansys import run_analysis
        desktop = MagicMock()
        desktop.GetProjectList.return_value = ["OtherProject"]
        co_init, co_uninit = _patch_com()
        with co_init, co_uninit, \
             patch("servers.ansys.run_analysis._attach_aedt", return_value=(None, desktop)):
            r = run_analysis._validate_setups("C:/demo.aedt", "Design1")
        assert r["success"] is False
        assert r["status"] == "project_not_open"

    def test_design_not_found(self):
        from servers.ansys import run_analysis
        desktop = MagicMock()
        desktop.GetProjectList.return_value = ["demo"]
        project = MagicMock()
        project.SetActiveDesign.side_effect = Exception("no design")
        desktop.SetActiveProject.return_value = project
        co_init, co_uninit = _patch_com()
        with co_init, co_uninit, \
             patch("servers.ansys.run_analysis._attach_aedt", return_value=(None, desktop)):
            r = run_analysis._validate_setups("C:/demo.aedt", "Design1")
        assert r["success"] is False
        assert r["status"] == "design_not_found"

    def test_success_returns_setups(self):
        from servers.ansys import run_analysis
        desktop = MagicMock()
        desktop.GetProjectList.return_value = ["demo"]
        project = MagicMock()
        design = MagicMock()
        project.SetActiveDesign.return_value = design
        desktop.SetActiveProject.return_value = project
        module = MagicMock()
        module.GetSetups.return_value = ["Setup1", "Setup2"]
        co_init, co_uninit = _patch_com()
        with co_init, co_uninit, \
             patch("servers.ansys.run_analysis._attach_aedt", return_value=(None, desktop)), \
             patch("servers.ansys.run_analysis.get_setup_module", return_value=(module, None)):
            r = run_analysis._validate_setups("C:/demo.aedt", "Design1")
        assert r["success"] is True
        assert r["setups"] == ["Setup1", "Setup2"]


class TestGetStatus:
    def test_task_not_found(self):
        from servers.ansys import run_analysis
        with patch.object(run_analysis.hfss_runner, "snapshot", return_value=None):
            r = run_analysis.get_hfss_analysis_status("nope")
        assert r["success"] is False
        assert r["error_code"] == "TASK_NOT_FOUND"

    def test_succeeded(self):
        from servers.ansys import run_analysis
        snap = {
            "task_id": "t1", "status": "SUCCEEDED", "message": "等待执行",
            "result": {"result_directory": "C:/demo.aedtresults", "result_verified": True,
                       "outcome_known": True, "task_success": True},
            "error": None, "metadata": {"project_name": "demo", "design_name": "D1",
                                        "setup_name": "Setup1", "project_path": "C:/demo.aedt"},
            "created_at": 1.0, "started_at": 1.1, "finished_at": 2.0,
        }
        with patch.object(run_analysis.hfss_runner, "snapshot", return_value=snap):
            r = run_analysis.get_hfss_analysis_status("t1")
        assert r["success"] is True
        assert r["status"] == "SUCCEEDED"
        assert r["outcome_known"] is True
        assert r["project_name"] == "demo"

    def test_unknown_when_succeeded_but_not_verified(self):
        from servers.ansys import run_analysis
        snap = {
            "task_id": "t1", "status": "SUCCEEDED", "message": "",
            "result": {"result_directory": "", "result_verified": False,
                       "outcome_known": False, "task_success": None},
            "error": None, "metadata": {},
            "created_at": 1.0, "started_at": 1.1, "finished_at": 2.0,
        }
        with patch.object(run_analysis.hfss_runner, "snapshot", return_value=snap):
            r = run_analysis.get_hfss_analysis_status("t1")
        assert r["status"] == "UNKNOWN"  # 兼容旧状态语义

    def test_failed_sets_outcome_known(self):
        from servers.ansys import run_analysis
        snap = {
            "task_id": "t1", "status": "FAILED", "message": "",
            "result": None, "error": "Analyze failed", "metadata": {},
            "created_at": 1.0, "started_at": 1.1, "finished_at": 2.0,
        }
        with patch.object(run_analysis.hfss_runner, "snapshot", return_value=snap):
            r = run_analysis.get_hfss_analysis_status("t1")
        assert r["status"] == "FAILED"
        assert r["outcome_known"] is True
        assert r["task_success"] is False


class TestStartAsync:
    def test_aedt_not_running(self):
        from servers.ansys import run_analysis
        with patch("servers.ansys.run_analysis.validate_file", return_value="C:/demo.aedt"), \
             patch("servers.ansys.run_analysis.aedt_is_running", return_value=False):
            r = run_analysis.start_hfss_analysis_async("C:/demo.aedt", "D1", "Setup1")
        assert r["success"] is False
        assert r["status"] == "aedt_not_running"

    def test_busy_when_pending(self):
        from servers.ansys import run_analysis
        validation = {"success": True, "setups": ["Setup1"]}
        with patch("servers.ansys.run_analysis.validate_file", return_value="C:/demo.aedt"), \
             patch("servers.ansys.run_analysis.aedt_is_running", return_value=True), \
             patch("servers.ansys.run_analysis._validate_setups", return_value=validation), \
             patch.object(run_analysis.hfss_runner, "submit", return_value=None), \
             patch.object(run_analysis.hfss_runner, "pending_count", return_value=1):
            r = run_analysis.start_hfss_analysis_async("C:/demo.aedt", "D1", "Setup1")
        assert r["success"] is False
        assert r["status"] == "analysis_busy"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
