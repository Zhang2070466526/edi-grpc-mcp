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
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from servers import mcp
from servers.laspcem.launcher import (
    _check_explicit_setup,
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
    """补杀 LASPCEM 自带的 MPI 代理（`hydra_pmi_proxy.exe`）。

    实测（2026-11-12）：mpiexec 被杀后它可能残留，cmdline 里**没有工程路径**（只有
    `--usize -1 --auto-cleanup 1`），按工程名匹配不到，会把结果目录句柄一直占住
    （删目录报 "Device or resource busy"）。这里按「exe 位于 LASPCEM solver 目录 + 启动时间
    不早于本次任务」筛，避免误杀其它 MPI 作业。
    """
    for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        if (proc.info.get("name") or "").lower() != "hydra_pmi_proxy.exe":
            continue
        exe = (proc.info.get("exe") or "").lower().replace("\\", "/")
        created = proc.info.get("create_time") or 0
        if "laspcem" in exe and created >= since - 5:
            _terminate_pid(proc.info["pid"])


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


def _entry_with_context(func, event: threading.Event, handle: dict):
    """把内部实现包一层：进线程先设上下文（取消信号/句柄），出来清掉。"""
    def entry(*args, **kwargs):
        set_task_context(event, handle)
        try:
            return func(*args, **kwargs)
        finally:
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


def _submit_laspcem_task(func, resolved: str, metadata: dict[str, Any],
                         state: dict | None = None, **kwargs) -> dict[str, Any]:
    """校验后把任务丢进队列：返回 submitted_response 或错误响应。

    事件/句柄必须在 submit 之前建好并传进去 —— 否则 cancel 打不到已经在跑的任务。
    """
    if _runner.pending_count() >= 20:
        return queue_full_response("LASPECM_QUEUE_FULL", message="排队任务过多，请稍后再提交")
    event = threading.Event()
    handle: dict = {}
    state = state if state is not None else {}
    # state 必须显式传进任务体：它是"同一个 dict"，任务体写 phase/steps，查询读同一份
    task_id = _runner.submit(_entry_with_context(func, event, handle), resolved,
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


def _run_solve_chain(
    sem_path: str,
    setup_name: str = "",
    timeout_seconds: int = 1800,
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
    timeout_seconds: int = 1800,
    stall_seconds: int = 900,
    then_export: bool = False,
    then_report: bool = False,
    export_path: str = "",
    report_name: str = "Sparameter_1",
    port_count: int = 0,
) -> dict[str, Any]:
    """提交 LASPCEM 求解任务（异步，立即返回 task_id）—— 大模型求解用这个。

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
        export_path: 指定 sNp 输出路径（留空按端口数自动命名）。
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


@mcp.tool()
def laspcem_solve_query(task_id: str) -> dict[str, Any]:
    """查询 LASPCEM 求解任务（配合 run_laspcem_solve_async 的 task_id）。

    运行中回传 `phase`（solving/exporting/reporting）、`elapsed_seconds`、`adapt_iterations`、
    `last_log_line`；完成后回传 `solve_result` 以及服务端串出来的 `export_result` /
    `report_result`（取决于提交时的 then_* 参数）。

    Args:
        task_id: run_laspcem_solve_async 返回的 task_id。
    """
    snap = _runner.snapshot(task_id)
    if snap is None:
        return error_response("TASK_NOT_FOUND", "任务不存在、已过期或服务已重启",
                             task_id=task_id)

    status = snap.get("status")
    if status == "CANCELLED":
        _reap(task_id, snap)
        return error_response("TASK_CANCELLED", snap.get("error") or "任务已取消",
                             task_id=task_id, status="CANCELLED")

    state = _phases.get(task_id) or {}
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
            out.update(solve_progress(sem))
        return out

    _reap(task_id, snap)
    result = snap.get("result")
    if isinstance(result, dict) and result.get("success") is False:
        resp = error_response(result.get("error_code") or "LASPECM_SOLVE_FAILED",
                              result.get("message") or "任务失败",
                              task_id=task_id, status=status)
        for key in ("steps", "solve_result", "export_result", "report_result"):
            if key in result:
                resp[key] = result[key]
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

    ok = _runner.cancel(task_id, kill=_do_kill)
    if not ok:
        return error_response("TASK_ALREADY_FINISHED", "任务已结束，无法取消",
                             task_id=task_id, status=(_runner.snapshot(task_id) or {}).get("status"))
    with _events_lock:
        _phases.pop(task_id, None)
    return {"success": True, "task_id": task_id, "status": "CANCELLED",
            "message": "任务已取消（执行中，已尝试终止）"}


@mcp.tool()
def laspcem_task_list() -> dict[str, Any]:
    """列出 LASPCEM 后台任务（含 operation=solve、phase、steps、状态与耗时）。"""
    tasks = []
    for snap in _runner.list():
        meta = snap.get("metadata") or {}
        state = _phases.get(snap.get("task_id")) or {}
        item = {
            "task_id": snap.get("task_id"),
            "status": snap.get("status"),
            "operation": meta.get("operation", ""),
            "sem_path": meta.get("sem_path", ""),
            # metadata.steps 存的是名字列表；state.steps 是 [{name,status}]
            "steps": meta.get("steps") or [s.get("name") for s in (state.get("steps") or [])],
            "created_at": snap.get("created_at"),
            "started_at": snap.get("started_at"),
            "finished_at": snap.get("finished_at"),
            "error": snap.get("error"),
        }
        if state.get("phase"):
            item["phase"] = state["phase"]
        tasks.append(item)
    return {"success": True, "count": len(tasks), "tasks": tasks}
