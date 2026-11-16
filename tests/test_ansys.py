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


def test_close_hfss_project_com_success_no_kill(monkeypatch):
    from servers.ansys import project_manage as pm
    monkeypatch.setattr(pm, "_resolve_close_targets", lambda: [12345])
    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_com_close_project",
                        lambda *a, **k: {"success": True, "method": "com"})
    killed = []
    monkeypatch.setattr(pm, "_kill_aedt_processes", lambda *a, **k: killed.append(1) or {"method": "kill"})
    r = pm.close_hfss_project(project_name="demo")
    assert r["method"] == "com"
    assert killed == []


def test_close_hfss_project_com_failure_falls_back_to_kill(monkeypatch):
    from servers.ansys import project_manage as pm
    monkeypatch.setattr(pm, "_resolve_close_targets", lambda: [12345])
    monkeypatch.setattr(pm, "aedt_is_running", lambda: True)
    monkeypatch.setattr(pm, "_com_close_project", lambda *a, **k: None)  # COM 附着失败
    calls = {}

    def fake_kill(pids, lock_path, project_name, hard):
        calls["pids"], calls["hard"] = pids, hard
        return {"success": True, "method": "taskkill"}

    monkeypatch.setattr(pm, "_kill_aedt_processes", fake_kill)
    r = pm.close_hfss_project(project_name="demo")
    assert r["method"] == "taskkill"
    assert calls["pids"] == [12345]
    assert calls["hard"] is False  # 非 force：先 taskkill（WM_CLOSE），失败才 /F


def test_close_hfss_project_force_direct_hard_kill(monkeypatch):
    from servers.ansys import project_manage as pm
    monkeypatch.setattr(pm, "_resolve_close_targets", lambda: [12345])
    calls = {}

    def fake_kill(pids, lock_path, project_name, hard):
        calls["hard"] = hard
        return {"success": True, "method": "taskkill"}

    monkeypatch.setattr(pm, "_kill_aedt_processes", fake_kill)
    pm.close_hfss_project(force=True)
    assert calls["hard"] is True  # force：直接 /F 硬杀


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
    assert r["details"]["modal_dialogs"] == [], "无 AEDT 时弹框检测应为空"
    assert "未检测到 AEDT 对话框" in r["message"], "没弹框时也要说清'不是弹框问题'"


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


def test_resolve_close_targets_groups_by_create_time(monkeypatch):
    """AEDT 会分裂成**互不为父子**的多个 ansysedt.exe；关闭目标要按创建时间收同一批。

    2026-11-16 实测：pid 31348（启动器，Popen 起的）与 32576（`-Embedding` GUI）
    create_time 只差 0.01 s，但 ppid 分别是 18080 / 1632 —— `children()` 抓不到，
    只认 `_LAST_PID` 会留下孤儿（实测杀 31348 后 32576 残留，要手工清）。
    """
    from servers.ansys import project_manage as P

    t0 = 1794810910.95

    class _Proc:
        def __init__(self, pid, ct):
            self.pid = pid
            self._ct = ct

        def is_running(self):
            return True

        def name(self):
            return "ansysedt.exe"

        def create_time(self):
            return self._ct

    class _Info:
        def __init__(self, pid, name, ct):
            self.info = {"pid": pid, "name": name, "create_time": ct}

    launcher = _Proc(31348, t0)
    embedding = _Proc(32576, t0 + 0.01)     # 同一批（分裂出的 -Embedding GUI）
    older = _Proc(20000, t0 - 3600)         # 一小时前手动开的，不该碰

    table = {31348: launcher, 32576: embedding, 20000: older}
    iterable = [
        _Info(31348, "ansysedt.exe", t0),
        _Info(32576, "ansysedt.exe", t0 + 0.01),
        _Info(20000, "ansysedt.exe", t0 - 3600),
        _Info(999, "explorer.exe", t0),
    ]
    monkeypatch.setattr(P, "_LAST_PID", 31348)
    monkeypatch.setattr(P.psutil, "Process", lambda pid: table[pid])
    monkeypatch.setattr(P.psutil, "process_iter", lambda attrs=None: iterable)
    monkeypatch.setattr(P, "get_aedt_pids", lambda: [31348, 32576, 20000])

    targets = P._resolve_close_targets()
    assert set(targets) == {31348, 32576}, f"同批两个都要收，实际 {targets}"
    assert 20000 not in targets, "不该误伤更早启动的实例"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
