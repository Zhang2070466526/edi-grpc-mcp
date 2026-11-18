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


def test_launch_aedt_already_running_waits_for_com(tmp_path, monkeypatch):
    """「AEDT 已运行」分支也要按 `timeout_seconds` 轮询等 COM，并返回 pid/pids。

    2026-11-16 复核：原实现只查一次 `query_desktop_state()` 就返回，且不带 pid ——
    AEDT 刚启动、COM 还在加载时会立刻回 `com_ready=false`，调用方误判「AEDT 不可用」；
    两个分支返回结构也不一致（started 有 pid、already_running 没有）。
    """
    from servers.ansys import project_manage as P

    fake_exe = tmp_path / "ansysedt.exe"
    fake_exe.write_text("x", encoding="utf-8")

    calls = {"n": 0}

    def fake_state():
        calls["n"] += 1
        return {"connected": calls["n"] >= 3}      # 第 3 次查询才就绪

    monkeypatch.setattr(P, "aedt_is_running", lambda: True)
    monkeypatch.setattr(P, "get_aedt_pids", lambda: [111, 222])
    monkeypatch.setattr(P, "query_desktop_state", fake_state)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)

    r = P.launch_aedt(aedt_path=str(fake_exe), timeout_seconds=10)
    assert r["status"] == "already_running"
    assert r["com_ready"] is True, "必须轮询到 COM 就绪，而不是一查就返回"
    assert r["pid"] == 111 and r["pids"] == [111, 222]
    assert calls["n"] >= 3, calls


def test_launch_aedt_already_running_times_out_with_pid(tmp_path, monkeypatch):
    """COM 一直不就绪 ⇒ `com_ready=false`，但仍 `success=true` 且**带 pid**（可定位实例）。"""
    from servers.ansys import project_manage as P

    fake_exe = tmp_path / "ansysedt.exe"
    fake_exe.write_text("x", encoding="utf-8")

    clock = {"v": 0.0}

    def fake_monotonic():
        clock["v"] += 5.0
        return clock["v"]

    monkeypatch.setattr(P, "aedt_is_running", lambda: True)
    monkeypatch.setattr(P, "get_aedt_pids", lambda: [333])
    monkeypatch.setattr(P, "query_desktop_state", lambda: {"connected": False})
    monkeypatch.setattr(P.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)

    r = P.launch_aedt(aedt_path=str(fake_exe), timeout_seconds=2)
    assert r["success"] is True
    assert r["com_ready"] is False
    assert r["pid"] == 333 and r["pids"] == [333]
    assert "未在时间内就绪" in r["message"]


def test_launch_aedt_does_not_hold_lock_while_waiting_for_com(tmp_path, monkeypatch):
    """等 COM 期间**不能**持 `_AEDT_LOCK`，否则 open/close/query 全被堵住。

    2026-11-16 复核：原实现把等待放在 `with _AEDT_LOCK` 里，最长持锁
    `timeout_seconds`（上限 120 s）；改成「锁内只判断+起进程，锁外等待」。
    `_AEDT_LOCK` 是 RLock（可重入），同线程探不出占用，必须用**另一线程**探。
    """
    import threading

    from servers.ansys import project_manage as P
    from servers.ansys.config import _AEDT_LOCK

    fake_exe = tmp_path / "ansysedt.exe"
    fake_exe.write_text("x", encoding="utf-8")

    observed: list[bool] = []

    def fake_state():
        got: dict = {}

        def probe():
            free = _AEDT_LOCK.acquire(blocking=False)
            got["free"] = free
            if free:
                _AEDT_LOCK.release()

        t = threading.Thread(target=probe)
        t.start()
        t.join(timeout=3)
        observed.append(bool(got.get("free")))
        return {"connected": False}

    clock = {"v": 0.0}

    def fast_clock():
        clock["v"] += 5.0
        return clock["v"]

    monkeypatch.setattr(P, "aedt_is_running", lambda: True)
    monkeypatch.setattr(P, "get_aedt_pids", lambda: [1])
    monkeypatch.setattr(P, "query_desktop_state", fake_state)
    monkeypatch.setattr(P.time, "monotonic", fast_clock)
    monkeypatch.setattr(P.time, "sleep", lambda s: None)

    P.launch_aedt(aedt_path=str(fake_exe), timeout_seconds=2)
    assert observed, "探测函数没被调用"
    assert all(observed), f"等 COM 时锁被占用（observed={observed}）—— 会阻塞其它 AEDT 工具"


def test_close_hfss_project_com_only_never_kills(monkeypatch):
    """拆分后：close 只走 COM，任何情况都不杀进程。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_instance_guard", lambda _a: None)
    monkeypatch.setattr(pm, "_com_close_project",
                        lambda *a, **k: {"success": True, "method": "com"})
    monkeypatch.setattr(pm, "_kill_aedt_processes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("close 不该杀进程")))

    r = pm.close_hfss_project(project_name="demo")
    assert r["method"] == "com"


def test_close_hfss_project_not_running(monkeypatch):
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: False)
    r = pm.close_hfss_project(project_name="demo")
    assert r["error_code"] == "AEDT_NOT_RUNNING"


def test_close_hfss_project_com_unavailable_points_to_quit(monkeypatch):
    """COM 附着失败 → COM_UNAVAILABLE（不杀进程），提示改用 quit_aedt(force=true)。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_instance_guard", lambda _a: None)
    monkeypatch.setattr(pm, "_com_close_project", lambda *a, **k: None)     # 附着失败
    monkeypatch.setattr(pm, "_kill_aedt_processes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("close 不该杀进程")))

    r = pm.close_hfss_project(project_name="demo")
    assert r["error_code"] == "COM_UNAVAILABLE"
    assert "quit_aedt(force=true)" in r["hint"]


def test_com_close_project_returns_error_when_closeproject_fails(monkeypatch):
    """附着成功但 `CloseProject` 抛异常 ⇒ 必须返回 `CLOSE_FAILED`，**不能**返回 None。

    返回 None 会让调用方走进程级兜底硬杀 AEDT —— 而这类失败多半是「项目有未保存
    改动 / AEDT 弹了确认框」，硬杀会把改动一起丢掉（2026-11-16 复核）。
    只有**附着阶段**失败才允许返回 None。
    """
    import contextlib

    from servers.ansys import project_manage as P

    class _Desktop:
        def GetProjectList(self):
            return ["demo"]

        def GetActiveProject(self):
            return None

        def CloseProject(self, name):
            raise RuntimeError("project has unsaved changes")

    class _App:
        def GetAppDesktop(self):
            return _Desktop()

    monkeypatch.setattr(P, "_attach_aedt", lambda: (_App(), _Desktop()))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)

    r = P._com_close_project("demo", False, "")
    assert r is not None, "关项目失败不能返回 None（None 会让调用方硬杀进程）"
    assert r["error_code"] == "CLOSE_FAILED"
    assert "已连通但关闭项目失败" in r["message"]
    assert r["details"]["attached"] is True, "要标明'已附着'，区别于附着失败"
    assert "force=true" in r["hint"], "提示要让调用方知道可显式强杀"
    assert "所有" in r["hint"], "hint 必须写明 force 会波及该 AEDT 名下所有项目"
    assert r["details"]["modal_dialogs"] == [], "无 AEDT 时弹框检测应为空"
    assert "仍含 demo" in r["message"], "要说明'事后核对确认仍打开'，而不是猜未保存改动"
    assert r["details"]["open_projects_after"] == ["demo"], "返回体要带事后证据"


class _FakeDesktop:
    """GetProjectList 按次序返回 lists 各项（用完后一直返回最后一项）。"""

    def __init__(self, lists, close_raises=None):
        self._lists = [list(x) for x in lists]
        self._i = 0
        self._close_raises = close_raises
        self.save_calls = []

    def GetProjectList(self):
        idx = min(self._i, len(self._lists) - 1)
        self._i += 1
        return list(self._lists[idx])

    def GetActiveProject(self):
        return None

    def SetActiveProject(self, name):
        outer = self

        class _Proj:
            def Save(self):
                outer.save_calls.append(name)

        return _Proj()

    def CloseProject(self, name):
        if self._close_raises:
            raise self._close_raises


def test_com_close_project_success_when_exception_raised_but_project_gone(monkeypatch):
    """2026-11-18 真实现象：CloseProject 在收尾阶段抛 DISP_E_EXCEPTION，但项目其实已关掉。

    判据必须是「事后核对 open_projects」：否则会把成功报成 CLOSE_FAILED，
    再被 hint 引向 force=true（同机另一个工程会被一起带走）。
    """
    import contextlib

    from servers.ansys import project_manage as P

    desktop = _FakeDesktop([["Project3"], []],
                           close_raises=RuntimeError("DISP_E_EXCEPTION 0x80020009"))
    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(P, "_aedt_modal_dialogs", lambda: [])

    r = P._com_close_project("Project3", False, "")

    assert r["success"] is True, "项目已不在 open_projects 里，就该判成功"
    assert r["closed_despite_exception"] is True
    assert r["project_closed"] is True
    assert r["open_projects_after"] == []
    assert "0x80020009" in r["warning"], "异常文本要作为诊断信息保留"


def test_com_close_project_rejects_unknown_project_name(monkeypatch):
    """名字不存在时直接 NO_SUCH_PROJECT，别让 CloseProject 抛异常再被 hint 引向 force。"""
    import contextlib

    from servers.ansys import project_manage as P

    desktop = _FakeDesktop([["CPW_SL"]], close_raises=RuntimeError("不该被调用"))
    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)

    r = P._com_close_project("Project3", False, "")

    assert r["error_code"] == "NO_SUCH_PROJECT"
    assert r["details"]["open_projects"] == ["CPW_SL"]
    assert "所有" in r["hint"], "hint 要提醒 force 的波及范围"


def test_com_close_project_warns_when_project_path_missing(monkeypatch, tmp_path):
    """project_path 指向不存在的文件（工程从未落盘）时要标注，不能默默按该路径清锁。"""
    import contextlib

    from servers.ansys import project_manage as P

    desktop = _FakeDesktop([["Project3"], []])
    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(P.time, "sleep", lambda _s: None)

    missing = str(tmp_path / "never_saved.aedt")
    r = P._com_close_project("Project3", False, missing)

    assert r["success"] is True
    assert any("不存在" in w for w in r["warnings"]), r["warnings"]
    assert r["lock_exists_after"] is False



def test_looks_like_aedt_dialog_classification():
    """Qt 窗口分类：真实「写入文件失败」框的标题就是光秃秃的 ANSYS Electronics Desktop。

    2026-11-18 实测截图：按「标题含 ansys electronics desktop 即主窗口」会漏掉这个真对话框。
    """
    from servers.ansys import project_manage as P

    assert P._looks_like_aedt_dialog("ANSYS Electronics Desktop", False) is True, "真实报错框"
    assert P._looks_like_aedt_dialog("Save changes?", False) is True
    assert P._looks_like_aedt_dialog("Save changes?", True) is True
    assert P._looks_like_aedt_dialog("HFSS_MultiDesign_Test - HFSS_7 2020.2", False) is False, "主窗"
    assert P._looks_like_aedt_dialog("HFSS_MultiDesign_Test - Ansys Electronics Desktop", False) is False


def test_com_close_project_skips_save_when_file_readonly(monkeypatch, tmp_path):
    """只读 .aedt + save_before_close=True：必须跳过保存并告警。

    否则 AEDT 不抛 Python 异常，而是弹「An error occurred while writing to file …
    Is the file or directory read only?」的 Qt 框，把调用拖住（2026-11-18 实测）。
    """
    import contextlib
    import os

    from servers.ansys import project_manage as P

    epp = tmp_path / "Ro.aedt"
    epp.write_text("x", encoding="utf-8")
    os.chmod(epp, 0o444)                       # Windows 上等价于设置只读属性
    try:
        assert P._is_file_readonly(str(epp)) is True

        desktop = _FakeDesktop([["Ro"], []])
        monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
        monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
        monkeypatch.setattr(P.time, "sleep", lambda _s: None)

        r = P._com_close_project("Ro", True, str(epp))

        assert r["success"] is True
        assert desktop.save_calls == [], "只读文件不该触发 Save（会弹 Qt 写失败框）"
        assert r["readonly_project_file"] is True, "只读状态要作为独立字段回给调用方"
        assert any("只读" in w for w in r["warnings"]), r["warnings"]
        assert any("转告用户" in w for w in r["warnings"]), "要明确要求 agent 转告用户确认"
    finally:
        os.chmod(epp, 0o644)


def test_com_close_project_reports_readonly_even_without_save(monkeypatch, tmp_path):
    """只读工程即使 save_before_close=False（不尝试保存）也必须把只读状态报给调用方。

    否则 agent 只看到 success: true，用户不知道这个工程的改动永远写不回去（2026-11-18 要求）。
    """
    import contextlib
    import os

    from servers.ansys import project_manage as P

    epp = tmp_path / "Ro2.aedt"
    epp.write_text("x", encoding="utf-8")
    os.chmod(epp, 0o444)
    try:
        desktop = _FakeDesktop([["Ro2"], []])
        monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
        monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
        monkeypatch.setattr(P.time, "sleep", lambda _s: None)

        r = P._com_close_project("Ro2", False, str(epp))

        assert r["success"] is True
        assert desktop.save_calls == []
        assert r["readonly_project_file"] is True
        assert any("只读" in w for w in r["warnings"]), r["warnings"]
    finally:
        os.chmod(epp, 0o644)


def test_com_close_project_readonly_flag_false_for_writable(monkeypatch, tmp_path):
    """可写工程的 readonly_project_file 必须是 False，别误报。"""
    import contextlib

    from servers.ansys import project_manage as P

    epp = tmp_path / "Rw.aedt"
    epp.write_text("x", encoding="utf-8")

    desktop = _FakeDesktop([["Rw"], []])
    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desktop))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(P.time, "sleep", lambda _s: None)

    r = P._com_close_project("Rw", False, str(epp))
    assert r["readonly_project_file"] is False
    assert not any("只读" in w for w in r["warnings"]), r["warnings"]


def test_com_close_project_reports_modal_dialog_when_present(monkeypatch):
    """检测到 AEDT 对话框时报文要直接指向「是不是弹了保存确认框」。"""
    import contextlib

    from servers.ansys import project_manage as P

    class _Desktop:
        def GetProjectList(self):
            return ["demo"]

        def GetActiveProject(self):
            return None

        def CloseProject(self, name):
            raise RuntimeError("command rejected while modal dialog is open")

    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), _Desktop()))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(P, "_aedt_modal_dialogs",
                        lambda: ["ANSYS Electronics Desktop", "Save changes?"])

    r = P._com_close_project("demo", False, "")
    assert r["error_code"] == "CLOSE_FAILED"
    assert "检测到 AEDT 上有 2 个对话框" in r["message"], r["message"]
    assert "Save changes?" in r["message"], "要把框标题摆出来，方便用户对号"
    assert r["details"]["modal_dialogs"] == ["ANSYS Electronics Desktop", "Save changes?"]
    assert "处理掉它" in r["message"]


def test_aedt_modal_dialogs_empty_without_aedt(monkeypatch):
    """AEDT 未运行时弹框检测直接返回空列表，不抛异常。"""
    from servers.ansys import project_manage as P

    monkeypatch.setattr(P, "get_aedt_pids", list)
    assert P._aedt_modal_dialogs() == []


def test_com_close_project_returns_none_on_attach_failure(monkeypatch):
    import contextlib
    from servers.ansys import project_manage as pm
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)

    def boom(*a, **k):
        raise RuntimeError("attached none")

    monkeypatch.setattr(pm, "_attach_aedt", boom)
    assert pm._com_close_project("demo", False, "") is None



# ---------------------------------------------------------------------------
# 多实例守卫 / 未响应快速失败 / 自锁清理（2026-11-18）
# ---------------------------------------------------------------------------

def test_aedt_instance_groups_merges_launcher_and_embedding(monkeypatch):
    """一个 AEDT 实例 = 启动器 + `-Embedding` 辅助进程，必须归成一组。

    2026-11-18 真机：`-Embedding` 比启动器晚 **10 分钟**才被 COM 拉起（45920→35932），
    按 create_time 归组会把单实例数成 2 → 所有 ANSYS 工具被 MULTI_INSTANCE_UNSUPPORTED 拦死。
    现在按命令行特征归组（带 `-Embedding` = 辅助）。
    """
    from servers.ansys import project_manage as pm

    class _P:
        def __init__(self, pid, t, cmd):
            self.info = {"name": "ansysedt.exe", "pid": pid, "create_time": t, "cmdline": cmd}

    exe = r"C:\Program Files\AnsysEM\AnsysEM20.2\Win64\ansysedt.exe"
    procs = [
        _P(10972, 1000.0, [exe, r"C:\a\A.aedt"]),        # 实例 1 启动器
        _P(50900, 1600.0, [exe, "-Embedding"]),           # 实例 1 辅助（晚 10 分钟）
        _P(77777, 1700.0, [exe, r"C:\a\B.aedt"]),         # 实例 2 启动器
    ]
    monkeypatch.setattr(pm.psutil, "process_iter", lambda attrs=None: iter(procs))

    groups = pm._aedt_instance_groups()

    assert groups == [[10972], [77777]], groups      # 只有启动器计入实例
    assert len(groups) == 2


def test_aedt_instance_groups_embedding_only_is_one_instance(monkeypatch):
    """机器上只有 `-Embedding`（没有启动器）→ 算 1 个实例，不能数成 N 个。"""
    from servers.ansys import project_manage as pm

    class _P:
        def __init__(self, pid, cmd):
            self.info = {"name": "ansysedt.exe", "pid": pid, "create_time": 1.0, "cmdline": cmd}

    exe = r"C:\...\ansysedt.exe"
    monkeypatch.setattr(pm.psutil, "process_iter",
                        lambda attrs=None: iter([_P(1, [exe, "-Embedding"]),
                                                 _P(2, [exe, "-Embedding"])]))

    groups = pm._aedt_instance_groups()

    assert groups == [[1, 2]] and len(groups) == 1, groups


def test_instance_guard_allows_single_instance_with_two_processes(monkeypatch):
    """单实例（两个进程）不能被拦。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[10972, 50900]])
    assert pm._instance_guard("打开工程") is None


def test_close_hfss_project_refuses_multi_instance(monkeypatch):
    """多个 AEDT 实例时拒绝按工程名关闭：AEDT 2020 不注册 ROT，COM 无法定向。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[111], [222]])
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [111, 222])
    called = []
    monkeypatch.setattr(pm, "_com_close_project",
                        lambda *a, **k: called.append(1) or {"success": True})

    r = pm.close_hfss_project(project_name="demo")

    assert r["error_code"] == "MULTI_INSTANCE_UNSUPPORTED"
    assert r["details"]["instances"] == [[111], [222]]
    assert called == [], "多实例下绝不能猜一个实例去关（会关错工程）"


def test_open_hfss_project_refuses_multi_instance(monkeypatch, tmp_path):
    from servers.ansys import project_manage as pm

    exe = tmp_path / "ansysedt.exe"
    exe.write_text("", encoding="utf-8")
    epp = tmp_path / "X.aedt"
    epp.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pm, "AEDT_PATH", str(exe))
    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[333], [444]])
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [333, 444])
    monkeypatch.setattr(pm, "_clear_self_lock", lambda p: None)
    monkeypatch.setattr(pm, "cleanup_stale_project_lock",
                        lambda p: {"removed": False, "lock_pid": None})

    r = pm.open_hfss_project(project_path=str(epp), timeout_seconds=5)

    assert r["error_code"] == "MULTI_INSTANCE_UNSUPPORTED"
    assert r["details"]["instances"] == [[333], [444]]


def test_open_hfss_project_timeout_is_not_success(monkeypatch, tmp_path):
    """冷启动后没确认工程打开 → success 必须是 False（改前返回 True + launch_requested）。"""
    from servers.ansys import project_manage as pm

    exe = tmp_path / "ansysedt.exe"
    exe.write_text("", encoding="utf-8")
    epp = tmp_path / "Proj.aedt"
    epp.write_text("x", encoding="utf-8")
    monkeypatch.setattr(pm, "AEDT_PATH", str(exe))
    monkeypatch.setattr(pm, "aedt_is_running", lambda: False)
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [])
    monkeypatch.setattr(pm, "_clear_self_lock", lambda p: None)
    monkeypatch.setattr(pm, "cleanup_stale_project_lock",
                        lambda p: {"removed": False, "lock_pid": None})
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "get_project_lock_path", lambda p: tmp_path / "Proj.aedt.lock")
    monkeypatch.setattr(pm.time, "sleep", lambda _s: None)   # 收尾核对 5×5s，别让测试真等 25s

    class _Proc:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(pm.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(pm, "_register_launched", lambda p: None)

    r = pm.open_hfss_project(project_path=str(epp), timeout_seconds=1)

    assert r["success"] is False, "工程没打开就不能报 success"
    assert r["error_code"] == "OPEN_TIMEOUT"
    assert r["details"]["project_opened"] is False
    assert "open_projects" in r["message"]


def test_clear_self_lock_moves_lock_when_project_not_open(monkeypatch, tmp_path):
    """锁里 PID 活着但工程不在 open_projects → 判定自锁残留，改名备份。"""
    from servers.ansys import project_manage as pm

    epp = tmp_path / "P.aedt"
    epp.write_text("x", encoding="utf-8")
    lock = tmp_path / "P.aedt.lock"
    lock.write_text("$begin 'ConnectionParameters'\n\tDesktopProcessID=99999\n"
                    "$end 'ConnectionParameters'\n", encoding="utf-8")
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [99999])
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": []})

    res = pm._clear_self_lock(str(epp))

    assert res and res["reason"] == "self_lock" and res["lock_pid"] == 99999
    assert not lock.exists(), "自锁残留必须被挪走，否则打开永远失败"
    assert (tmp_path / "P.aedt.lock.selflock.bak").exists()


def test_clear_self_lock_keeps_lock_when_project_really_open(monkeypatch, tmp_path):
    """工程确实在 open_projects 里 → 锁是正常的，绝不能动。"""
    from servers.ansys import project_manage as pm

    epp = tmp_path / "P.aedt"
    epp.write_text("x", encoding="utf-8")
    lock = tmp_path / "P.aedt.lock"
    lock.write_text("DesktopProcessID=99999\n", encoding="utf-8")
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [99999])
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["P"]})

    assert pm._clear_self_lock(str(epp)) is None
    assert lock.exists()


def test_get_hfss_project_info_reports_not_responding(monkeypatch):
    """COM 挂住时要快速返回 AEDT_NOT_RESPONDING，而不是把调用拖几分钟。"""
    import time as _time

    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [777])
    monkeypatch.setattr(pm, "_COM_READ_TIMEOUT", 0.3)
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: _time.sleep(2) or {"connected": True, "projects": []})

    t0 = _time.monotonic()
    r = pm.get_hfss_project_info()
    dt = _time.monotonic() - t0

    assert r["success"] is False and r["error_code"] == "AEDT_NOT_RESPONDING"
    assert dt < 1.5, f"应当快速失败，实际等了 {dt:.1f}s"


def test_get_hfss_project_info_flags_multi_instance(monkeypatch):
    """查询要把实例数报出来，方便调用方判断能不能按名字操作。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [1, 2])
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[1], [2]])
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["A"], "active_project": "A",
                                 "active_design": "D"})

    r = pm.get_hfss_project_info()
    assert r["instance_count"] == 2 and r["multi_instance"] is True


def test_com_close_backfills_lock_path_from_com(monkeypatch, tmp_path):
    """lock_path 为空（工程不是本服务打开的）→ 用 COM 的 GetPath() 补出 .aedt 路径。

    2026-11-18 真机：用户在 GUI 手开工程，close 成功但 `lock_path: null`，
    导致只读检测和锁清理全部失效。
    """
    import contextlib

    from servers.ansys import project_manage as P

    epp = tmp_path / "Manual.aedt"
    epp.write_text("x", encoding="utf-8")

    class _Proj:
        def GetPath(self):
            return str(tmp_path)          # AEDT 返回工程**目录**

        def Save(self):
            pass

    class _Desk(_FakeDesktop):
        def SetActiveProject(self, name):
            return _Proj()

    desk = _Desk([["Manual"], []])
    monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desk))
    monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(P.time, "sleep", lambda _s: None)
    monkeypatch.setattr(P, "get_project_lock_path", lambda p: tmp_path / "Manual.aedt.lock")

    r = P._com_close_project("Manual", False, "")      # 关键：lock_path 为空

    assert r["success"] is True
    assert r["lock_path"] and r["lock_path"].endswith("Manual.aedt.lock"), r.get("lock_path")
    assert r["lock_exists_after"] is False
    assert r["readonly_project_file"] is False


def test_com_close_backfilled_readonly_file_warns(monkeypatch, tmp_path):
    """回填出的工程若只读，必须补上「转告用户」的告警（而不是静默关掉）。"""
    import contextlib
    import os

    from servers.ansys import project_manage as P

    epp = tmp_path / "ManualRo.aedt"
    epp.write_text("x", encoding="utf-8")
    os.chmod(epp, 0o444)
    try:
        class _Proj:
            def GetPath(self):
                return str(tmp_path)

            def Save(self):
                raise AssertionError("只读时不该调用 Save")

        class _Desk(_FakeDesktop):
            def SetActiveProject(self, name):
                return _Proj()

        desk = _Desk([["ManualRo"], []])
        monkeypatch.setattr(P, "_attach_aedt", lambda: (object(), desk))
        monkeypatch.setattr(P, "com_session", contextlib.nullcontext)
        monkeypatch.setattr(P.time, "sleep", lambda _s: None)
        monkeypatch.setattr(P, "get_project_lock_path", lambda p: tmp_path / "ManualRo.aedt.lock")

        r = P._com_close_project("ManualRo", True, "")   # 要求保存 + 路径未知

        assert r["readonly_project_file"] is True
        assert any("转告用户" in w for w in r["warnings"]), r["warnings"]
    finally:
        os.chmod(epp, 0o644)


def test_quit_aedt_not_running(monkeypatch):
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [])
    r = pm.quit_aedt()
    assert r["success"] is True and r["status"] == "not_running"


def test_quit_aedt_refuses_multi_instance(monkeypatch):
    """多实例下不能"猜一个"退掉 —— 会把别人在用的实例关掉。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [1, 2])
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[1], [2]])

    r = pm.quit_aedt()
    assert r["error_code"] == "MULTI_INSTANCE_UNSUPPORTED"


def test_quit_aedt_com_quit_success(monkeypatch):
    import contextlib

    from servers.ansys import project_manage as pm

    state = {"pids": [111]}

    class _Desk:
        def __init__(self):
            self.quit_called = 0

        def QuitApplication(self):
            self.quit_called += 1

    desk = _Desk()
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: list(state["pids"]))
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[111]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: True)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", lambda: (object(), desk))
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: (state.update(pids=[]), True)[1])

    r = pm.quit_aedt()

    assert desk.quit_called == 1, "应走 COM QuitApplication"
    assert r["success"] is True and r["status"] == "quit" and r["method"] == "com"
    assert r["pids_after"] == []


def test_quit_aedt_warns_about_open_projects(monkeypatch):
    import contextlib

    from servers.ansys import project_manage as pm

    state = {"pids": [111]}
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: list(state["pids"]))
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["A", "B"]})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[111]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: True)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", lambda: (object(), type("D", (), {
        "QuitApplication": lambda self: None})()))
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: (state.update(pids=[]), True)[1])

    r = pm.quit_aedt()
    assert r["data_loss_risk"] is True
    assert any("转告用户" in w for w in r["warnings"]), r["warnings"]


def test_quit_aedt_force_kills_all(monkeypatch):
    from servers.ansys import project_manage as pm

    calls = {}
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [111, 222])
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": ["A"]})

    def fake_kill(pids):
        calls.update(pids=pids)
        return {"success": True, "method": "taskkill", "scope": "all"}

    monkeypatch.setattr(pm, "_kill_aedt_processes", fake_kill)

    r = pm.quit_aedt(force=True, confirm=True)

    assert calls == {"pids": [111, 222]}
    assert r["status"] == "killed" and r["data_loss_risk"] is True


def test_quit_aedt_force_requires_confirm(monkeypatch):
    """force 是不可逆整机硬杀 → 没有 confirm 必须拒绝，且一个进程都不许动。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [111, 222])
    monkeypatch.setattr(pm, "_kill_aedt_processes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该杀进程")))

    r = pm.quit_aedt(force=True)

    assert r["error_code"] == "CONFIRM_REQUIRED"
    assert "quit_aedt(force=True, confirm=True)" in r["hint"]
    assert r["details"]["scope"] == "all"


def test_quit_aedt_still_running_returns_error(monkeypatch):
    """COM 退出失败 + WM_CLOSE 也无效 → STILL_RUNNING，并给出人工处置提示（不自己硬杀）。"""
    import contextlib

    from servers.ansys import project_manage as pm

    def _boom():
        raise RuntimeError("COM 挂了")

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [333])
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[333]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: True)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", _boom)
    monkeypatch.setattr(pm, "_post_wm_close_to_aedt", lambda pids: 1)
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: False)

    r = pm.quit_aedt()

    assert r["error_code"] == "STILL_RUNNING"
    assert "force=true" in r["hint"]
    assert r["details"]["pids_after"] == [333]


def test_quit_aedt_unmanaged_instance_requires_confirm(monkeypatch):
    """用户手开 / 别的工具开的 AEDT：未经 confirm 不许退（否则替别人关掉他在用的实例）。"""
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [555])
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["UserProject"]})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[555]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: False)
    monkeypatch.setattr(pm, "_kill_aedt_processes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该动进程")))

    r = pm.quit_aedt()

    assert r["error_code"] == "CONFIRM_REQUIRED"
    assert r["details"]["managed"] is False
    assert r["details"]["aedt_pids"] == [555]
    assert r["details"]["open_projects"] == ["UserProject"]
    assert "confirm=True" in r["hint"]


def test_quit_aedt_unmanaged_instance_with_confirm_proceeds(monkeypatch):
    """确认后可以退：COM QuitApplication 正常走完。"""
    import contextlib

    from servers.ansys import project_manage as pm

    state = {"pids": [555]}
    desk = type("D", (), {"n": 0, "QuitApplication": lambda self: setattr(self, "n", 1)})()
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: list(state["pids"]))
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[555]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: False)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", lambda: (object(), desk))
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: (state.update(pids=[]), True)[1])

    r = pm.quit_aedt(confirm=True)

    assert r["success"] is True and r["method"] == "com" and desk.n == 1


def test_open_hfss_project_opened_after_timeout_is_success(monkeypatch, tmp_path):
    """等待窗口过期后 AEDT 才把工程挂上 → 必须报 success + opened_after_timeout。

    2026-11-18 真机：实际 129.5s 才打开，窗口 120s → 旧逻辑误报 OPEN_TIMEOUT（假失败）。
    """
    from servers.ansys import project_manage as pm

    exe = tmp_path / "ansysedt.exe"
    exe.write_text("", encoding="utf-8")
    epp = tmp_path / "Late.aedt"
    epp.write_text("x", encoding="utf-8")

    calls = {"n": 0}

    def fake_state():
        calls["n"] += 1
        # 收尾核对的第 2 次才挂上（第 1 次仍看不到）→ 走 opened_after_timeout 分支
        return {"connected": True, "projects": ["Late"] if calls["n"] >= 2 else []}

    # 让轮询阶段直接过期（monotonic 每次跳 10s），逼出收尾核对分支
    ticks = {"t": 0.0}

    def fake_mono():
        ticks["t"] += 10.0
        return ticks["t"]

    class _Proc:
        pid = 4242
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(pm, "AEDT_PATH", str(exe))
    monkeypatch.setattr(pm, "aedt_is_running", lambda: False)
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [])
    monkeypatch.setattr(pm, "cleanup_stale_project_lock", lambda p: {"removed": False, "lock_pid": None})
    monkeypatch.setattr(pm, "_clear_self_lock", lambda p: None)
    monkeypatch.setattr(pm.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(pm, "_register_launched", lambda p: None)
    monkeypatch.setattr(pm, "query_desktop_state", fake_state)
    monkeypatch.setattr(pm.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pm.time, "monotonic", fake_mono)

    r = pm.open_hfss_project(project_path=str(epp), timeout_seconds=1)

    assert r["success"] is True
    assert r["status"] == "opened_after_timeout"
    assert r["project_opened"] is True and r["verified"] is True
    assert any("timeout_seconds" in w for w in r["warnings"])
    assert pm._LAST_PID == 4242


def test_open_hfss_project_already_open_reports_success(monkeypatch, tmp_path):
    """工程已开着（锁被活进程持有 + open_projects 里有它）→ success + already_open。

    2026-11-18 真机：旧逻辑先判锁 → 回 PROJECT_LOCKED（0.1s），agent 会误以为打开失败。
    """
    from servers.ansys import project_manage as pm

    exe = tmp_path / "ansysedt.exe"
    exe.write_text("", encoding="utf-8")
    epp = tmp_path / "Opened.aedt"
    epp.write_text("x", encoding="utf-8")

    monkeypatch.setattr(pm, "AEDT_PATH", str(exe))
    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [9001])
    monkeypatch.setattr(pm, "_clear_self_lock", lambda p: None)
    monkeypatch.setattr(pm, "cleanup_stale_project_lock",
                        lambda p: {"removed": False, "lock_pid": 9001, "status": "lock_active"})
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["Opened"]})
    monkeypatch.setattr(pm.subprocess, "Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该再起进程")))

    r = pm.open_hfss_project(project_path=str(epp), timeout_seconds=5)

    assert r["success"] is True
    assert r["status"] == "already_open"
    assert r["project_opened"] is True and r["verified"] is True
    assert r["lock_pid"] == 9001
    assert pm._OPEN_PROJECT_PATHS.get("Opened") == str(epp)


def test_open_hfss_project_lock_held_elsewhere_stays_error(monkeypatch, tmp_path):
    """锁被持有、但 open_projects 里没有这个工程 → 仍回 PROJECT_LOCKED（不误报成功）。"""
    from servers.ansys import project_manage as pm

    exe = tmp_path / "ansysedt.exe"
    exe.write_text("", encoding="utf-8")
    epp = tmp_path / "Elsewhere.aedt"
    epp.write_text("x", encoding="utf-8")

    monkeypatch.setattr(pm, "AEDT_PATH", str(exe))
    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_clear_self_lock", lambda p: None)
    monkeypatch.setattr(pm, "cleanup_stale_project_lock",
                        lambda p: {"removed": False, "lock_pid": 9002, "status": "lock_active"})
    monkeypatch.setattr(pm, "query_desktop_state",
                        lambda: {"connected": True, "projects": ["Other"]})

    r = pm.open_hfss_project(project_path=str(epp), timeout_seconds=5)

    assert r["error_code"] == "PROJECT_LOCKED"
    assert r["details"]["lock_pid"] == 9002


def test_is_access_denied_markers():
    from servers.ansys import project_manage as pm

    assert pm._is_access_denied("错误: 无法终止 PID 为 35932 的进程。原因: 拒绝访问。")
    assert pm._is_access_denied("Access is denied.")
    assert not pm._is_access_denied("进程不存在")
    assert not pm._is_access_denied("")


def test_kill_aedt_processes_retries_when_helper_respawns(monkeypatch):
    """杀掉 helper 后可能冒出**新 PID**（35932→40936）→ 必须多轮重试。"""
    from servers.ansys import project_manage as pm

    alive = {1, 2}
    calls = []

    def fake_taskkill(pid):
        calls.append(pid)
        if pid in alive:
            alive.discard(pid)
            return True, ""
        return False, "进程不存在"

    # 第二轮：1 被杀后又冒出 3
    def fake_pids():
        return sorted(alive | ({3} if 1 not in alive and 2 not in alive else set()))

    def fake_taskkill2(pid):
        calls.append(pid)
        alive.discard(pid)
        if pid == 3:
            return True, ""
        return True, ""

    monkeypatch.setattr(pm, "get_aedt_pids", fake_pids)
    monkeypatch.setattr(pm, "_taskkill", fake_taskkill2)
    monkeypatch.setattr(pm.time, "sleep", lambda _s: None)

    r = pm._kill_aedt_processes([1, 2])

    assert r["success"] is True, r
    assert 3 in calls, f"新生 helper 没被杀：{calls}"
    assert alive == set()


def test_kill_aedt_processes_reports_permission_denied(monkeypatch):
    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [35932])
    monkeypatch.setattr(pm, "_taskkill",
                        lambda pid: (False, "错误: 无法终止 PID 为 35932 的进程。原因: 拒绝访问。"))
    monkeypatch.setattr(pm.time, "sleep", lambda _s: None)

    r = pm._kill_aedt_processes([35932], rounds=1)

    assert r["error_code"] == "PERMISSION_DENIED"
    assert r["details"]["pids_left"] == [35932]
    assert "管理员" in r["hint"]


def test_quit_aedt_cleans_leftover_embedding_helpers(monkeypatch):
    """优雅退出带走启动器后，残留的 `-Embedding` 必须自动清理并报成功。

    2026-11-18 真机：45920 退出、35932(-Embedding) 留着，WM_CLOSE 无效 → 旧逻辑报 STILL_RUNNING。
    """
    import contextlib

    from servers.ansys import project_manage as pm

    state = {"pids": [45920, 35932]}
    killed = []

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: list(state["pids"]))
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[45920]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: True)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", lambda: (object(), type("D", (), {
        "QuitApplication": lambda self: None})()))
    # COM 退出后启动器消失，只剩 helper
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: (state.update(pids=[35932]), False)[1])
    monkeypatch.setattr(pm, "_post_wm_close_to_aedt", lambda pids: 0)
    monkeypatch.setattr(pm, "_embedding_pids", lambda pids: [p for p in pids if p == 35932])

    def fake_kill(pids, rounds=3):
        killed.extend(pids)
        state.update(pids=[p for p in state["pids"] if p not in pids])
        return {"success": True, "method": "taskkill", "scope": "all"}

    monkeypatch.setattr(pm, "_kill_aedt_processes", fake_kill)

    r = pm.quit_aedt(confirm=True)

    assert r["success"] is True and r["status"] == "quit", r
    assert killed == [35932], killed
    assert "helper_cleanup" in r["method"]
    assert r["pids_after"] == []


def test_quit_aedt_does_not_kill_when_launcher_alive(monkeypatch):
    """启动器还活着时（可能弹着保存框）不许自动 helper 清理，要如实报 STILL_RUNNING。"""
    import contextlib

    from servers.ansys import project_manage as pm

    monkeypatch.setattr(pm, "get_aedt_pids", lambda: [45920, 35932])
    monkeypatch.setattr(pm, "query_desktop_state", lambda: {"connected": True, "projects": []})
    monkeypatch.setattr(pm, "_aedt_instance_groups", lambda: [[45920]])
    monkeypatch.setattr(pm, "_is_managed_aedt", lambda pids: True)
    monkeypatch.setattr(pm, "com_session", contextlib.nullcontext)
    monkeypatch.setattr(pm, "_attach_aedt", lambda: (object(), type("D", (), {
        "QuitApplication": lambda self: None})()))
    monkeypatch.setattr(pm, "_wait_no_aedt", lambda _t: False)
    monkeypatch.setattr(pm, "_post_wm_close_to_aedt", lambda pids: 1)
    monkeypatch.setattr(pm, "_embedding_pids", lambda pids: [35932])          # 只有一个是 helper
    monkeypatch.setattr(pm, "_kill_aedt_processes",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("启动器还活着，不该杀")))

    r = pm.quit_aedt(confirm=True)

    assert r["error_code"] == "STILL_RUNNING"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])