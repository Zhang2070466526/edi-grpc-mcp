"""测试 ANSYS HFSS 模块 —— 队列迁移后的逻辑（mock COM/AEDT，不依赖真实 AEDT）。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest



def _patch_com():
    """屏蔽 COM 初始化（com_session 内部调 config.pythoncom），返回两个 patch。"""
    return (
        patch("servers.ansys.config.pythoncom.CoInitialize"),
        patch("servers.ansys.config.pythoncom.CoUninitialize"),
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
        assert r["error_code"] == "PROJECT_NOT_OPEN"

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
        assert r["error_code"] == "DESIGN_NOT_FOUND"

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
             patch("servers.ansys.run_analysis.get_setup_module", return_value=module):
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
        assert r["error_code"] == "AEDT_NOT_RUNNING"

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
        assert r["error_code"] == "ANALYSIS_BUSY"


def test_attach_aedt_requires_running_instance():
    """AEDT 未运行 ⇒ 直接报错，**一个 COM 通道都不许碰**。

    `Dispatch` 在实例不存在时会新建一个，所以没有这层守卫就违反了
    "_attach_aedt 只附着、绝不创建"的约定。
    """
    from servers.ansys import config as C

    touched: list[str] = []

    with patch.object(C, "aedt_is_running", return_value=False), \
         patch.object(C, "GetActiveObject", side_effect=lambda p: touched.append(p)), \
         patch.object(C, "Dispatch", side_effect=lambda p: touched.append(p)), \
         pytest.raises(RuntimeError) as ei:
        C._attach_aedt()

    assert touched == [], f"未运行时不该碰任何 COM 通道，实际碰了 {touched}"
    assert "绝不创建新实例" in str(ei.value)


def test_attach_aedt_falls_back_to_dispatch_when_rot_empty():
    """AEDT 2020 R2 实测：`GetActiveObject` 两个 PROGID 都 `MK_E_UNAVAILABLE`
    （AEDT 不注册 ROT），必须靠 `Dispatch` 走 CLSID 连上已运行实例。
    """
    from servers.ansys import config as C

    good_desktop = MagicMock()
    good_desktop.GetProjectList.return_value = ["Project5"]
    good_app = MagicMock()
    good_app.GetAppDesktop.return_value = good_desktop

    tried: list[tuple[str, str]] = []

    def ga(progid):
        tried.append(("GetActiveObject", progid))
        raise OSError("MK_E_UNAVAILABLE (操作无法使用)")

    def di(progid):
        tried.append(("Dispatch", progid))
        return good_app

    with patch.object(C, "aedt_is_running", return_value=True), \
         patch.object(C, "GetActiveObject", side_effect=ga), \
         patch.object(C, "Dispatch", side_effect=di):
        app, desktop = C._attach_aedt()

    assert app is good_app and desktop is good_desktop
    assert ("GetActiveObject", C._COM_PROGIDS[0]) in tried
    assert ("Dispatch", C._COM_PROGIDS[0]) in tried, tried


def test_attach_aedt_skips_shell_object_and_tries_next_progid():
    """拿到对象但方法全炸（瘦壳）⇒ 探活拦下，继续试下一个 PROGID。"""
    from servers.ansys import config as C

    good_desktop = MagicMock()
    good_desktop.GetProjectList.return_value = ["Project8", "HFSS_MultiDesign_Test"]
    good_app = MagicMock()
    good_app.GetAppDesktop.return_value = good_desktop

    def getter(progid):
        if progid == C._COM_PROGIDS[0]:
            shell = MagicMock()
            shell.GetAppDesktop.return_value = MagicMock(
                GetProjectList=MagicMock(side_effect=AttributeError("shell object"))
            )
            return shell
        return good_app

    with patch.object(C, "aedt_is_running", return_value=True), \
         patch.object(C, "GetActiveObject", side_effect=getter), \
         patch.object(C, "Dispatch", side_effect=getter):
        app, desktop = C._attach_aedt()

    assert app is good_app
    assert desktop is good_desktop, "两个通道对第一个 PROGID 都是瘦壳时，必须换到第二个 PROGID"


def test_attach_aedt_error_keeps_each_attempt_reason():
    """全都附着不上时，RuntimeError 要带每个 PROGID × 通道的真实原因。"""
    from servers.ansys import config as C

    def boom(progid):
        raise OSError(f"cannot attach {progid}")

    with patch.object(C, "aedt_is_running", return_value=True), \
         patch.object(C, "GetActiveObject", side_effect=boom), \
         patch.object(C, "Dispatch", side_effect=boom), \
         pytest.raises(RuntimeError) as ei:
        C._attach_aedt()

    msg = str(ei.value)
    assert "AnsoftHfss.HfssScriptInterface" in msg and "Ansoft.ElectronicsDesktop" in msg
    assert "via GetActiveObject" in msg and "via Dispatch" in msg
    assert msg.count("cannot attach") == 4, msg      # 2 PROGID × 2 通道


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
