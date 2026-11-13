"""LASPCEM 并发/生命周期修复的单测（2026-11-13 审查对应改动）。

覆盖：
- `project_registry` 的占用/冲突/重入/豁免语义；
- 后台求解任务登记期间，同步 export / report 被 PROJECT_IN_USE 拦住；
- `_entry_with_context` 进线程登记、出线程释放；
- `_kill_solver_procs(pids=...)` 按 pid 精确杀（超时路径用）；
- `_sem_of_process` 从 cmdline / /session 脚本里认工程；
- `laspcem_cleanup_orphans` 的 dry_run 不误杀。
"""
import threading
import time
from unittest import mock

import pytest

from servers.laspcem import async_solve, launcher, project_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空全局登记表，避免用例互相污染。"""
    project_registry.clear_owner("owner1")
    project_registry.clear_owner("task:t1")
    yield
    for item in project_registry.snapshot():
        project_registry.clear_owner(item["owner"])
    project_registry.bind_thread("")


def test_registry_acquire_conflict_and_exemption():
    sem = r"D:\tmp\A.sem"
    ok, _ = project_registry.acquire(sem, "owner1", "solve")
    assert ok
    assert project_registry.holder(sem)["owner"] == "owner1"

    ok2, held = project_registry.acquire(sem, "owner2")
    assert ok2 is False and held["owner"] == "owner1"

    # 本线程没绑定 owner1 ⇒ 视为冲突
    assert project_registry.conflict(sem) is not None
    # 绑定同一 owner ⇒ 后处理链路自己不该被自己拦住
    project_registry.bind_thread("owner1")
    assert project_registry.conflict(sem) is None
    project_registry.bind_thread("")

    project_registry.release(sem, "owner1")
    assert project_registry.holder(sem) is None


def test_registry_reentrant_count():
    sem = r"D:\tmp\B.sem"
    assert project_registry.acquire(sem, "owner1")[0]
    assert project_registry.acquire(sem, "owner1")[0]
    project_registry.release(sem, "owner1")
    assert project_registry.holder(sem)["count"] == 1     # 还欠一次
    project_registry.release(sem, "owner1")
    assert project_registry.holder(sem) is None


def test_entry_with_context_registers_and_releases():
    sem = r"D:\tmp\C.sem"
    seen = {}

    def body():
        seen.update(project_registry.holder(sem) or {})
        return "ok"

    entry = async_solve._entry_with_context(body, threading.Event(), {}, "task:t1", sem)
    assert entry() == "ok"
    assert seen.get("owner") == "task:t1"
    assert seen.get("kind") == "solve"
    assert project_registry.holder(sem) is None          # 出线程必须释放


def test_export_blocked_while_background_solve_holds_project(tmp_path):
    sem = tmp_path / "demo.sem"
    sem.write_bytes(b"not-a-real-sem")
    project_registry.acquire(str(sem), "task:t1", "solve")
    try:
        with mock.patch.object(launcher, "_resolve_exe", return_value=("C:/fake/laspcem.exe", None)), \
             mock.patch.object(launcher, "_project_in_use", return_value=[]):
            r = launcher.export_laspcem_snp(str(sem))
    finally:
        project_registry.clear_owner("task:t1")
    assert r["success"] is False, r
    assert r["error_code"] == "PROJECT_IN_USE", r
    assert r["details"]["holder"]["owner"] == "task:t1", r


def test_report_blocked_while_background_solve_holds_project(tmp_path):
    sem = tmp_path / "demo2.sem"
    sem.write_bytes(b"not-a-real-sem")
    project_registry.acquire(str(sem), "task:t1", "solve")
    try:
        with mock.patch.object(launcher, "_resolve_exe", return_value=("C:/fake/laspcem.exe", None)), \
             mock.patch.object(launcher, "_project_in_use", return_value=[]):
            r = launcher.build_laspcem_report(str(sem))
    finally:
        project_registry.clear_owner("task:t1")
    assert r["success"] is False, r
    assert r["error_code"] == "PROJECT_IN_USE", r


def test_registry_busy_passes_sem_path_to_running_task_lookup(tmp_path, monkeypatch):
    """holder 必须带 sem_path，否则 _registry_busy_response 查不到 running_solve_task。

    2026-11-13 线上实证发现的疏漏：holder 里只有 owner/kind/count/since，
    传空串去比路径必然拿不到 task_id（返回 null）。
    """
    sem = str(tmp_path / "t.sem")
    project_registry.acquire(sem, "task:x", "solve")
    try:
        held = project_registry.holder(sem)
        assert held["sem_path"] == sem
        captured = {}

        def _fake_lookup(path):
            captured["path"] = path
            return "tid123"

        monkeypatch.setattr(launcher, "_running_solve_task_for", _fake_lookup)
        r = launcher._registry_busy_response(held, "导出")
        assert captured["path"] == sem, captured
        assert r["error_code"] == "PROJECT_IN_USE"
        assert r["details"]["running_solve_task"] == "tid123", r
    finally:
        project_registry.clear_owner("task:x")


def test_wait_for_solve_returns_immediately_when_gui_exited(monkeypatch):
    """GUI 退出且从未见到求解器 ⇒ 立刻返回 gui_exited，不能等满宽限期。

    2026-11-13 实测：BPF.sem 在 `app.Open` 就崩退（exit 1），旧 grace=timeout+1
    会让调用方白等 30 分钟才拿到 SOLVE_NOT_TRIGGERED。
    """
    import time as _t

    class _DeadProc:
        returncode = 1

        def poll(self):
            return 1

    monkeypatch.setattr(launcher, "_solver_processes", list)
    monkeypatch.setattr(launcher, "_progress_sig", lambda p: (0, (0, 0.0)))
    monkeypatch.setattr(launcher, "_file_sig", lambda p: (0, 0.0))
    t0 = _t.time()
    r = launcher._wait_for_solve("x.sem", 1800, no_solver_grace=600.0, proc=_DeadProc())
    assert r["reason"] == "gui_exited", r
    assert r["gui_exit_code"] == 1, r
    assert r["saw_solver"] is False
    assert _t.time() - t0 < 5.0, "should return immediately, not after grace period"


def test_solve_timeout_kills_solvers_and_mpi_before_persist(tmp_path, monkeypatch):
    """超时必须「先杀求解器（含 MPI 代理）→ 再补保存」，并把 killed 列表透出。

    2026-11-13 实测：旧顺序是先 persist（起新 GUI SaveAs）再杀，等于在求解器写入时
    读写同一个 .sem；且 hydra_pmi_proxy 不在 solver_processes 里，会变成新孤儿。
    """
    import servers.laspcem.launcher as L

    sem = tmp_path / "t.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    order = []

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda *a, **k: {
        "saw_solver": True, "timed_out": True, "solver_seconds": 1800.0,
        "solver_processes": [{"pid": 7, "name": "phofem.exe"}], "sem_changed": False})
    monkeypatch.setattr(L, "_kill_solver_procs",
                        lambda p, pids=None: order.append(("kill", tuple(pids or ()))) or [7])
    monkeypatch.setattr(L, "_kill_orphan_mpi_since",
                        lambda since: order.append(("mpi", None)) or [8])
    monkeypatch.setattr(L, "_persist_solution",
                        lambda p, e: order.append(("persist", None)) or {"saved": True})
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (None, ""))
    monkeypatch.setattr(L, "_solver_processes", list)
    monkeypatch.setattr(L, "detect_solution_names", lambda p: ["FEM_1"])
    monkeypatch.setattr(L, "is_solved", lambda p: False)

    r = L.run_laspcem_solve(str(sem))
    assert r["error_code"] == "SOLVE_TIMEOUT", r
    assert [o[0] for o in order] == ["kill", "mpi", "persist"], order
    assert order[0][1] == (7,), order
    assert r["details"]["killed_solver_pids"] == [7, 8], r["details"]


def test_cleanup_orphans_skips_project_with_running_task(tmp_path, monkeypatch):
    """P1-2 回归：显式指定**正在求解**的工程时，cleanup 不能杀掉它的求解器。

    原实现里守卫写成 `owner_key in active_sems and owner_key != want`，
    而 want 分支下 owner_key 恒等于 want ⇒ 条件恒 False ⇒ 正好在最该保护的场景失效。
    """
    sem = str(tmp_path / "busy.sem")
    (tmp_path / "busy.sem").write_bytes(b"x")
    script = tmp_path / "s.py"
    script.write_text(f'app.Open("{sem.replace(chr(92), "/")}")', encoding="utf-8")

    gate = threading.Event()
    tid = async_solve._runner.submit(lambda: gate.wait(20), metadata={"sem_path": sem})
    time.sleep(0.2)

    class _Fake:
        def __init__(self):
            self.info = {"pid": 101, "name": "laspcem.exe",
                         "cmdline": ["C:/L/bin/laspcem.exe", "/session", str(script)]}

    monkeypatch.setattr(async_solve.psutil, "process_iter", lambda attrs=None: [_Fake()])

    def _no_terminate(pid):
        raise AssertionError(f"不该 terminate 正在跑的求解器 pid={pid}")

    monkeypatch.setattr(async_solve.psutil, "Process", _no_terminate)
    try:
        r = async_solve.laspcem_cleanup_orphans(sem_path=sem, dry_run=False)
    finally:
        gate.set()
        async_solve._runner.cancel(tid)
    assert r["killed"] == [], r
    assert any("正在跑" in s.get("reason", "") for s in r["skipped"]), r


def test_cancel_clears_all_three_tables():
    """P2-3 回归：cancel 后 _phases / _cancel_events / _handles 三张表都要清。"""
    gate = threading.Event()
    tid = async_solve._runner.submit(lambda: gate.wait(20), metadata={"sem_path": ""})
    async_solve._phases[tid] = {"phase": "solving"}
    async_solve._cancel_events[tid] = gate
    async_solve._handles[tid] = {}
    try:
        r = async_solve.laspcem_task_cancel(tid)
        assert r["success"] is True, r
        assert tid not in async_solve._phases
        assert tid not in async_solve._cancel_events
        assert tid not in async_solve._handles
    finally:
        gate.set()


def test_report_not_verified_returns_error(tmp_path, monkeypatch):
    """P2-4 回归：GUI 提前退出、工程未被改写时不能报 success。"""
    sem = tmp_path / "r.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")

    class _P:
        returncode = 1

        def poll(self):
            return 1

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(launcher, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(launcher, "_project_in_use", lambda p: [])
    monkeypatch.setattr(launcher, "_spawn_script", lambda e, s: (_P(), "s.py", None))
    monkeypatch.setattr(launcher, "_file_sig", lambda p: (0, 0.0))
    r = launcher.build_laspcem_report(str(sem), timeout_seconds=5)
    assert r["success"] is False, r
    assert r["error_code"] == "REPORT_NOT_VERIFIED", r
    assert r["details"]["verified"] is False, r     # error_response 的 extra 进 details


def test_cleanup_orphans_handles_solvers_without_project_path(tmp_path, monkeypatch):
    """求解器类进程 cmdline 无工程路径时（实测 phofem 就是这样）的三条分支。

    - 无在跑任务 ⇒ 当孤儿清掉（服务重启后最常见的残留，旧代码会静默漏掉）
    - 有在跑任务 ⇒ 跳过 **并记录** 到 skipped（不能静默）
    - 显式指定工程 ⇒ 同样跳过并记录（无法确认归属）
    """
    sem = str(tmp_path / "p.sem")
    (tmp_path / "p.sem").write_bytes(b"x")

    class _P:
        def __init__(self):
            self.info = {"pid": 202, "name": "phofem.exe",
                         "cmdline": ["solver/fem/phofem", "Design", "ADAPTIVE-MESH"]}

    terminated = []

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            terminated.append(self.pid)

    monkeypatch.setattr(async_solve.psutil, "process_iter", lambda attrs=None: [_P()])
    monkeypatch.setattr(async_solve.psutil, "Process", _Proc)

    # 1) 无在跑任务、不指定工程 ⇒ 清掉
    r = async_solve.laspcem_cleanup_orphans(dry_run=False)
    assert [k["pid"] for k in r["killed"]] == [202], r
    assert terminated == [202], terminated

    # 2) 指定工程 ⇒ 跳过并记录（无法确认归属）
    terminated.clear()
    r = async_solve.laspcem_cleanup_orphans(sem_path=sem, dry_run=False)
    assert r["killed"] == [], r
    assert any(s["pid"] == 202 for s in r["skipped"]), r
    assert terminated == [], terminated

    # 3) 有在跑任务 ⇒ 跳过并记录
    gate = threading.Event()
    tid = async_solve._runner.submit(lambda: gate.wait(20), metadata={"sem_path": sem})
    time.sleep(0.2)
    try:
        r = async_solve.laspcem_cleanup_orphans(dry_run=False)
    finally:
        gate.set()
        async_solve._runner.cancel(tid)
    assert r["killed"] == [], r
    assert any("有在跑的任务" in s["reason"] for s in r["skipped"]), r


def test_is_solved_accepts_either_signal(tmp_path):
    """两个判据取或：`SolutionId` 字节 或 结果目录里跑完的 Design_*.log。

    2026-11-13 实测：CPWG-3 求解完（.sem 342 KB → 7.6 MB）文件里没有 "SolutionId"，
    但 `1_Result/Design_FEM.log` 末尾是 `Job is successfully finished !`；
    旧实现只看字节 ⇒ 误报 SOLVE_NOT_SAVED。
    """
    import servers.laspcem.setup_reader as S

    p = tmp_path / "blank.sem"
    p.write_bytes(b"nothing about solutions here")
    assert S.is_solved(str(p)) is False          # 两判据皆空

    # 旧格式：顶层字节有 SolutionId
    p.write_bytes(b"xx SolutionId yy")
    assert S.is_solved(str(p)) is True

    # 新格式：顶层没有 SolutionId，但结果目录里有跑完的求解日志
    p.write_bytes(b"nothing about solutions here")
    res = tmp_path / "blank" / "1_Result"
    res.mkdir(parents=True)
    (res / "Design_FEM.log").write_bytes(b"mesh...\n Job is successfully finished !\n")
    assert S.is_solved(str(p)) is True

    # 解名残留**不算**（detect_solution_names 会误报，不能作判据）
    p2 = tmp_path / "stale.sem"
    p2.write_bytes(b"FEM_1 FEM_2 Setup1(FEM)")
    assert S.detect_solution_names(str(p2)) == ["FEM_1", "FEM_2"]
    assert S.is_solved(str(p2)) is False


def test_cleanup_orphans_records_unresolvable_gui(monkeypatch):
    """cmdline 读不出工程路径的 GUI（`/session` 的临时脚本已被删）⇒ 不动，但必须记录。

    2026-11-13 复核：这类进程原来走 `if not owner_key: continue` 静默吞掉，
    返回体里看不出"有个实例我没敢动"。
    """
    class _P:
        def __init__(self):
            self.info = {"pid": 303, "name": "laspcem.exe",
                         "cmdline": ["laspcem.exe", "/session", "C:/temp/already-gone.py"]}

    monkeypatch.setattr(async_solve.psutil, "process_iter", lambda attrs=None: [_P()])
    monkeypatch.setattr(async_solve.psutil, "Process",
                        lambda pid: (_ for _ in ()).throw(AssertionError(f"不该 terminate {pid}")))
    r = async_solve.laspcem_cleanup_orphans(dry_run=False)
    assert r["killed"] == [], r
    assert any(s["pid"] == 303 and "无工程路径" in s["reason"] for s in r["skipped"]), r


def test_kill_solver_procs_by_pid(monkeypatch):
    killed_procs = []

    class _FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            killed_procs.append(self.pid)

    monkeypatch.setattr(launcher.psutil, "Process", _FakeProc)
    killed = launcher._kill_solver_procs("x.sem", pids=[11, 22])
    assert killed == [11, 22]
    assert killed_procs == [11, 22]


def test_sem_of_process_reads_cmdline_and_session_script(tmp_path):
    assert async_solve._sem_of_process(
        '"C:/LASPCEM/bin/laspcem.exe" "D:/proj/A.sem"').replace("\\", "/") == "D:/proj/A.sem"

    script = tmp_path / "s.py"
    script.write_text('app.OpenProject(r"D:/proj/B.sem")\n', encoding="utf-8")
    cmd = f'"C:/LASPCEM/bin/laspcem.exe" /session "{script}"'
    assert async_solve._sem_of_process(cmd).replace("\\", "/") == "D:/proj/B.sem"
    assert async_solve._sem_of_process("mpiexec -n 4 --usize -1") == ""


def test_cleanup_orphans_dry_run_does_not_touch_unrelated_project():
    r = async_solve.laspcem_cleanup_orphans(sem_path=r"D:\no\such\zzz.sem", dry_run=True)
    assert r["success"] is True, r
    assert r["dry_run"] is True
    assert r["killed"] == [], r


def test_task_list_reaps_stale_entries():
    """本地三张表里残留的 task_id（TaskRunner 已淘汰）应被 task_list 顺带清掉。"""
    async_solve._phases["ghost123"] = {"phase": "done"}
    async_solve._cancel_events["ghost123"] = threading.Event()
    async_solve._handles["ghost123"] = {}
    try:
        r = async_solve.laspcem_task_list()
        assert r["reaped_stale"] >= 1
        assert "ghost123" not in async_solve._phases
    finally:
        async_solve._phases.pop("ghost123", None)
        async_solve._cancel_events.pop("ghost123", None)
        async_solve._handles.pop("ghost123", None)
