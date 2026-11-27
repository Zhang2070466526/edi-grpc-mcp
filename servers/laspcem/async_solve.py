"""LASPCEM 异步任务层：只有「求解」是慢操作，后处理（导出/建报告）挂在同一个任务上。

设计（2026-11-13 定稿）：
- **求解**是最耗时的能力（150 s – 数十分钟），单独做成异步任务；
- 调用方**不需要自己串流程**：提交时用 `then_export=True` / `then_report=True` 声明后处理，
  服务端在求解完成后自动接着做，一个 `task_id` 查到底；
- `export_laspcem_snp` / `build_laspcem_report` 保持**同步快工具**（5–6 s），
  它们只在工程已求解时可用，未求解直接返回 `SOLVE_REQUIRED`（不阻塞、不起 GUI）。

状态机（TaskRunner 维护）：QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED，
RUNNING 期间 `phase` 依次是 solving → exporting → reporting。
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from servers import mcp
from servers.laspcem import project_registry
from servers.laspcem.launcher import (
    _check_explicit_setup,
    _kill_orphan_mpi_since,
    build_laspcem_report,
    clear_task_context,
    export_laspcem_snp,
    run_laspcem_solve,
    set_task_context,
    solve_progress,
)
from servers.laspcem.setup_reader import is_solved
from servers.task_runner import TaskRunner
from servers.utils import (
    MAX_QUERY_WAIT_SECONDS,
    bounded_wait,
    error_response,
    queue_full_response,
    submitted_response,
    validate_file,
)

#: 单 worker：LASPCEM 一次只跑一个作业，队列串行化避免多实例抢工程/CPU
_runner = TaskRunner(name_prefix="laspcem", max_workers=1, ttl_seconds=6 * 3600, max_tasks=20)

#: task_id → 取消信号（传给 run_laspcem_solve，让等待循环立刻收手）
_cancel_events: dict[str, threading.Event] = {}
#: task_id → 进程句柄（proc/script_path，取消时用来精确定位 GUI）
_handles: dict[str, dict] = {}
#: task_id → 链式进度（phase / steps），查询时回传
_phases: dict[str, dict] = {}
#: sem_path → (时间戳, solve_progress 结果)：轮询时避免重复扫盘/枚举进程
_progress_cache: dict[tuple[str, float], tuple[float, dict]] = {}
_events_lock = threading.Lock()


def _terminate_pid(pid: int) -> None:
    """尽力终止进程；已退出或权限不足都静默跳过。"""
    try:
        psutil.Process(pid).terminate()
    except (psutil.Error, OSError):
        pass


def _related_pids(script_path: str, sem_path: str) -> list[int]:
    """找出该任务相关的进程 pid：LASPCEM GUI（按 /session 脚本路径或工程名）+ mpiexec/phofem。

    实测（2026-11-12）：`_spawn_script` 起的是 `laspcem.exe /session <临时脚本>`，cmdline 里
    **没有 .sem 路径**，所以按工程名找不到 GUI —— 必须按脚本路径匹配。
    """
    script = (script_path or "").replace("\\", "/").lower()
    stem = Path(sem_path).stem.lower()
    out: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (proc.info.get("name") or "").lower()
        cmd = " ".join(proc.info.get("cmdline") or []).replace("\\", "/").lower()
        is_gui = name in ("laspcem.exe", "laspcemapp.exe") and (
            (script and script in cmd) or (stem and stem in cmd))
        is_solver = name in ("mpiexec.exe", "phofem.exe") and bool(stem) and stem in cmd
        if is_gui or is_solver:
            out.append(proc.info["pid"])
    return out


def _kill_orphan_mpi(since: float) -> None:
    """兼容旧调用名：实现已搬到 launcher（超时路径也要用），这里只转调。"""
    _kill_orphan_mpi_since(since)


def _kill_task(handle: dict[str, Any], sem_path: str) -> None:
    """取消时彻底停掉一次作业：GUI 进程树 + mpiexec/phofem + MPI 代理，15 s 内反复补杀。

    只 terminate GUI 会留下孤儿求解器；只杀求解器，GUI 会立刻开始下一轮自适应 —— 两头都要。
    """
    started = time.time()
    proc = handle.get("proc")
    if proc is not None:
        with contextlib.suppress(psutil.Error):
            for child in psutil.Process(proc.pid).children(recursive=True):
                _terminate_pid(child.pid)
        with contextlib.suppress(OSError, psutil.Error):   # 句柄可能已退出，杀不掉不算失败
            proc.kill()

    deadline = time.time() + 15
    while time.time() < deadline:
        left = _related_pids(handle.get("script_path", ""), sem_path)
        if not left:
            break
        for pid in left:
            # 先杀子进程（mpiexec 的子树里有 hydra_pmi_proxy / phofem），再杀本体
            with contextlib.suppress(psutil.Error):
                for child in psutil.Process(pid).children(recursive=True):
                    _terminate_pid(child.pid)
            _terminate_pid(pid)
        time.sleep(0.5)
    _kill_orphan_mpi(started)


def _entry_with_context(func, event: threading.Event, handle: dict,
                        owner: str = "", sem_path: str = ""):
    """把内部实现包一层：进线程先设上下文（取消信号/句柄 + 工程占用），出来清掉。

    工程占用登记是 P0 修复的关键：求解期间该 .sem 在 `project_registry` 里被登记，
    同步 export/report 的入口据此回 PROJECT_IN_USE，避免起第二个实例抢工程
    （`_project_in_use` 看不到 /session 实例，见模块 docstring）。
    """
    def entry(*args, **kwargs):
        set_task_context(event, handle)
        project_registry.bind_thread(owner)
        if owner and sem_path:
            project_registry.acquire(sem_path, owner, "solve")
        try:
            return func(*args, **kwargs)
        finally:
            if owner and sem_path:
                project_registry.clear_owner(owner)
            project_registry.bind_thread("")
            clear_task_context()
    return entry


def _reap(task_id: str, snap: dict[str, Any] | None) -> None:
    """任务进入终态后丢掉取消信号/句柄/进度引用，避免长跑服务里几张表只增不减。"""
    if not snap or not snap.get("finished_at"):
        return
    with _events_lock:
        _cancel_events.pop(task_id, None)
        _handles.pop(task_id, None)
        _phases.pop(task_id, None)


def _reap_stale() -> int:
    """清掉「已从 TaskRunner 淘汰、却还留在本地三张表里」的条目。

    本地表（_phases/_cancel_events/_handles）原本只在 `laspcem_solve_query` 读到终态时
    才回收；调用方"提交后从不查询"或任务被 TTL/max_tasks 淘汰时，条目会永久残留
    （2026-11-13 审查）。这里以 TaskRunner 的存活任务为基准兜底回收。
    """
    alive = {t.get("task_id") for t in _runner.list()}
    reaped = 0
    with _events_lock:
        stale = [tid for tid in _phases if tid not in alive]
        for tid in stale:
            _phases.pop(tid, None)
            _cancel_events.pop(tid, None)
            _handles.pop(tid, None)
            reaped += 1
    if len(_progress_cache) > 50:
        _progress_cache.clear()
    return reaped


def _cached_progress(sem_path: str, ttl: float = 2.0, since: float = 0.0) -> dict[str, Any]:
    """带 2 s 缓存的求解进度：`solve_progress` 要扫结果目录 + 枚举全表进程，
    轮询密集时太贵（2026-11-13 审查）。读写都持 `_events_lock`，与其它表一致。

    `since`（任务 started_at）进缓存 key：同一工程前后两个任务的进度不能混用。
    """
    now = time.time()
    key = (sem_path, round(since, 3))
    with _events_lock:
        hit = _progress_cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = solve_progress(sem_path, since=since)
    with _events_lock:
        _progress_cache[key] = (now, value)
    return value


def kill_running_tasks(reason: str = "服务停止") -> int:
    """进程退出前收掉所有 RUNNING 的 LASPCEM 任务，返回处理条数。

    不做这件事的后果（2026-11-13 审查）：`TaskRunner` 的 worker 是非 daemon 线程，
    `sys.exit` 后解释器会等它跑完（可能 30 分钟）；强杀则留下孤儿 GUI + mpiexec，
    而重启后内存任务表已丢，`laspcem_task_cancel` 拿不到 task_id 就再也清不掉。
    """
    handled = 0
    try:
        for task in _runner.list():
            if task.get("finished_at") is not None or task.get("status") != "RUNNING":
                continue
            tid = task.get("task_id") or ""
            sem = (task.get("metadata") or {}).get("sem_path") or ""
            event = _cancel_events.get(tid)
            if event is not None:
                event.set()
            if sem:
                _kill_task(_handles.get(tid) or {}, sem)
                handled += 1
    except Exception:  # noqa: BLE001 — 退出路径绝不能抛
        return handled
    return handled


def _submit_laspcem_task(func, resolved: str, metadata: dict[str, Any],
                         state: dict | None = None, **kwargs) -> dict[str, Any]:
    """校验后把任务丢进队列：返回 submitted_response 或错误响应。

    事件/句柄必须在 submit 之前建好并传进去 —— 否则 cancel 打不到已经在跑的任务。
    """
    # 与 TaskRunner 的 max_tasks 构成双闸门：这里先快速拒（不占 submit 路径、不进日志噪声），
    # 保留是有意的防御性冗余 —— 有测试 `test_queue_full_rejected` 锁定该行为（2026-11-13）。
    if _runner.pending_count() >= 20:
        return queue_full_response("LASPECM_QUEUE_FULL", message="排队任务过多，请稍后再提交")
    event = threading.Event()
    handle: dict = {}
    state = state if state is not None else {}
    # state 必须显式传进任务体：它是"同一个 dict"，任务体写 phase/steps，查询读同一份
    owner = f"task:{uuid.uuid4().hex[:8]}"
    task_id = _runner.submit(_entry_with_context(func, event, handle, owner, resolved), resolved,
                             metadata=metadata, state=state, **kwargs)
    if task_id is None:
        return queue_full_response("LASPECM_QUEUE_FULL", message="提交失败，请稍后重试")
    with _events_lock:
        _cancel_events[task_id] = event
        _handles[task_id] = handle
        _phases[task_id] = state
    resp = submitted_response(task_id, message="任务已提交（后台串行执行）", sem_path=resolved)
    if state.get("steps"):
        resp["steps"] = state["steps"]
    return resp


def _mark(steps: list[dict], name: str, status: str) -> None:
    for step in steps:
        if step["name"] == name:
            step["status"] = status


_DONE_STATES = ("SUCCEEDED", "SKIPPED")


def _aggregate_steps(steps: list[dict]) -> str:
    """按 steps 聚合任务状态（顶层 runner status 只表示「任务体是否正常返回」，不聚合步骤）。

    2026-11-26 实测：solve 失败时 TaskRunner 仍把任务记为 SUCCEEDED（任务体正常 return 了
    一个 success=False 的结果），`laspcem_task_list` 直接透传该状态会误报成功。
    """
    states = [str(s.get("status") or "").upper() for s in steps]
    if not states:
        return "UNKNOWN"
    if "FAILED" in states:
        return "FAILED"
    if "CANCELLED" in states:
        return "CANCELLED"
    if all(s in _DONE_STATES for s in states):
        return "SUCCEEDED"
    if any(s == "RUNNING" for s in states):
        return "RUNNING"
    return "PENDING"


def _run_solve_chain(
    sem_path: str,
    setup_name: str = "",
    timeout_seconds: int = 7200,
    stall_seconds: float = 900.0,
    then_export: bool = False,
    then_report: bool = False,
    export_path: str = "",
    report_name: str = "Sparameter_1",
    port_count: int = 0,
    state: dict | None = None,
) -> dict[str, Any]:
    """任务体：求解 →（可选）导出 →（可选）建报告。后处理由服务端串，失败不吞前一步结果。"""
    steps = [{"name": "solve", "status": "PENDING"}]
    if then_export:
        steps.append({"name": "export", "status": "PENDING"})
    if then_report:
        steps.append({"name": "report", "status": "PENDING"})
    if state is not None:
        state["steps"] = steps
        state["phase"] = "solving"
    _mark(steps, "solve", "RUNNING")

    # 后处理要独占工程 ⇒ 求解这一步始终关 GUI（否则导出会撞 PROJECT_IN_USE）
    solve_r = run_laspcem_solve(sem_path, setup_name=setup_name,
                                timeout_seconds=timeout_seconds, close_gui=True,
                                stall_seconds=stall_seconds)
    solved_ok = solve_r.get("success") is True
    if not solved_ok:
        # “本来就已经求解过、被 LASPCEM 跳过”不算失败：工程里的解还在，后处理可以继续
        skipped = (solve_r.get("error_code") == "SOLVE_NOT_TRIGGERED"
                   and solve_r.get("skipped_likely") and is_solved(sem_path))
        solved_ok = bool(skipped)
    _mark(steps, "solve", "SUCCEEDED" if solved_ok else "FAILED")
    if not solved_ok:
        if state is not None:
            state["phase"] = "failed"
        # 失败后不执行的后续步骤要标 SKIPPED，不能留 PENDING（读起来像"还在跑"）
        for rest in ("export", "report"):
            _mark(steps, rest, "SKIPPED")
        return {"success": False,
                "error_code": solve_r.get("error_code") or "LASPECM_SOLVE_FAILED",
                "message": f"求解失败，后续步骤未执行：{solve_r.get('message')}",
                "steps": steps, "solve_result": solve_r}

    out: dict[str, Any] = {"success": True, "steps": steps, "solve_result": solve_r,
                           "solve_skipped": solve_r.get("success") is not True}

    if then_export:
        if state is not None:
            state["phase"] = "exporting"
        _mark(steps, "export", "RUNNING")
        exp = export_laspcem_snp(sem_path, output_path=export_path)
        out["export_result"] = exp
        if exp.get("success") is not True:
            _mark(steps, "export", "FAILED")
            if state is not None:
                state["phase"] = "failed"
            # 导出失败后不会建报告 ⇒ 标 SKIPPED，别留 PENDING 像"还在跑"
            _mark(steps, "report", "SKIPPED")
            out.update({"success": False,
                        "error_code": exp.get("error_code") or "LASPECM_EXPORT_FAILED",
                        "message": f"求解已完成，但导出失败：{exp.get('message')}"})
            return out
        _mark(steps, "export", "SUCCEEDED")

    if then_report:
        if state is not None:
            state["phase"] = "reporting"
        _mark(steps, "report", "RUNNING")
        rep = build_laspcem_report(sem_path, report_name=report_name, port_count=port_count)
        out["report_result"] = rep
        if rep.get("success") is not True:
            _mark(steps, "report", "FAILED")
            if state is not None:
                state["phase"] = "failed"
            out.update({"success": False,
                        "error_code": rep.get("error_code") or "LASPECM_REPORT_FAILED",
                        "message": f"求解已完成，但建报告失败：{rep.get('message')}"})
            return out
        _mark(steps, "report", "SUCCEEDED")

    if state is not None:
        state["phase"] = "done"
    return out


@mcp.tool()
def run_laspcem_solve_async(
    sem_path: str,
    setup_name: str = "",
    timeout_seconds: int = 7200,
    stall_seconds: int = 900,
    then_export: bool = False,
    then_report: bool = False,
    export_path: str = "",
    report_name: str = "Sparameter_1",
    port_count: int = 0,
) -> dict[str, Any]:
    """提交 LASPCEM 求解任务（异步，立即返回 task_id）

    用法：后台跑这个 sem 的求解；求解完顺便导出 sNp（then_export=True）；求解完建报告

    求解是最耗时的操作，所以只有它做成异步；**后处理由服务端串起来**：
    `then_export=True` / `then_report=True` 声明后，服务端会在求解完成后自动接着做，
    调用方只提交一次、用 `laspcem_solve_query` 查同一个 task_id 就能拿到全部结果。

    Args:
        sem_path: `.sem` 工程绝对路径。
        setup_name: 求解 setup 名；留空自动探测。传错名字会**立刻**返回 SETUP_NOT_FOUND（不进队列）。
        timeout_seconds: 求解超时（后台线程内生效）。
        stall_seconds: 卡死判定（求解器还在但日志/工程/CPU 这么久没进展 ⇒ SOLVE_STALLED）；0 = 关闭。
        then_export: 求解完成后自动导出 sNp（服务端执行，结果在 `export_result`）。
        then_report: 求解完成后自动建报告（结果在 `report_result`）。
        export_path: 指定 sNp 输出路径；留空落到默认产物目录（<result_dir>/export_laspcem_snp_<时间戳>.sNp，实际后缀按端口数 .s2p/.s4p）。
        report_name: 报告名，默认 Sparameter_1。
        port_count: 报告曲线端口数；0 = 自动（按 sNp 后缀推断，兜底 2）。

    Returns:
        {"success": True, "task_id": "…", "status": "QUEUED", "steps": ["solve","export"],
         "sem_path": "…", "message": "任务已提交"}
    """
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    # 参数校验在提交阶段同步做：错名字不该先占一个队列位、跑起来才失败
    bad_setup = _check_explicit_setup(resolved, setup_name)
    if bad_setup:
        return bad_setup

    steps = [{"name": "solve"}]
    if then_export:
        steps.append({"name": "export"})
    if then_report:
        steps.append({"name": "report"})

    resp = _submit_laspcem_task(
        _run_solve_chain, resolved,
        metadata={"sem_path": resolved, "setup_name": setup_name, "operation": "solve",
                  "steps": [s["name"] for s in steps]},
        state={"phase": "queued", "steps": steps},
        setup_name=setup_name, timeout_seconds=timeout_seconds,
        stall_seconds=stall_seconds, then_export=then_export, then_report=then_report,
        export_path=export_path, report_name=report_name, port_count=port_count,
    )
    if resp.get("success"):
        resp["setup_name"] = setup_name
        resp["steps"] = [s["name"] for s in steps]
    return resp


_MAX_WAIT_SECONDS = MAX_QUERY_WAIT_SECONDS   # 兼容旧引用
# 具体实现已提到公共层（CST / EDI 的 query 复用同一套），旧名保留给既有测试与调用方
_bounded_wait = bounded_wait


@mcp.tool()
def laspcem_solve_query(task_id: str, wait_seconds: int = 0) -> dict[str, Any]:
    """查询 LASPCEM 求解任务（配合 run_laspcem_solve_async 的 task_id）。

    运行中回传 `phase`（solving/exporting/reporting）、`elapsed_seconds`、`adapt_iterations`、
    `last_log_line`；完成后回传 `solve_result` 以及服务端串出来的 `export_result` /
    `report_result`（取决于提交时的 then_* 参数）。

    Args:
        task_id: run_laspcem_solve_async 返回的 task_id。
        wait_seconds: 阻塞等待上限（秒），默认 0 = 立即返回快照；会被 clamp 到 [0, 120]。
            短求解（导出、已完成的求解）可以用它一次拿到终态；超过 120 s 的求解仍应轮询。
    """
    _bounded_wait(task_id, wait_seconds, _runner.snapshot)
    snap = _runner.snapshot(task_id)
    if snap is None:
        # 顶层 status 与 CST/EDI 的 *_query 对齐（那边都回 status），按 status 判断的
        # 调用方不会拿到 None（2026-11-27 实测：只有这里缺）
        resp = error_response("TASK_NOT_FOUND", "任务不存在、已过期或服务已重启",
                             task_id=task_id)
        resp["status"] = "UNKNOWN"
        return resp

    status = snap.get("status")
    if status == "CANCELLED":
        _reap(task_id, snap)
        resp = error_response("TASK_CANCELLED", snap.get("error") or "任务已取消",
                             task_id=task_id)
        resp["status"] = "CANCELLED"
        return resp

    with _events_lock:
        state = dict(_phases.get(task_id) or {})
    if not snap.get("finished_at"):
        out: dict[str, Any] = {"success": True, "task_id": task_id, "status": status,
                               "completed": False, "message": "任务进行中"}
        if state.get("phase"):
            out["phase"] = state["phase"]
        if state.get("steps"):
            out["steps"] = state["steps"]
        if snap.get("started_at"):
            out["elapsed_seconds"] = round(time.time() - snap["started_at"], 1)
        sem = (snap.get("metadata") or {}).get("sem_path")
        if sem:
            # 只算本次任务开始后的日志，避免把上一个任务的 Iter/末行当成本次进度
            out.update(_cached_progress(sem, since=snap.get("started_at") or 0.0))
        return out

    _reap(task_id, snap)
    result = snap.get("result")
    if isinstance(result, dict) and result.get("success") is False:
        resp = error_response(result.get("error_code") or "LASPECM_SOLVE_FAILED",
                              result.get("message") or "任务失败",
                              task_id=task_id)
        # 业务失败：任务本身可能记为 SUCCEEDED（跑完了但求解/导出结果失败），
        # 顶层 status 按业务结论给 FAILED，原始任务态留在 details 里
        resp["status"] = "FAILED"
        resp["details"]["task_status"] = status
        for key in ("steps", "solve_result", "export_result", "report_result"):
            if key in result:
                resp[key] = result[key]
        # 外层 hint 是错误码词典查的（SOLVE_TIMEOUT → do_not_retry），内层 solve_result
        # 明确 retry_safe；不覆盖会让 agent 误判「不可重试」（2026-11-13 实测）
        inner_hint = (result.get("solve_result") or {}).get("hint")
        if inner_hint:
            resp["hint"] = inner_hint
        return resp

    out = {"success": True, "task_id": task_id, "status": status, "completed": True,
           "phase": "done", "message": "任务完成"}
    if isinstance(result, dict):
        for key in ("steps", "solve_result", "export_result", "report_result", "solve_skipped"):
            if key in result:
                out[key] = result[key]
    return out


@mcp.tool()
def laspcem_task_cancel(task_id: str) -> dict[str, Any]:
    """取消 LASPCEM 求解任务（求解/导出/建报告 任一阶段都能取消）。

    取消后：等待循环立刻收手、跳过补保存，并结束 LASPCEM GUI 与它的 mpiexec/phofem/MPI 代理。

    Args:
        task_id: run_laspcem_solve_async 返回的 task_id。
    """
    snap = _runner.snapshot(task_id)
    if snap is None:
        return error_response("TASK_NOT_FOUND", "任务不存在、已过期或服务已重启", task_id=task_id)
    if snap.get("finished_at"):
        return error_response("TASK_ALREADY_FINISHED", "任务已结束，无法取消",
                             task_id=task_id, status=snap.get("status"))

    sem = (snap.get("metadata") or {}).get("sem_path", "")
    handle = _handles.get(task_id) or {}
    event = _cancel_events.get(task_id)

    def _do_kill() -> None:
        if event is not None:
            event.set()
        if sem:
            _kill_task(handle, sem)

    ok, cancel_msg = _runner.cancel(task_id, kill=_do_kill)
    if not ok:
        return error_response("TASK_ALREADY_FINISHED", cancel_msg or "任务已结束，无法取消",
                             task_id=task_id, status=(_runner.snapshot(task_id) or {}).get("status"))
    with _events_lock:
        # 三张表一起回收：_reap_stale 以 _phases 为基准找 stale，只 pop _phases
        # 会让 _handles（含 GUI 句柄）/_cancel_events 永远扫不到（2026-11-13 复核）
        _phases.pop(task_id, None)
        _cancel_events.pop(task_id, None)
        _handles.pop(task_id, None)
    return {"success": True, "task_id": task_id, "status": "CANCELLED",
            "message": cancel_msg}


@mcp.tool()
def laspcem_task_list() -> dict[str, Any]:
    """列出 LASPCEM 后台任务（含 operation=solve、phase、steps、状态与耗时）。"""
    reaped = _reap_stale()
    tasks = []
    for snap in _runner.list():
        meta = snap.get("metadata") or {}
        with _events_lock:
            state = dict(_phases.get(snap.get("task_id")) or {})
        steps_detail = state.get("steps") or []
        status = _aggregate_steps(steps_detail)
        # 排队/未开始时 steps 还没状态（UNKNOWN/PENDING），回退到 runner 的真实状态（QUEUED/RUNNING）
        if status in ("UNKNOWN", "PENDING"):
            status = snap.get("status")
        item = {
            "task_id": snap.get("task_id"),
            # status = 按 steps 聚合的真实状态（steps 还没状态时回退 runner_status）
            "status": status,
            "runner_status": snap.get("status"),
            "operation": meta.get("operation", ""),
            "sem_path": meta.get("sem_path", ""),
            # metadata.steps 存的是名字列表；state.steps 是 [{name,status}]
            "steps": meta.get("steps") or [s.get("name") for s in steps_detail],
            "steps_detail": steps_detail,
            "created_at": snap.get("created_at"),
            "started_at": snap.get("started_at"),
            "finished_at": snap.get("finished_at"),
            "error": snap.get("error"),
        }
        if state.get("phase"):
            item["phase"] = state["phase"]
        tasks.append(item)
    return {"success": True, "count": len(tasks), "tasks": tasks,
            "reaped_stale": reaped}


# ── 孤儿进程清理（服务重启/强杀后唯一的收拾手段）────────────────────────

#: LASPCEM 求解器相关进程名 —— 它们的 cmdline 常常不含工程路径（判不出归属）
_SOLVER_NAMES = frozenset({"mpiexec.exe", "phofem.exe", "hydra_pmi_proxy.exe"})

_SEM_RE = re.compile(r'[^\s"\']+\.sem\b', re.IGNORECASE)


def _sem_of_process(cmd: str) -> str:
    """尽力判定一个 LASPCEM 相关进程属于哪个 .sem（判不出返回空串）。

    两种 GUI 形态：cmdline 直接带 .sem；或 `laspcem.exe /session <临时脚本>` ——
    工程路径只写在脚本内容里（`async_solve._related_pids` 的实测注释就是这点）。
    """
    hits = _SEM_RE.findall(cmd)
    if hits:
        return hits[0]
    if "/session" in cmd.lower():
        parts = cmd.split()
        for i, tok in enumerate(parts):
            if tok.lower() == "/session" and i + 1 < len(parts):
                script = parts[i + 1].strip('"')
                try:
                    text = Path(script).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return ""
                inner = _SEM_RE.findall(text)
                if inner:
                    return inner[0]
    return ""


@mcp.tool()
def laspcem_cleanup_orphans(sem_path: str = "", dry_run: bool = False,
                            include_unattributed: bool = False) -> dict[str, Any]:
    """清理残留的 LASPCEM 进程（GUI / mpiexec / phofem / MPI 代理）。

    服务重启或强杀后内存任务表已丢失，`laspcem_task_cancel` 拿不到 task_id，
    这些孤儿会一直占 CPU 和工程目录。本工具按工程精确定位并终止它们。

    用法：服务重启后收拾残留；求解卡死时手工清场

    Args:
        sem_path: 只清理这个工程的残留；留空 = 清理所有「能判定归属、且不属于在跑任务」的进程。
        dry_run: True 只报告要杀谁、不动手（建议先跑一次看清）。
        include_unattributed: True 时连**判定不出归属**的 `laspcem.exe` 也清（cmdline 里没有
            工程路径的实例，例如 `open_laspcem_project` 起的、LASPCEM re-exec 后丢掉参数的）。
            默认 False：用户自己 File→Open 打开的窗口 cmdline 同样是纯 `laspcem.exe`，
            默认保守跳过；确认没有人工窗口时再开这个开关。

    Returns:
        {"success": True, "dry_run": False, "killed": [{pid,name,sem_path,reason}],
         "skipped": [...], "active_sems": [...]}
    """
    want = project_registry.canon(sem_path)
    try:
        want_stem = Path(sem_path).stem.lower() if sem_path else ""
    except (OSError, ValueError):
        want_stem = ""

    active_sems: set[str] = set()
    for task in _runner.list():
        if task.get("finished_at") is not None:
            continue
        active_sems.add(project_registry.canon((task.get("metadata") or {}).get("sem_path", "")))
    active_stems = {Path(s).stem.lower() for s in active_sems if s}

    killed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (proc.info.get("name") or "").lower()
        if name not in ("laspcem.exe", "laspcemapp.exe", "mpiexec.exe",
                        "phofem.exe", "hydra_pmi_proxy.exe"):
            continue
        cmd = " ".join(proc.info.get("cmdline") or [])
        pid = proc.info.get("pid")
        owner_sem = _sem_of_process(cmd)
        owner_key = project_registry.canon(owner_sem) if owner_sem else ""

        # 求解器类进程（mpiexec / phofem / hydra）的 cmdline 里常常**没有工程路径**：
        # 实测 `phofem Design ADAPTIVE-MESH`、`hydra_pmi_proxy --usize -1 --auto-cleanup 1`。
        # 这类既判不出归属、也匹配不到工程 stem，旧代码直接静默 continue ⇒
        # 「重启后清理孤儿」这个主用途正好漏掉最关键的求解器本体（2026-11-13 实测）。
        if not owner_key and name in _SOLVER_NAMES:
            if want:
                skipped.append({"pid": pid, "name": proc.info.get("name"), "sem_path": "",
                                "reason": "求解器类进程但 cmdline 无工程路径，无法确认属于该工程，保守跳过"})
                continue
            if active_sems:
                skipped.append({"pid": pid, "name": proc.info.get("name"), "sem_path": "",
                                "reason": "当前有在跑的任务，无法确认归属，保守跳过"})
                continue
            item = {"pid": pid, "name": proc.info.get("name"), "sem_path": "",
                    "reason": "dry_run" if dry_run else "terminated"}
            if dry_run:
                killed.append(item)          # dry_run 里 killed = 「将要清掉的」
                continue
            try:
                psutil.Process(pid).terminate()
                killed.append(item)
            except (psutil.Error, OSError) as exc:
                item["reason"] = f"terminate 失败：{exc}"
                skipped.append(item)
            continue

        # 目标筛选：给了工程 ⇒ 只动归属该工程的（判不出归属时用 stem 兜底）
        if want:
            if owner_key:
                if owner_key != want:
                    continue
            elif not (want_stem and want_stem in cmd.lower()):
                continue
        else:
            # 未指定工程：只动「能判定归属」的；判不出归属的不碰（保守，避免误杀手动开的窗口）。
            # 但必须**记录**下来：cmdline 读不出工程路径的实例（如 /session 的临时脚本已被删）
            # 属于"我看到了但没敢动"，不能静默吞掉（2026-11-13 复核，与 solver 分支口径一致）。
            if not owner_key:
                if include_unattributed and name == "laspcem.exe":
                    # reason 与其它分支保持同一口径：dry_run 就写 "dry_run"，
                    # 否则报告里看不出"这只是个预览"（2026-11-13 复核）
                    killed.append({"pid": pid, "name": proc.info.get("name"), "sem_path": "",
                                   "reason": "dry_run" if dry_run
                                   else "显式要求清理无归属实例（include_unattributed）"})
                    if not dry_run:
                        _terminate_pid(pid)
                    continue
                skipped.append({"pid": pid, "name": proc.info.get("name"), "sem_path": "",
                                "reason": "cmdline 无工程路径（可能 /session 脚本已删），保守跳过"})
                continue
            if owner_key in active_sems:
                skipped.append({"pid": pid, "name": proc.info.get("name"),
                                "sem_path": owner_sem, "reason": "有正在跑的任务，别杀"})
                continue

        # 该工程的求解任务正在跑时一律不动（活任务应走 laspcem_task_cancel）。
        # 2026-11-13 复核：原来的 `and owner_key != want` 让「显式指定正在求解的工程」
        # 这个**最该保护**的场景漏过守卫 —— want 分支里 owner_key 已等于 want，
        # 条件恒 False，于是会 terminate 掉活着的求解器。
        if owner_key in active_sems:
            skipped.append({"pid": pid, "name": proc.info.get("name"), "sem_path": owner_sem,
                            "reason": "该工程有正在跑的任务，别杀（要停请用 laspcem_task_cancel）"})
            continue
        if not owner_key and want_stem and want_stem in active_stems:
            skipped.append({"pid": pid, "name": proc.info.get("name"),
                            "sem_path": "", "reason": "该工程有正在跑的任务，别杀"})
            continue

        item = {"pid": pid, "name": proc.info.get("name"), "sem_path": owner_sem,
                "reason": "dry_run" if dry_run else "terminated"}
        if dry_run:
            killed.append(item)
            continue
        try:
            psutil.Process(pid).terminate()
            killed.append(item)
        except (psutil.Error, OSError) as exc:
            item["reason"] = f"terminate 失败：{exc}"
            skipped.append(item)

    return {"success": True, "dry_run": bool(dry_run), "killed": killed, "skipped": skipped,
            "active_sems": sorted(s for s in active_sems if s),
            "message": ("仅报告（dry_run），未终止任何进程" if dry_run
                        else f"已终止 {len(killed)} 个残留进程")}
