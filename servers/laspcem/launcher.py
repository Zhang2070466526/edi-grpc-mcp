"""LASPCEM 工具 — 启动软件 / 打开 .sem 工程 / 求解 / 导出 S 参数 / 建报告。

LASPCEM 无 COM/gRPC 接口，程序化入口是内嵌 Python 脚本（app/cem/report 全局对象），
通过命令行 `laspcem /session <脚本>` 执行（等价于 GUI 的 Run Script，实测会真正跑求解，
FEMAnalyze 会拉起 mpiexec + phofem.exe 求解器）。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from servers import mcp
from servers.laspcem import project_registry
from servers.laspcem.config import (
    find_laspcem_exe,
    find_laspcem_processes,
    laspcem_is_running,
)
from servers.laspcem.script_gen import (
    build_export_snp_script,
    build_import_aedt_script,
    build_report_script,
    build_save_script,
    build_solve_script,
    default_log_path,
)
from servers.laspcem.setup_reader import (
    detect_port_count,
    detect_setup_name,
    detect_setup_names,
    detect_solution_names,
    extract_design_variables,
    is_solved,
    read_setup_report,
    split_setup_name,
)
from servers.utils import default_result_path, error_response, require_nonempty, validate_file


def _resolve_exe(laspcem_path: str) -> tuple[str | None, dict | None]:
    """定位并校验 laspcem.exe；失败返回 (None, error_dict)。"""
    exe = (laspcem_path or "").strip() or find_laspcem_exe()
    if not exe:
        return None, error_response(
            "LASPCEM_NOT_FOUND",
            "未定位到 laspcem.exe；请设置 LASPCEM_PATH 环境变量，或确认已安装 LASPCEM",
            hint="自动检测顺序：环境变量 LASPCEM_PATH → 注册表卸载项（DisplayName 含 LASPCEM）→ 注册表 .sem 文件关联")
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        return None, error_response("LASPCEM_NOT_FOUND", f"laspcem.exe 不存在: {exe_path}")
    return str(exe_path), None


@mcp.tool()
def launch_laspcem(laspcem_path: str = "") -> dict[str, Any]:
    """启动 LASPCEM 电磁仿真软件。已运行时返回状态，不重复启动。

    用法："打开 LASPCEM"、"启动 laspcem"

    Args:
        laspcem_path: laspcem.exe 路径，默认自动检测（LASPCEM_PATH → 注册表卸载项 → 注册表 .sem 关联）。

    Returns:
        {"success": True, "started": bool, "already_running": bool, "exe": "...", "message": "..."}
    """
    exe, err = _resolve_exe(laspcem_path)
    if err:
        return err

    already = laspcem_is_running()
    if not already:
        try:
            subprocess.Popen([exe], cwd=str(Path(exe).parent),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return error_response("LAUNCH_FAILED", f"无法启动 laspcem.exe: {exc}")

    return {
        "success": True,
        "started": not already,
        "already_running": already,
        "exe": exe,
        "message": "LASPCEM 已在运行，未重复启动" if already else "LASPCEM 已启动",
    }


def _same_path(a: str, b: str) -> bool:
    """两个路径是否指向同一个文件（大小写/相对路径不敏感）。"""
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except OSError:
        return False


@mcp.tool()
def open_laspcem_project(project_path: str, laspcem_path: str = "",
                         force_new_instance: bool = False) -> dict[str, Any]:
    """在 LASPCEM 中打开 .sem 工程文件（GUI 打开与后台求解互斥，先求解后打开）。

    等价于双击 .sem 文件：laspcem.exe "<project_path>"。

    ⚠️ 实测（2026-11-10）：LASPCEM **没有进程间投递**（无 COM/gRPC/IPC），带文件启动一律新起
    一个 GUI 进程，不会交给已在跑的实例。所以本工具默认做「同工程去重」——目标 .sem 已经在
    某个实例的命令行里就直接复用、不再起新实例（返回 reused_existing=True + 该实例 pid）；
    要强制新开实例传 force_new_instance=True。返回里 running_instances 给出调用后的实例 PID 列表。

    用法："在 LASPCEM 中打开 C:/Users/xx/demo.sem"、"用 laspcem 打开这个工程"

    Args:
        project_path: .sem 工程文件绝对路径。
        laspcem_path: laspcem.exe 路径，默认自动检测。
        force_new_instance: 该工程已在某实例中打开时也强制再起一个新实例（默认 False）。

    Returns:
        {"success": True, "exe": "...", "project": "<绝对路径>", "reused_existing": bool,
         "new_instance": bool, "pid": int | None, "running_instances": [pid, ...], "message": "..."}
    """
    try:
        resolved = validate_file(project_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe, err = _resolve_exe(laspcem_path)
    if err:
        return err

    procs = find_laspcem_processes()
    if not force_new_instance:
        for proc in procs:
            if proc["project"] and _same_path(proc["project"], resolved):
                return {
                    "success": True,
                    "exe": exe,
                    "project": resolved,
                    "reused_existing": True,
                    "new_instance": False,
                    "pid": proc["pid"],
                    "running_instances": [p["pid"] for p in procs],
                    "message": f"该工程已在 LASPCEM(PID {proc['pid']}) 中打开，未重复启动",
                }

    try:
        child = subprocess.Popen([exe, resolved], cwd=str(Path(exe).parent),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return error_response("LAUNCH_FAILED", f"无法启动 laspcem.exe: {exc}")

    existing = ", ".join(str(p["pid"]) for p in procs) or "无"
    return {
        "success": True,
        "exe": exe,
        "project": resolved,
        "reused_existing": False,
        "new_instance": True,
        "pid": child.pid,
        "running_instances": [p["pid"] for p in procs] + [child.pid],
        "message": (f"LASPCEM 无进程间打开接口，已新起实例 PID {child.pid} 打开 {resolved}"
                    f"（此前已有实例：{existing}）"),
    }


@mcp.tool()
def close_laspcem_project(project_path: str, force: bool = False,
                          include_script_sessions: bool = False) -> dict[str, Any]:
    """关闭在 LASPCEM 中打开某个 .sem 的 GUI 实例（等价于关窗口）。

    LASPCEM 没有进程间 IPC，只能按「谁的 cmdline 里带这个工程」定位实例。
    与 `export_laspcem_snp` / `build_laspcem_report` 的关系：工程被 GUI 打开期间它们一律回
    `PROJECT_IN_USE`，关掉之后立刻可用（不必等到进程自然退出）。

    用法："把这个工程的 LASPCEM 窗口关掉"、"关闭 CPW_SL.sem 的实例"

    Args:
        project_path: .sem 工程文件绝对路径。**不要求文件仍然存在** —— 工程被删/改名后
            LASPCEM 实例的 cmdline 仍可能指向原路径，这种残留更要能关掉。
        force: True 时 terminate 超时后再 kill；默认只 terminate 并等 5 s。
        include_script_sessions: True 时把 `laspcem.exe /session <脚本>` 起的实例也算进来。
            这类实例 cmdline 里只有脚本路径、没有 .sem（服务端求解/导出就是这么起的），
            按工程路径匹配不到，2026-11-26 实测只能强杀。默认 False = 不动它们（避免误关
            别人手工开的窗口）。

    Returns:
        {"success": True, "project": "<绝对路径>", "closed": [pid, ...],
         "remaining": [pid, ...], "message": "..."}
        `closed` 为空 = 没有实例在打开该工程（不算失败）。
    """
    raw = (project_path or "").strip()
    if not raw:
        return error_response("INVALID_PATH", "project_path 不能为空")
    if Path(raw).suffix.lower() != ".sem":
        return error_response("INVALID_PATH", f"文件扩展名必须是 ('.sem',): {raw}")
    # 这里**不**调 validate_file：那个会校验文件存在，而"关掉一个文件已被删除的工程"
    # 恰恰是需要的场景（2026-11-13 复核）。匹配仍走 _same_path（不依赖文件存在）。
    try:
        resolved = os.path.abspath(raw)
    except OSError:
        resolved = raw

    closed: list[int] = []
    failed: list[dict[str, Any]] = []
    matched_scripts: list[int] = []
    for proc in find_laspcem_processes():
        proj = proc.get("project") or ""
        matched = bool(proj) and _same_path(proj, resolved)
        if not matched and include_script_sessions and not proj:
            if "/session" in (proc.get("cmdline") or "").lower():
                matched = True
                matched_scripts.append(int(proc.get("pid")))
        if not matched:
            continue
        pid = proc.get("pid")
        try:
            p = psutil.Process(int(pid))
            p.terminate()
            try:
                p.wait(timeout=5)
            except psutil.TimeoutExpired:
                if not force:
                    failed.append({"pid": pid, "reason": "terminate 超时（需要时传 force=True 强杀）"})
                    continue
                p.kill()
                p.wait(timeout=5)
            closed.append(int(pid))
        except (psutil.Error, OSError) as exc:
            failed.append({"pid": pid, "reason": str(exc)[:60]})

    remaining = [p.get("pid") for p in find_laspcem_processes()
                 if p.get("project") and _same_path(p["project"], resolved)]
    resp: dict[str, Any] = {
        "success": True,
        "project": resolved,
        "closed": closed,
        "remaining": remaining,
        "message": (f"已关闭 {len(closed)} 个打开该工程的实例（PID {closed}）" if closed
                    else "没有实例在打开该工程（可能已关闭）"),
    }
    if failed:
        resp["details"] = {"failed": failed}
    return resp


def _run_script(exe: str, script_text: str, timeout_seconds: int, background: bool = False) -> tuple[str, str | None, int | None]:
    """写脚本到临时文件并执行，阻塞到完成。

    求解用 `/session`（阻塞、会起 GUI），`/background /session` 是非阻塞的、跑完拿不到结果。
    返回 (脚本路径, 错误, 退出码)。错误为 "timeout"（超时被强杀）或 OSError 文本；None = 正常退出。
    ⚠️ 退出码 0 **不代表**脚本里的 API 成功：/session 只是把脚本喂给 GUI 的 Python 宿主，
    API 抛的异常不会反映到进程退出码（实测 FEMAnalyze 被静默跳过时也是 rc=0）。
    """
    fd, tmp = tempfile.mkstemp(prefix="laspcem_", suffix=".py")
    os.close(fd)
    Path(tmp).write_text(script_text, encoding="utf-8")
    args = [exe, "/background", "/session", tmp] if background else [exe, "/session", tmp]
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(Path(exe).parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return tmp, f"无法启动 laspcem.exe: {exc}", None
    try:
        rc = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        return tmp, "timeout", None
    return tmp, None, rc


def _new_solutions(before: list[str], after: list[str]) -> list[str]:
    """求解后新增的解序号（= 真正产生了新解）。"""
    return [n for n in after if n not in before]


def _read_script_log(path: str, limit: int = 4000) -> str:
    """读脚本日志（宿主不回传 stdout，脚本内写的 BEGIN/OK/traceback 只能这样拿）。

    只取尾部 limit 字符：正常日志很短，出错时 traceback 在末尾。
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _solve_hint(setup: str, script_log: str, sem_path: str) -> str:
    """按工具侧外部日志给失败原因（求解脚本本身不写日志，见 build_solve_script）。"""
    log = script_log or ""
    if "timed_out=True" in log:
        return ("等待超时：求解器进程还在跑或一直没退出。看 scheduler 负载，或加大 timeout_seconds；"
                f"本次 setup={setup}。")
    if "saw_solver=False" in log:
        return ("脚本执行了但整个等待期都没出现 mpiexec/phofem ⇒ 求解没被触发。"
                f"重点查 setup 名（本次用 {setup}，.sem 里的候选 "
                f"{detect_setup_names(sem_path)[:3]}，传给 API 必须用显示名去掉括号）、"
                "工程是否需要先在 GUI 里 Check/Redo Mesh，以及 GUI 的 Message 面板有没有弹窗报错。")
    return f"见 script_log_tail（脚本路径与进程信息都在里面）；setup={setup}。"


def _spawn_script(exe: str, script_text: str) -> tuple[subprocess.Popen | None, str, str | None]:
    """把脚本写到临时文件并**非阻塞**启动（求解是异步的，必须由调用方去等）。"""
    fd, tmp = tempfile.mkstemp(prefix="laspcem_", suffix=".py")
    os.close(fd)
    Path(tmp).write_text(script_text, encoding="utf-8")
    try:
        # ⚠️ 实测注意：
        # 1) 不要重定向 stdout/stderr 到 DEVNULL（`cem.FEMAnalyze` 会不拉起求解器）；
        # 2) 从没有控制台的进程（MCP 服务/uvicorn）里 spawn 时，给一个独立控制台更稳
        #    （不加时出现过脚本不生效的情况；单独复现未完全定论，故保留此 flag）。
        proc = subprocess.Popen(
            [exe, "/session", tmp],
            cwd=str(Path(exe).parent),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except OSError as exc:
        return None, tmp, f"无法启动 laspcem.exe: {exc}"
    return proc, tmp, None


def _solver_processes() -> list[dict[str, Any]]:
    """当前在跑的求解器进程（mpiexec / phofem）——「真求解」的硬证据。"""
    out: list[dict[str, Any]] = []
    try:
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            name = (p.info.get("name") or "").lower()
            if name in ("mpiexec.exe", "phofem.exe"):
                out.append({"pid": p.info.get("pid"), "name": p.info.get("name"),
                            "cmdline": " ".join(p.info.get("cmdline") or [])[:200]})
    except (psutil.AccessDenied, OSError):
        pass
    return out


#: 异步任务上下文（线程局部）：MCP 工具签名里不能出现 threading.Event（FastMCP 生成不了 JSON Schema），
#: 所以取消信号/进程句柄走这里，由 async_solve 在调用前 set、调用后 clear。
_TASK_CTX = threading.local()


def set_task_context(cancel_event: threading.Event | None = None,
                     handle: dict[str, Any] | None = None) -> None:
    """设置当前线程的任务上下文（供异步队列调用内部实现时使用）。"""
    _TASK_CTX.cancel_event = cancel_event
    _TASK_CTX.handle = handle


def clear_task_context() -> None:
    """清空当前线程的任务上下文。"""
    _TASK_CTX.cancel_event = None
    _TASK_CTX.handle = None


def _ctx_event() -> threading.Event | None:
    return getattr(_TASK_CTX, "cancel_event", None)


def _ctx_handle() -> dict[str, Any] | None:
    return getattr(_TASK_CTX, "handle", None)


def _solver_cpu_total(solvers: list[dict[str, Any]]) -> float:
    """求解器进程的累计 CPU 时间（user+system，秒）。CPU 在涨 = 还在算，即便日志不写。"""
    total = 0.0
    for rec in solvers or []:
        try:
            t = psutil.Process(rec["pid"]).cpu_times()
            total += float(t.user) + float(t.system)
        except (psutil.Error, OSError, KeyError):
            continue
    return total


def solve_progress(sem_path: str, since: float = 0.0) -> dict[str, Any]:
    """求解进度快照（供异步查询展示）：自适应轮次 + Prog.log 末行 + 日志总量。

    只读文件，不碰 GUI；工程未开始求解时返回空字段。

    `since`：只统计 mtime 不早于该时间戳的日志。传任务 `started_at` 可避免读到**上一个任务**
    留在同一工程目录里的 `Hofem_adapt_itr*.log` / `Prog.log` —— 2026-11-13 实测新任务刚提交时
    出现过 `adapt_iterations=0` 却 `last_log_line="Iter3: FEM 2D solver finished"` 的自相矛盾。
    """
    base = Path(sem_path).with_suffix("")
    iters = 0
    log_bytes = 0
    last_line = ""

    def _fresh(path: Path) -> bool:
        if not since:
            return True
        try:
            return path.stat().st_mtime >= since
        except OSError:
            return False

    try:
        for f in base.rglob("*"):
            if not f.is_file() or not _fresh(f):
                continue
            name = f.name.lower()
            if name.startswith("hofem_adapt_itr"):
                iters += 1
            if name.endswith(".log"):
                try:
                    log_bytes += f.stat().st_size
                except OSError:
                    continue
        prog = base / "1_Result" / "Prog.log"
        if prog.is_file() and _fresh(prog):
            text = prog.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            last_line = text[-1].strip() if text else ""
    except OSError:
        pass
    # MaxNumberOfPass 只能当参照：adapt_iterations 统计的是 Hofem_adapt_itr*.log 份数，
    # 实测会超过该值（同一 pass 会产出多份日志，2026-11-26 观测 12 份 vs MaxNumberOfPass=6），
    # 所以不给百分比，只把上限一并回传，避免把假 ETA 当精确进度。
    max_passes = None
    if iters:   # 求解还没开始就不必读 6 MB 的 .sem（轮询是高频调用，别白花 IO）
        try:
            from servers.laspcem.setup_reader import read_setup_report
            max_passes = (read_setup_report(str(Path(sem_path))).get("setup") or {}).get("MaxNumberOfPass")
        except Exception:  # noqa: BLE001 — 进度展示失败不影响求解
            max_passes = None
    return {
        "adapt_iterations": iters,
        "log_bytes": log_bytes,
        "last_log_line": last_line[:200],
        "max_passes": max_passes,
        "progress_note": ("adapt_iterations 是 Hofem_adapt_itr*.log 计数，实测会超过 MaxNumberOfPass"
                          "（同一 pass 可产出多份），只能当粗略进度"),
    }


def _running_solve_task_for(sem_path: str) -> str | None:
    """该工程是否已有排队/运行中的求解任务（延迟导入 async_solve，避免循环依赖）。"""
    try:
        from servers.laspcem import async_solve as _a

        for task in _a._runner.list():
            meta = task.get("metadata") or {}
            if task.get("finished_at") is None and _same_project(meta.get("sem_path", ""), sem_path):
                return task.get("task_id")
    except Exception:      # noqa: BLE001 —— 只是提示信息，任何异常都不该影响主流程
        return None
    return None


def _progress_sig(sem_path: str) -> tuple[int, tuple[int, float] | None]:
    """求解进度指纹：结果目录里所有 `*.log` 的总大小 + `.sem` 指纹。

    卡死检测用（实测 BGA 工程曾停在 `Iter3: Solving 1 out of 1 frequencies`，
    `Prog.log` 不再增长、`phofem` CPU≈0，但进程还在 —— 只能靠"日志不前进"发现）。
    """
    base = Path(sem_path).with_suffix("")
    total = 0
    try:
        for f in base.rglob("*.log"):
            try:
                total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return (total, _file_sig(sem_path))


def _kill_solver_procs(sem_path: str, pids: list[int] | None = None) -> list[int]:
    """杀掉属于该工程的求解器进程（mpiexec / phofem / hydra_pmi_proxy），返回被处理的 pid。

    pids 给出时按 pid **精确**杀（超时路径传 `wait["solver_processes"]` 的实际 pid，最可靠）；
    未给出时按工程 stem 模糊匹配 cmdline 兜底 —— 不同目录的同名工程会互相误伤。
    """
    killed: list[int] = []
    if pids:
        for pid in pids:
            try:
                psutil.Process(pid).terminate()
                killed.append(pid)
            except (psutil.Error, OSError):
                continue
        return killed
    stem = Path(sem_path).stem.lower()
    if not stem:
        return killed
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        name = (proc.info.get("name") or "").lower()
        if name not in ("mpiexec.exe", "phofem.exe", "hydra_pmi_proxy.exe"):
            continue
        cmd = " ".join(proc.info.get("cmdline") or []).replace("\\", "/").lower()
        if stem not in cmd:
            continue
        try:
            psutil.Process(proc.info["pid"]).terminate()
            killed.append(proc.info["pid"])
        except (psutil.Error, OSError):
            continue
    return killed


def _kill_orphan_mpi_since(since: float) -> list[int]:
    """补杀 LASPCEM 的 MPI 代理 `hydra_pmi_proxy.exe`，返回被处理的 pid。

    实测（2026-11-12 cancel / 2026-11-13 超时现场）：mpiexec 被杀后它会残留，
    cmdline 里**没有工程路径**（只有 `--usize -1 --auto-cleanup 1`），按工程名和 pid
    都够不到，会一直占着结果目录句柄（删目录报 "Device or resource busy"）。
    这里按「exe 在 LASPCEM 目录下 + 启动时间不早于 since」筛，避免误伤其它 MPI 作业。
    """
    killed: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            if (proc.info.get("name") or "").lower() != "hydra_pmi_proxy.exe":
                continue
            exe = (proc.info.get("exe") or "").lower().replace("\\", "/")
            if "laspcem" not in exe:
                continue
            if (proc.info.get("create_time") or 0) < since - 5:
                continue
            try:
                psutil.Process(proc.info["pid"]).terminate()
                killed.append(proc.info["pid"])
            except (psutil.Error, OSError):
                continue
    except (psutil.Error, OSError):
        pass
    return killed


def _wait_for_solve(sem_path: str, timeout_seconds: int, poll: float = 2.0,
                    quiet_seconds: float = 15.0,
                    no_solver_grace: float = 120.0,
                    no_change_seconds: float = 60.0,
                    cancel_event: threading.Event | None = None,
                    stall_seconds: float = 900.0,
                    proc: Any = None) -> dict[str, Any]:
    """等异步求解结束：见过 mpiexec/phofem → 求解器退出且**安静 quiet_seconds 秒**才算完。

    实测（2026-11-12）：自适应求解有**多轮**（每轮一个 mpiexec + 2×phofem），轮次之间求解器会
    短暂退出。只判「求解器消失」会早停（实测 35 s 就误判完成，把还在跑的求解器搞成孤儿进程），
    所以要求「没有求解器 + .sem 不再变化」持续 quiet_seconds 秒。
    """
    t0 = time.time()
    saw_solver = False
    seen: list[dict[str, Any]] = []
    sig = _file_sig(sem_path)
    last_activity = time.time()
    elapsed0 = time.time()
    prog = _progress_sig(sem_path)
    last_progress = time.time()      # 最近一次"确实有进展"（日志增长 / sem 变化 / 求解器变动 / CPU 在涨）
    last_cpu = 0.0
    while True:
        if cancel_event is not None and cancel_event.is_set():
            # 被取消：立刻返回，调用方负责关 GUI 且**不做补保存**（否则会再起一个实例）
            return {"saw_solver": saw_solver, "timed_out": False, "cancelled": True,
                    "solver_seconds": round(time.time() - t0, 1),
                    "solver_processes": seen, "sem_changed": False}
        # GUI 自己退出且从未见到求解器 ⇒ 不可能是"正在建网格"，立刻返回。
        # 不检查的话，未求解工程会白等满宽限期（实测 BPF.sem 在 app.Open 就崩/挂，
        # 旧的 grace=timeout+1 会等 30 分钟才报错）—— 2026-11-13 审查。
        if proc is not None and not saw_solver and proc.poll() is not None:
            return {"saw_solver": False, "timed_out": False, "reason": "gui_exited",
                    "gui_exit_code": proc.returncode,
                    "solver_seconds": round(time.time() - t0, 1),
                    "solver_processes": [], "sem_changed": False}
        solvers = _solver_processes()
        now_sig = _file_sig(sem_path)
        now_prog = _progress_sig(sem_path)
        if now_prog != prog:
            prog = now_prog
            last_progress = time.time()
        if solvers:
            cpu = _solver_cpu_total(solvers)
            if cpu > last_cpu + 1.0:            # CPU 时间在涨 ⇒ 确实在算（插值扫频阶段不写日志）
                last_cpu = cpu
                last_progress = time.time()
            if not seen:
                last_progress = time.time()     # 求解器刚起来，算一次进展
            saw_solver = True
            seen = solvers
            last_activity = time.time()
        if now_sig != sig:
            sig = now_sig
            last_activity = time.time()
        quiet = time.time() - last_activity
        if saw_solver and not solvers and quiet >= quiet_seconds:
            return {"saw_solver": True, "timed_out": False,
                    "solver_seconds": round(time.time() - t0 - quiet, 1),
                    "solver_processes": seen, "sem_changed": sig is not None}
        # 一直没见到求解器：不要死等到 timeout（实测错 setup 名会白等 30 分钟），
        # 给一个宽限期就返回，让调用方按「未触发求解」报错。
        if not saw_solver and (time.time() - elapsed0) > no_solver_grace and quiet >= no_change_seconds:
            return {"saw_solver": False, "timed_out": False, "reason": "no_solver",
                    "solver_seconds": round(time.time() - t0, 1),
                    "solver_processes": [], "sem_changed": False}
        # 卡死：求解器还在，但日志/sem 长时间毫无进展（实测 BGA 停 7 分钟无输出）
        if (stall_seconds and saw_solver and solvers
                and (time.time() - last_progress) > stall_seconds):
            return {"saw_solver": True, "timed_out": False, "stalled": True,
                    "stalled_seconds": round(time.time() - last_progress, 1),
                    "solver_seconds": round(time.time() - t0, 1),
                    "solver_processes": seen, "sem_changed": False}
        if time.time() - t0 > timeout_seconds:
            return {"saw_solver": saw_solver, "timed_out": True,
                    "solver_seconds": round(time.time() - t0, 1),
                    "solver_processes": seen, "sem_changed": False}
        time.sleep(poll)


def _file_sig(path: str) -> tuple[int, float] | None:
    """文件指纹（大小 + mtime），用来判断工程是否还在被写。"""
    try:
        st = Path(path).stat()
        return (st.st_size, st.st_mtime)
    except OSError:
        return None


def _terminate(proc: subprocess.Popen | None, wait_seconds: int = 10) -> None:
    """关掉本次拉起的 GUI（结果已在文件里；GUI 常常 app.Exit() 后仍不退出）。"""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()


def _lock_path(sem_path: str) -> Path:
    """LASPCEM 的锁文件：与 `.sem` 同目录的 `~$<stem>.lock`（GUI 打开工程时生成）。"""
    p = Path(sem_path)
    return p.parent / f"~${p.stem}.lock"


def _clear_stale_lock(sem_path: str) -> str | None:
    """清理残留锁文件：`~$xxx.lock` 存在且**没有实例正持有该工程**时删掉。

    实测：GUI 被 terminate（或崩溃）后锁文件会留在磁盘上。启动前/结束后各清一次，
    避免下次打开工程时被误判为"已被占用"。
    """
    lock = _lock_path(sem_path)
    try:
        if not lock.exists():
            return None
    except OSError:
        return None
    if _project_in_use(sem_path):      # 有实例正开着 ⇒ 锁可能是活的，不动
        return None
    try:
        lock.unlink()
        return str(lock)
    except OSError:
        return None


def _persist_solution(sem_path: str, exe: str, timeout_seconds: int = 180) -> dict[str, Any]:
    """求解后补一次 `Open → SaveAs`，把 `1_Result` 里的结果合并回顶层 `.sem`。

    实测：只跑 Analyze 时结果留在 `<stem>/1_Result/` 的工作副本，顶层 `.sem` 不变
    （`is_solved=False`）；补一次 SaveAs 后 `is_solved` 才为真，后续导出才有数据。
    """
    sig0 = _file_sig(sem_path)
    log = default_log_path("save")
    proc, script_path, err = _spawn_script(exe, build_save_script(sem_path))
    if err:
        return {"saved": False, "error": err, "script_log": log}

    t0 = time.time()
    saved = False
    try:
        while time.time() - t0 < timeout_seconds:
            if is_solved(sem_path):
                saved = True
                break
            sig = _file_sig(sem_path)
            if sig is not None and sig != sig0:
                saved = True
                break
            if proc.poll() is not None:
                break
            time.sleep(1)
    finally:
        _terminate(proc)
        _clear_stale_lock(sem_path)
    elapsed = round(time.time() - t0, 1)
    rc = proc.returncode if proc is not None else None
    Path(log).write_text("\n".join([
        f"exe={exe}",
        f"script={script_path}",
        f"saved={saved}",
        f"is_solved={is_solved(sem_path)}",
        f"sem_size={Path(sem_path).stat().st_size if Path(sem_path).exists() else 0}",
        f"elapsed={elapsed}",
        f"exit_code={rc}",
    ]) + "\n", encoding="utf-8")
    return {"saved": saved, "is_solved": is_solved(sem_path), "elapsed_seconds": elapsed,
            "script_path": script_path, "script_log": log,
            "script_log_tail": _read_script_log(log)}


def _check_explicit_setup(sem_path: str, setup_name: str) -> dict[str, Any] | None:
    """调用方**显式**传 setup 名时，先跟 `.sem` 里探测到的名字比对。

    实测痛点（2026-11-12）：名字写错时 `cem.FEMAnalyze` 静默空转，工具只能报
    SOLVE_NOT_TRIGGERED —— 与"结果已最新被跳过"完全无法区分（同样无 mpiexec、`.sem` 不变）。
    在这里先拦掉，把"名字写错"变成调用前的明确错误 `SETUP_NOT_FOUND`。
    未显式传（空串）或 `.sem` 里探测不到名字时不阻断，交给运行期。
    """
    want = setup_name.strip()
    if not want:
        return None
    detected = detect_setup_names(sem_path)
    if not detected:
        return None
    pool: set[str] = set()
    for name in detected:
        pool.add(name.lower())
        pool.add(split_setup_name(name)[0].lower())
    if want.lower() in pool:
        return None
    return error_response(
        "SETUP_NOT_FOUND",
        f"setup {want!r} 不在工程里（.sem 里探测到：{detected[:5]}）",
        hint=("不传 setup_name 会按算法自动探测；显式传时用候选里的名字即可"
              "（带不带 (FEM) 后缀都接受）。"),
        setup_candidates=detected[:5],
        setup_name=want,
    )


def _results_present(sem_path: str) -> bool:
    """工程目录下是否真的有求解结果（`*Result*` 里的 `Design_*.log` / `_Solver*.log`）。

    实测目录结构不固定：`<stem>/1_Result/_SolverPip_0.log` 与
    `<stem>/1_Result/1_Result/Design_FEM.log` 并存，求解期还会出现 `2_Result_cur`。
    所以这里在 `<stem>` 下按 `*Result*` 目录做**两层**扫描，避免误判"结果缺失"。
    """
    base = Path(sem_path).with_suffix("")

    def _hit(d: Path) -> bool:
        try:
            for f in d.iterdir():
                if f.is_file() and (f.name.startswith("Design_") or f.name.startswith("_Solver")):
                    return True
        except OSError:
            return False
        return False

    dirs: list[Path] = []
    for cand in (base, *(() if not base.is_dir() else base.iterdir())):
        try:
            if cand.is_dir() and ("Result" in cand.name or cand == base):
                dirs.append(cand)
        except OSError:
            continue
    for d in dirs:
        if _hit(d):
            return True
        try:
            for sub in d.iterdir():
                if sub.is_dir() and _hit(sub):
                    return True
        except OSError:
            continue
    return False


def _solve_log_verdict(sem_path: str) -> tuple[bool | None, str]:
    """读最新的 `Design_*.log`，判断求解是否正常结束（含 `successfully finished`）。

    返回 (verdict, log_path)：True=正常结束，False=跑完但没成功，None=找不到日志/读不了。
    """
    base = Path(sem_path).with_suffix("")
    logs: list[Path] = []
    if base.is_dir():
        try:
            for d in base.iterdir():
                if not d.is_dir() or "Result" not in d.name:
                    continue
                logs.extend(d.rglob("Design_*.log"))
        except OSError:
            logs = []
    if not logs:
        return None, ""
    try:
        newest = max(logs, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None, ""
    try:
        text = newest.read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return None, str(newest)
    return bool(re.search(r"successfully finished", text, re.IGNORECASE)), str(newest)


def _snp_data_rows(path: str) -> int:
    """数 Touchstone 里的数据行（至少 3 列数值），用来识别"只有表头"的空导出。"""
    rows = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s or s[0] in "!#":
                    continue
                parts = s.replace(",", " ").split()
                if len(parts) < 3:
                    continue
                try:
                    for v in parts[:3]:
                        float(v)
                except ValueError:
                    continue
                rows += 1
    except OSError:
        return 0
    return rows


def _registry_busy_response(held: dict[str, Any], action: str) -> dict[str, Any]:
    """后台任务正占用该工程时回 PROJECT_IN_USE（复用既有错误码，不新增词典项）。"""
    kind = held.get("kind") or "后台任务"
    task_id = _running_solve_task_for(held.get("sem_path") or "")
    return error_response(
        "PROJECT_IN_USE",
        f"该工程正被后台任务占用（{kind}），无法{action}",
        hint=("求解与导出/建报告不能同时动同一个 .sem（会互相踩坏结果）。"
              "等任务跑完再调本工具，或先 laspcem_solve_query 看进度 / laspcem_task_cancel 取消；"
              "也可以提交时直接用 then_export / then_report 让服务端串起来。"),
        holder=held,
        running_solve_task=task_id,
    )


def _same_project(a: str, b: str) -> bool:
    """两个 .sem 路径是否指向同一工程（大小写/斜杠/相对路径统一归一化后比较）。

    占用检测与「该工程是否已有求解任务」必须用同一把尺子：一边精确比字符串、
    另一边归一化，路径写法不同就会漏判（2026-11-13 审查）。
    """
    if not a or not b:
        return False
    try:
        return str(Path(a).resolve()).lower() == str(Path(b).resolve()).lower()
    except OSError:
        return a.replace("\\", "/").lower() == b.replace("\\", "/").lower()


def _project_in_use(sem_path: str) -> list[dict[str, Any]]:
    """是否有 LASPCEM 实例已经打开了这个工程（避免两个实例抢同一 .sem）。"""
    held: list[dict[str, Any]] = []
    try:
        for proc in find_laspcem_processes():
            proj = proc.get("project") or ""
            if not proj:
                continue
            if _same_project(proj, sem_path):
                held.append(proc)
    except Exception:  # noqa: BLE001 — 进程枚举失败不该阻断主流程
        return []
    return held


def _busy_response(held: list[dict[str, Any]], action: str) -> dict[str, Any]:
    pids = ", ".join(str(p.get("pid")) for p in held)
    return error_response(
        "PROJECT_IN_USE",
        f"工程已被 LASPCEM 实例占用（PID {pids}），无法{action}",
        hint=("LASPCEM 没有单实例转发，同时开两个实例操作同一个 .sem 会互相踩。"
              "先调 close_laspcem_project(project_path) 关掉占用它的那个实例"
              "（按命令行匹配、发 terminate 并等 5 s；closed=[] 表示本来没有实例开着，不算失败），"
              "或直接在 GUI 里关掉窗口，再重试。"),
        pids=[p.get("pid") for p in held],
    )


def _script_log_failed(log_path: str) -> bool:
    """脚本日志里已有失败痕迹（traceback）⇒ 不必再等 GUI 退出或等满 timeout。

    `render_script` 会写 `_w('BEGIN …')` / `_w('OK …')`，异常时写 traceback。
    2026-11-13：去掉 `app.Exit()` 后 GUI 不再自己退出，等待循环失去 `proc.poll()`
    这个提前结束信号 ⇒ 失败路径会白等满 timeout（实测 report 撞无效工程 600 s，
    并把单 worker 占死，后续异步任务全部排队）。
    """
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "Traceback (most recent call last)" in text


def _wait_for_artifact(out: str, proc: subprocess.Popen | None, timeout_seconds: int,
                       before_sig: tuple[int, float] | None = None,
                       poll: float = 1.0, log_path: str = "") -> str | None:
    """等导出产物**真的被本次导出改写**（不能把上一次遗留的同名文件当成功）。

    实测坑：目录里已有 `x.s2p` 时，直接 `_resolve_snp_output()` 会立刻返回旧文件 ⇒
    错解名也能 0.1 s 报 success（假成功）。所以要求文件指纹（大小+mtime）与导出前不同。
    也不能等进程退出：ExportSnpFile 完成后 GUI 常常很久不退。
    """
    t0 = time.time()
    while True:
        actual = _resolve_snp_output(out)
        if actual:
            sig = _file_sig(actual)
            if sig is not None and sig != before_sig and sig[0] > 0:
                return actual
        if proc is not None and proc.poll() is not None:
            time.sleep(2)
            actual = _resolve_snp_output(out)
            sig = _file_sig(actual) if actual else None
            return actual if sig is not None and sig != before_sig and sig[0] > 0 else None
        if log_path and _script_log_failed(log_path):
            # 脚本已抛异常 ⇒ 不会再有产物，立刻收手（别再等满 timeout）
            return None
        if time.time() - t0 > timeout_seconds:
            return None
        time.sleep(poll)


# ⚠️ 不是 MCP 工具：求解只对外提供异步版（run_laspcem_solve_async，见 async_solve.py）。
# 本函数保留为**内部实现** —— 异步队列、export_laspcem_snp、build_laspcem_report 都复用它。
def run_laspcem_solve(
    sem_path: str,
    setup_name: str = "",
    timeout_seconds: int = 7200,
    close_gui: bool = True,
    cancel_event: threading.Event | None = None,
    handle: dict[str, Any] | None = None,
    stall_seconds: float = 900.0,
) -> dict[str, Any]:
    """对 .sem 工程跑 FEM 求解（打开工程 → cem.FEMAnalyze → 等求解器跑完 → 关 GUI）。

    实测机制（2026-11-12 录制 + 真跑）：`cem.FEMAnalyze` 是**异步**的，脚本结束后 GUI 继续跑
    mpiexec + phofem（约 2 分钟），跑完 GUI 自己把结果写回 .sem。所以本工具按「**是否真的
    出现了求解器进程**」判成败，而不是看解序号（同一 setup 重解是覆盖，序号不变）。

    用法：用 LASPCEM 求解这个工程；对 C:/demo.sem 跑一次 FEM

    Args:
        sem_path: .sem 工程文件绝对路径。
        setup_name: setup 名；默认从 .sem 自动探测（存的是 Setup1(FEM)，传给 API 时用显示名 Setup1）。
        timeout_seconds: 最长等待秒数，默认 7200（2 h）。
        close_gui: 求解结束后是否关掉本次拉起的 GUI 实例（结果已写回工程，默认 True）。
    Returns:
        {"success": True, "sem_path": "...", "setup_name": "Setup1(FEM)", "solved": True,
         "saw_solver": True, "solver_seconds": 128.4, "solver_processes": [...],
         "solutions_before": [...], "solutions_after": [...], "sem_size_before": int,
         "sem_size_after": int, "script_log": "...", "script_log_tail": "..."}
        没有出现求解器 ⇒ success=False + error_code="SOLVE_NOT_TRIGGERED"（工程已有解时
        skipped_likely=True，说明大概率被 LASPCEM 判定"结果已最新"而跳过）；
        求解器跑完但 Design_*.log 里没有 "successfully finished" ⇒ "SOLVE_INCOMPLETE"。
        显式传入的 setup_name 在 .sem 里找不到 ⇒ "SETUP_NOT_FOUND"（调用前就拦，不启动 LASPCEM）。
        求解后顶层 .sem 仍无解记录（补了 SaveAs 也没有）⇒ "SOLVE_NOT_SAVED"；
        求解器还在但 15 分钟内日志/工程毫无进展 ⇒ "SOLVE_STALLED"（会先结束求解器）。
    """
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe, err = _resolve_exe("")
    if err:
        return err

    bad_setup = _check_explicit_setup(resolved, setup_name)
    if bad_setup:
        return bad_setup
    setup = _resolve_setup_name(resolved, setup_name)
    held = _project_in_use(resolved)
    if held:
        return _busy_response(held, "求解")
    locks_removed = [p for p in [_clear_stale_lock(resolved)] if p]   # 开跑前清残留锁
    was_solved = is_solved(resolved)
    before = detect_solution_names(resolved)
    size_before = Path(resolved).stat().st_size
    results_dir_before = _dir_stat(_results_dir(resolved))
    log = default_log_path("solve")

    proc, script_path, spawn_err = _spawn_script(
        exe, build_solve_script(resolved, setup_name=setup))
    if spawn_err:
        return error_response("SOLVE_FAILED", spawn_err)
    handle = handle if handle is not None else _ctx_handle()
    if handle is not None:
        # 把 GUI 句柄/脚本路径交给调用方（异步取消要靠它精确杀进程：
        # `/session <脚本>` 的 cmdline 里没有 .sem 路径，按工程名找不到）
        handle["proc"] = proc
        handle["script_path"] = script_path

    # 未求解工程首次 Analyze 要先建网格，mpiexec 可能 120 s 后才出现，且这段时间 .sem 不变，
    # 所以不能用「无求解器就早退」的窄宽限。但也不能像以前那样给 timeout+1（=30 分钟）：
    # 工程打不开/脚本崩退时调用方会白等半小时（2026-11-13 实测 BPF.sem）。
    # 折中：上限 10 分钟，并且 GUI 进程退出时立即返回（_wait_for_solve 里的 proc 检测）。
    grace = 120.0 if was_solved else min(float(timeout_seconds), 600.0)
    try:
        wait = _wait_for_solve(resolved, timeout_seconds, no_solver_grace=grace,
                               cancel_event=cancel_event or _ctx_event(),
                               stall_seconds=stall_seconds, proc=proc)
    finally:
        if close_gui:
            _terminate(proc)
        after_lock = _clear_stale_lock(resolved)                      # 结束后再清一次
        if after_lock:
            locks_removed.append(after_lock)

    after = detect_solution_names(resolved)
    size_after = Path(resolved).stat().st_size
    # 超时/卡死时必须**先**把求解器（含 MPI 代理）清掉，再考虑补保存：
    # 否则会一边有求解器在写结果、一边起新 GUI 去 SaveAs 同一个工程。
    # 2026-11-13 实测 1800 s 超时现场：persist.saved=true 且 phofem/mpiexec 仍在跑，
    # 杀完还留下 hydra_pmi_proxy 占着结果目录。
    solver_pids = [p.get("pid") for p in wait["solver_processes"] if p.get("pid")]
    killed_solver_pids: list[int] = []
    if wait.get("timed_out") or wait.get("stalled"):
        killed_solver_pids = _kill_solver_procs(resolved, pids=solver_pids)
        killed_solver_pids += _kill_orphan_mpi_since(
            time.time() - float(wait.get("solver_seconds") or 0.0) - 120)
        time.sleep(1.0)                     # 让 OS 释放文件句柄，再决定要不要 SaveAs
    # 结果常常只落在 <stem>/1_Result 的工作副本里 ⇒ 补一次 Open+SaveAs 合并回顶层 .sem
    persist = None
    if wait["saw_solver"] and not wait.get("cancelled") and not is_solved(resolved):
        persist = _persist_solution(resolved, exe)
        after = detect_solution_names(resolved)
        size_after = Path(resolved).stat().st_size
    # 求解脚本本身不写日志（包装会抑制异步求解），这里把关键事实落到工具侧日志
    Path(log).write_text("\n".join([
        f"exe={exe}",
        f"script={script_path}",
        f"setup={setup}",
        f"saw_solver={wait['saw_solver']}",
        f"timed_out={wait['timed_out']}",
        f"solver_seconds={wait['solver_seconds']}",
        f"sem_size_before={size_before}",
        f"sem_size_after={size_after}",
        f"solutions_before={before}",
        f"solutions_after={after}",
        *[f"solver_pid={p.get('pid')} {p.get('name')} {p.get('cmdline', '')}"
          for p in wait["solver_processes"]],
    ]) + "\n", encoding="utf-8")
    script_log = _read_script_log(log)
    orphans = _solver_processes()          # 关 GUI 后不该还有求解器；有就是孤儿进程
    finished, solve_log_path = _solve_log_verdict(resolved)
    evidence = {
        "setup_name": setup,
        "was_solved": was_solved,
        "saw_solver": wait["saw_solver"],
        "solver_seconds": wait["solver_seconds"],
        "solver_processes": wait["solver_processes"],
        "solutions_before": before,
        "solutions_after": after,
        "new_solutions": _new_solutions(before, after),
        "sem_size_before": size_before,
        "sem_size_after": size_after,
        "results_dir_before": results_dir_before,
        "results_dir_after": _dir_stat(_results_dir(resolved)),
        "script_path": script_path,
        "script_log": log,
        "script_log_tail": script_log,
        "orphan_solvers": orphans,
        "solve_log": solve_log_path,
        "solve_log_finished": finished,
        "persist": persist,
        "stale_lock_removed": locks_removed,
    }

    if wait["timed_out"]:
        resp = error_response("SOLVE_TIMEOUT", f"{timeout_seconds}s 内未等到求解结束",
                              retryable=True, script_log=log, saw_solver=wait["saw_solver"],
                              killed_solver_pids=killed_solver_pids)
        resp.update(evidence)
        resp["orphan_solvers_after_kill"] = _solver_processes()
        return resp

    if wait.get("stalled"):
        # 求解器还在跑但长时间零进展：已在上面清掉求解器，这里只如实报卡死
        resp = error_response(
            "SOLVE_STALLED",
            f"求解卡住：{wait.get('stalled_seconds')}s 内日志与工程文件都没有任何进展（求解器进程仍在）",
            hint=("看 solve_log 与 <工程名>/1_Result/Prog.log 末尾；已尝试结束求解器进程。"
                  "常见原因：网格规模异常、内存不足、求解器死锁。"),
            killed_solver_pids=killed_solver_pids,
            stalled_seconds=wait.get("stalled_seconds"),
        )
        resp.update(evidence)
        return resp

    if not wait["saw_solver"]:
        # 不报成功：没见求解器就等于「没有发生求解」。工程里已有解时，大概率是 LASPCEM 认为
        # 结果已是最新而跳过（Message: already calculated）——这是正常优化，重算需让模型真的变化。
        if wait.get("reason") == "gui_exited":
            # 求解脚本刻意不包装（包装会抑制异步求解）⇒ 脚本内异常拿不到；
            # 只能靠 GUI 退出码 + 让调用方去 GUI Message 面板看（2026-11-13 实测 BPF.sem）
            resp = error_response(
                "SOLVE_NOT_TRIGGERED",
                (f"LASPCEM GUI 执行求解脚本时自行退出（exit_code={wait.get('gui_exit_code')}），"
                 "没有出现 mpiexec/phofem 求解器"),
                hint=("求解脚本刻意不做日志包装（实测包装会抑制异步求解），脚本内异常拿不到。"
                      "先用 open_laspcem_project 手动打开该工程、点 Analyze 看 Message 面板；"
                      "常见原因：工程打不开（引用的材质/文件缺失、从别处拷贝的工程）、"
                      "setup 与算法不匹配、.sem 版本过旧。"),
            )
            resp.update(evidence)
            resp["reason"] = "gui_exited"
            resp["gui_exit_code"] = wait.get("gui_exit_code")
            return resp
        hint = _solve_hint(setup, script_log, resolved)
        if before:
            hint += (f"（工程里已有解 {', '.join(before)}：也可能是 LASPCEM 认为结果已是最新而跳过；"
                     "要重算请先让模型发生变化——改变量/几何后它会自己重算）")
        resp = error_response(
            "SOLVE_NOT_TRIGGERED",
            "脚本执行完毕但没有出现 mpiexec/phofem 求解器进程，判定没有真正求解",
            hint=hint,
        )
        resp.update(evidence)
        resp["skipped_likely"] = bool(before)
        return resp

    if wait.get("cancelled"):
        # 取消优先：不再往下判 finished / 补保存，避免"取消后又起一个 GUI 去 SaveAs"
        resp = error_response(
            "SOLVE_CANCELLED",
            "求解任务被取消（已跳过补保存）",
            sem_path=resolved,
        )
        resp.update(evidence)
        return resp

    if finished is False:
        # 求解器确实跑过，但 Design_*.log 里没有 finished 标记 ⇒ 不收敛/中途失败，不能报成功
        resp = error_response(
            "SOLVE_INCOMPLETE",
            "求解器进程跑完了，但求解日志里没有 \"successfully finished\"（可能不收敛或中途失败）",
            hint="看 solve_log 末尾的报错；必要时调整网格/收敛设置后重跑。",
            solve_log=solve_log_path,
        )
        resp.update(evidence)
        return resp

    if not is_solved(resolved):
        # 求解跑过、也补了 SaveAs，但顶层 .sem 仍无解记录 ⇒ 不能报成功
        resp = error_response(
            "SOLVE_NOT_SAVED",
            "求解结束（并已补一次 SaveAs），但顶层 .sem 里仍没有解记录",
            hint=("结果可能只写在工作副本 <stem>/1_Result；看 persist.script_log 与 GUI 的 Message 面板。"),
            persist=persist,
        )
        resp.update(evidence)
        return resp

    return {
        "success": True,
        "sem_path": resolved,
        "solved": True,
        "skipped": False,
        **evidence,
        "message": (f"求解完成（setup={setup}，求解器跑了 {wait['solver_seconds']}s），"
                    f"工程 {size_before} → {size_after} 字节"),
    }


def _resolve_setup_name(sem_path: str, setup_name: str) -> str:
    """确定交给 cem.FEMAnalyze 的 setup 名：调用方给的优先，否则从 .sem 探测。

    实测：求解过的工程里 setup 存成 `Setup1(FEM)`；传裸 `Setup1` 会找不到 setup，
    `cem.FEMAnalyze` 静默空转（3s 返回、无 mpiexec/phofem、无新解）。
    """
    explicit = (setup_name or "").strip()
    return explicit or detect_setup_name(sem_path) or "Setup1"


def _resolve_solution_name(sem_path: str, setup_name: str,
                           solution_name: str) -> tuple[str, list[str]]:
    """确定 report 族 API 用的解名称：调用方给的优先，否则 = 算法_<setup 基名>。

    实测：`Setup1(FEM)` 对应的解名是 `FEM_Setup1`（s2p 头 / 图例 / GUI Solution 字段三处一致）。
    .sem 里的 `FEM_1`/`FEM_2` 是内部结果 id，只作排查候选回传，不参与取名。

    Returns:
        (解名称, .sem 里探测到的内部解候选)。
    """
    candidates = detect_solution_names(sem_path)
    explicit = (solution_name or "").strip()
    if explicit:
        return explicit, candidates
    base, algo = split_setup_name(setup_name)
    return f"{algo}_{base}", candidates


def _resolve_snp_output(requested: str) -> str | None:
    """找到 Touchstone 的实际产物：LASPCEM 按端口数命名（请求 x.sNp → 2 端口落地 x.s2p）。

    先看请求路径本身；不存在则同目录按 `<stem>.s<N>p`（不分大小写）找，多份取最新。
    找不到返回 None。
    """
    p = Path(requested)
    if p.is_file():
        return str(p)
    stem = p.with_suffix("")
    parent = stem.parent
    if not parent.is_dir():
        return None
    # ⚠️ 真机双重不匹配（2026-11-26 复现）：① LASPCEM 把请求串里的 "snp" 整段替换成 "s2p"
    # （export_laspcem_snp_<ts>.sNp → 落地 export_laspcem_s2p_<ts>.sNp），stem 变了；
    # ② 落地文件的扩展名就是 ".sNp"（N 不是端口数字），原 `\.s\d+p` 也匹配不上。
    # 两者叠加 ⇒ 永远等不到产物，白等满 export timeout、任务假 RUNNING、export_result 拿不到路径。
    frag = re.escape(stem.name).replace("snp", r"s(?:np|\d+p)")
    pat = re.compile(frag + r"\.s(?:np|\d+p)", re.IGNORECASE)
    cands = [f for f in parent.iterdir() if f.is_file() and pat.fullmatch(f.name)]
    return str(max(cands, key=lambda f: f.stat().st_mtime)) if cands else None


@mcp.tool()
def export_laspcem_snp(
    sem_path: str,
    output_path: str = "",
    setup_name: str = "",
    solution_name: str = "",
    sweeping_parameters: dict | None = None,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """导出 .sem 工程的 S 参数为 Touchstone 文件（.sNp 会按端口数落成 .s<N>p）。

    **只导已求解的工程**：没求解就立刻返回 `SOLVE_REQUIRED`（不起 GUI、不阻塞），
    提示调用方提交 `run_laspcem_solve_async(then_export=True)` —— 求解与导出由服务端串起来。
    产物按端口数命名（2 端口 → `.s2p`，请求 `.sNp` 会返回实际落地的 `.s2p` 路径）。

    ⚠️ 不做「强制重解」：LASPCEM 对模型未变化的工程会跳过重算，这是正常行为。
    需要重算就让模型真的变化（改变量/几何）。

    用法：导出这个工程的 S 参数；把 C:/demo.sem 的 S 参数导成 .sNp

    Args:
        sem_path: .sem 工程文件绝对路径。
        output_path: 输出路径（可写 .sNp）；空则落到默认产物目录（<result_dir>/export_laspcem_snp_<时间戳>.sNp）。实际文件名由端口数决定。
        setup_name: setup 名；默认从 .sem 探测（求解过的是 Setup1(FEM) 这种，带算法后缀）。
        solution_name: 解名称；默认 = 算法_<setup 基名>（如 FEM_Setup1）。
        sweeping_parameters: 扫参变量 {变量名: ['nominal',...]}，默认按工程变量取标称值。
        timeout_seconds: 导出阶段最长等待秒数，默认 600。

    Returns:
        {"success": True, "output_path": "...s2p", "requested_output_path": "...sNp",
         "exported": True, "setup_name": "Setup1(FEM)", "solution_name": "FEM_Setup1",
         "solved_first": bool, "attempts": ["export-only"], "exit_code": int, "message": "..."}
    """
    out_path_raw = (output_path or "").strip()
    if out_path_raw and Path(out_path_raw).is_dir():
        return error_response(
            "INVALID_PARAMETERS",
            f"output_path 必须是文件路径，收到的是目录: {out_path_raw}",
            hint=("传 .../xxx.sNp 或 .../xxx.s2p。只给目录时 LASPCEM 导不出文件，"
                  "工具会白等 30 s 才报 EXPORT_NO_OUTPUT（2026-11-13 实测）。"),
        )
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe, err = _resolve_exe("")
    if err:
        return err

    bad_setup = _check_explicit_setup(resolved, setup_name)
    if bad_setup:
        return bad_setup
    setup = _resolve_setup_name(resolved, setup_name)
    held = _project_in_use(resolved)
    if held:
        return _busy_response(held, "导出")
    busy = project_registry.conflict(resolved)   # 后台求解任务正在动这个工程
    if busy:
        return _registry_busy_response(busy, "导出")
    locks_removed = [p for p in [_clear_stale_lock(resolved)] if p]
    out = output_path.strip() or default_result_path("export_laspcem_snp", ".sNp")
    sn, candidates = _resolve_solution_name(resolved, setup, solution_name)
    # ⚠️ 判「是否已求解」必须以 is_solved()（SolutionId 记录）为准，不能用解 id 候选是否非空：
    # 未求解的工程里也会残留 FEM_1/FEM_2 这类旧字符串（实测 2026-11-12：is_solved=False 但
    # detect_solution_names 返回 ['FEM_1','FEM_2']），用它当判据会直接跳过求解、导出必然无产物。
    solved_first = False

    solved_now = is_solved(resolved)
    # 实测（2026-11-12）：结果目录 `1_Result` 被删也能导出 —— .sem 自带结果数据，
    # 打开工程时 LASPCEM 会把目录重建（BGA 工程实测 4.2 s 出 91 行）。
    # 所以这里**不设硬门槛**，只把事实记在返回体里；真导不出来由 EXPORT_NO_OUTPUT 兜底。
    results_present = _results_present(resolved)
    results_dir_before = _dir_stat(_results_dir(resolved))

    if not solved_now:
        # 不在导出里偷偷跑求解（那会让工具耗时从 5 s 变成 4 分钟、占死 MCP 请求）。
        # 把"先求解"交给求解任务：服务端会在求解完成后自动接着导出。
        resp = error_response(
            "SOLVE_REQUIRED",
            "工程尚未求解（.sem 里没有解记录），导出需要已求解的工程",
            hint=("先调 run_laspcem_solve_async(sem_path=…) 求解；想一把串完就加 then_export=True，"
                  "完成后用同一个 task_id 查结果（那时导出只需 5 秒）"
                  + ("。results_present=true：工程目录里有结果日志但 .sem 无解记录"
                     "（可能被回滚/替换过），重跑前先确认旧结果是否还有效" if results_present else "")),
            sem_path=resolved,
        )
        resp["suggested"] = {"tool": "run_laspcem_solve_async",
                             "arguments": {"sem_path": resolved, "then_export": True}}
        resp["running_solve_task"] = _running_solve_task_for(resolved)
        resp["results_present"] = results_present
        return resp

    sweeping_auto = sweeping_parameters is None
    if sweeping_auto:
        sweeping_parameters = {v: ["nominal"] for v in extract_design_variables(resolved)}
    empty_sweep_warning = ""
    if sweeping_auto and not sweeping_parameters:
        empty_sweep_warning = ("没有从 .sem 里自动探测到设计变量（double 型变量可能取不到），"
                               "导出的数据维度可能不全；如需指定请传 sweeping_parameters。")

    log = default_log_path("export")
    script = build_export_snp_script(
        resolved, out, setup_name=setup, solution_name=sn,
        sweeping_parameters=sweeping_parameters, log_path=log,
    )
    # 不等进程退出（GUI 常常不退），改为轮询产物；拿到文件后主动关 GUI
    proc, script_path, spawn_err = _spawn_script(exe, script)
    if spawn_err:
        return error_response("EXPORT_FAILED", spawn_err, script_log=log)
    ctx_handle = _ctx_handle()
    if ctx_handle is not None:
        ctx_handle["proc"] = proc
        ctx_handle["script_path"] = script_path
    # 导出前先给已有同名产物拍指纹：只有被本次改写过的才算成功（否则旧文件会造成假成功）
    existing = _resolve_snp_output(out)
    before_sig = _file_sig(existing) if existing else None
    # 实测（2026-11-13）：`is_solved=True` 但结果目录被清空的工程，LASPCEM 会报
    # error 892（calculation result is no longer valid；旧版报 167）并且**不会产出** Touchstone，
    # 同时弹一个模态「Do you want to exit the program?」确认框。这种情形最多等 30 s 就判失败。
    wait_seconds, results_invalid_suspect = timeout_seconds, False
    if solved_now and not results_present:
        wait_seconds = min(int(timeout_seconds), 30)
        results_invalid_suspect = True
    t0 = time.time()
    try:
        actual = _wait_for_artifact(out, proc, wait_seconds, before_sig=before_sig, log_path=log)
    finally:
        _terminate(proc)
        after_lock = _clear_stale_lock(resolved)
        if after_lock:
            locks_removed.append(after_lock)
    results_dir_after = _dir_stat(_results_dir(resolved))
    elapsed = round(time.time() - t0, 1)
    rc = proc.returncode if proc is not None else None

    # 脚本自己会往同一个 log 写 BEGIN/OK/traceback；下面写摘要前先留存原文，
    # 否则脚本异常栈被摘要覆盖，script_log_tail 里只剩摘要（2026-11-13 审查）
    script_output = _read_script_log(log)
    Path(log).write_text("\n".join([
        f"exe={exe}",
        f"script={script_path}",
        f"setup={setup}",
        f"solution={sn}",
        f"requested={out}",
        f"artifact={actual}",
        f"artifact_sig_before={before_sig}",
        f"stale_lock_removed={locks_removed}",
        f"elapsed={elapsed}",
        f"exit_code={rc}",
    ]) + "\n", encoding="utf-8")

    rows = _snp_data_rows(actual) if actual else 0
    if actual and rows == 0:
        return error_response(
            "EXPORT_EMPTY",
            f"导出文件 {actual} 只有表头/没有数据行（0 行数值）",
            hint=("解名或扫频参数可能不对；也可能是该解本身没有插值数据。"
                  "看 script_log_tail 与 GUI 的 Message 面板。"),
            output_path=actual,
            solution_name=sn,
            script_log=log,
            script_log_tail=_read_script_log(log),
            script_output=script_output,
        )

    if actual:
        result = {
            "success": True,
            "sem_path": resolved,
            "output_path": actual,
            "requested_output_path": out,
            "exported": True,
            "data_rows": rows,
            "setup_name": setup,
            "solution_name": sn,
            "solution_candidates": candidates,
            "sweeping_variables": sorted(sweeping_parameters),
            "sweeping_parameters_auto": sweeping_auto,
            "solved_first": solved_first,
            "results_present_before": results_present,
            "results_dir_before": results_dir_before,
            "results_dir_after": results_dir_after,
            "results_dir_changed": results_dir_before != results_dir_after,
            "elapsed_seconds": elapsed,
            "exit_code": rc,
            "stale_lock_removed": locks_removed,
            "script_path": script_path,
            "script_log": log,
            "script_log_tail": _read_script_log(log),
            "script_output": script_output,
            "message": (f"已导出 {actual}（{rows} 行数据）"
                        + ("（导出前自动求解）" if solved_first else "")),
        }
        if empty_sweep_warning:
            result["warning"] = empty_sweep_warning
        return result

    resp = error_response(
        "EXPORT_NO_OUTPUT",
        (f"{wait_seconds}s 内没有产生 Touchstone 文件（请求 {out}，也未找到同名 .s<N>p）"
         + ("；工程有解记录但结果目录为空，疑似结果已失效（GUI Message 面板实测 error 892，旧版 167）"
            if results_invalid_suspect else "")),
        hint=(("工程有解记录但结果目录为空，GUI Message 面板实测 error 892（旧版 167，结果已失效）；"
               "此时 GUI 会弹「Do you want to exit the program?」模态框，工具等 30 s 后强杀。"
               "重新求解一次（模型真的变化才会重算：改变量/几何），或从源 .aedt 重新导入；"
               "本工具不做强制重解。")
              if results_invalid_suspect else
              (f"setup={setup}, solution={sn}, 是否先求解={solved_first}, elapsed={elapsed}s；"
               "看 script_log_tail 与 GUI 的 Message 面板")),
        script_log=log,
        script_log_tail=_read_script_log(log),
        solution_candidates=candidates,
    )
    resp["results_present_before"] = results_present
    resp["results_dir_before"] = results_dir_before
    resp["results_dir_after"] = results_dir_after
    resp["results_dir_changed"] = results_dir_before != results_dir_after
    resp["waited_seconds"] = wait_seconds
    resp["script_output"] = script_output
    return resp


@mcp.tool()
def build_laspcem_report(
    sem_path: str,
    setup_name: str = "",
    report_name: str = "Sparameter_1",
    solution_name: str = "",
    timeout_seconds: int = 600,
    port_count: int = 0,
) -> dict[str, Any]:
    """给已求解的 .sem 工程建 S 参数网络报告（没求解就返回 SOLVE_REQUIRED）。

    通过 `laspcem /session <脚本>` 执行，report.CreateNetworkPlot 建矩形坐标网络报告。

    用法："给这个工程建 S 参数报告"、"对 C:/demo.sem 建网络报告"

    Args:
        sem_path: .sem 工程文件绝对路径。
        setup_name: setup 名；默认从 .sem 自动探测（求解过的是 Setup1(FEM) 这种）。
        report_name: 报告名，默认 "Sparameter_1"。
        solution_name: 解名称；默认 = 算法_<setup 基名>（如 FEM_Setup1）。
        timeout_seconds: 建报告阶段最长等待秒数，默认 600。
        port_count: 端口数（决定画哪些 S(i,j)）；0 = 自动（看同目录 Touchstone 后缀，兜底 2）。

    Returns:
        {"success": True, "sem_path": "...", "report_name": "...", "setup_name": "Setup1(FEM)",
         "solution_name": "FEM_Setup1", "solution_candidates": ["FEM_1", "FEM_2"],
         "solved_first": bool, "verified": False, "message": "..."}
        solution_candidates 是 .sem 里的内部结果 id，仅供排查，不是 report API 的解名。
    """
    report_name, name_err = require_nonempty(report_name, label="report_name")
    if name_err:
        return name_err
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe, err = _resolve_exe("")
    if err:
        return err

    bad_setup = _check_explicit_setup(resolved, setup_name)
    if bad_setup:
        return bad_setup
    setup = _resolve_setup_name(resolved, setup_name)
    held = _project_in_use(resolved)
    if held:
        return _busy_response(held, "建报告")
    busy = project_registry.conflict(resolved)   # 后台求解任务正在动这个工程
    if busy:
        return _registry_busy_response(busy, "建报告")
    locks_removed = [p for p in [_clear_stale_lock(resolved)] if p]
    sn, candidates = _resolve_solution_name(resolved, setup, solution_name)
    # 遗留字段：旧版「导出前自动求解」删掉后 solved_first 永远是 False，
    # message 里的「（导出前自动求解）」分支永不触发（2026-11-13 复核，保留字段以免破坏调用方）
    solved_first = False
    solved_now = is_solved(resolved)
    results_present = _results_present(resolved)   # 同上：只记录，不阻断
    if not solved_now:
        # 同导出：不在建报告里偷偷求解，交给求解任务（服务端串起来，完成后自动接着建报告）
        resp = error_response(
            "SOLVE_REQUIRED",
            "工程尚未求解（.sem 里没有解记录），建报告需要已求解的工程",
            hint=("先调 run_laspcem_solve_async(sem_path=…) 求解；想一把串完就加 then_report=True，"
                  "完成后用同一个 task_id 查结果（那时建报告只需 6 秒）"
                  + ("。results_present=true：工程目录里有结果日志但 .sem 无解记录"
                     "（可能被回滚/替换过），重跑前先确认旧结果是否还有效" if results_present else "")),
            sem_path=resolved,
        )
        resp["suggested"] = {"tool": "run_laspcem_solve_async",
                             "arguments": {"sem_path": resolved, "then_report": True}}
        resp["running_solve_task"] = _running_solve_task_for(resolved)
        resp["results_present"] = results_present
        return resp
    sweeping_parameters = {v: ["nominal"] for v in extract_design_variables(resolved)}
    results_dir_before = _dir_stat(_results_dir(resolved))
    log = default_log_path("report")
    ports = int(port_count) if port_count else detect_port_count(resolved)
    script = build_report_script(
        resolved, setup_name=setup, report_name=report_name,
        solution_name=sn, port_count=ports, sweeping_parameters=sweeping_parameters,
        log_path=log,
    )
    # 不等进程退出（GUI 常常 app.Exit() 后仍不退），用 .sem 是否被 SaveAs 改写当完成信号
    sig0 = _file_sig(resolved)
    proc, script_path, spawn_err = _spawn_script(exe, script)
    if spawn_err:
        return error_response("REPORT_FAILED", spawn_err, script_log=log)
    t0 = time.time()
    verified = False
    try:
        while time.time() - t0 < timeout_seconds:
            sig = _file_sig(resolved)
            if sig is not None and sig != sig0:
                verified = True
                break
            if _script_log_failed(log):
                break          # 脚本已抛异常 ⇒ 工程不会再有改动，立刻收手
            if proc.poll() is not None:
                break
            time.sleep(1)
    finally:
        _terminate(proc)
        after_lock = _clear_stale_lock(resolved)
        if after_lock:
            locks_removed.append(after_lock)
    elapsed = round(time.time() - t0, 1)
    rc = proc.returncode if proc is not None else None
    # 同 export：摘要覆盖前先留存脚本原文
    script_output = _read_script_log(log)
    Path(log).write_text("\n".join([
        f"exe={exe}", f"script={script_path}", f"setup={setup}", f"solution={sn}",
        f"report={report_name}", f"sem_saved={verified}", f"elapsed={elapsed}", f"exit_code={rc}",
        f"stale_lock_removed={locks_removed}",
    ]) + "\n", encoding="utf-8")

    if not verified and elapsed >= timeout_seconds:
        return error_response("REPORT_TIMEOUT", f"{timeout_seconds}s 内没看到工程被改写（SaveAs）",
                              retryable=True, script_log=log, elapsed=elapsed,
                              script_output=script_output)

    if not verified:
        # GUI 提前退出（proc.poll() 有值）或循环异常结束 ⇒ 无法确认报告真的建上，
        # 不能报 success。2026-11-13 复核：原来只在 message 里写「请到 GUI 确认」，
        # 读 success 的 agent 会当成「建报告成功」。
        return error_response(
            "REPORT_NOT_VERIFIED",
            (f"建报告脚本已执行，但工程没有被改写，无法确认报告 {report_name} 是否真的建上"
             f"（elapsed={elapsed}s，exit_code={rc}）"),
            hint=("看 script_output / script_log 与 GUI Message 面板；常见原因：解名或 setup 不匹配、"
                  "工程打不开、报告脚本报错。必要时到 GUI 手工建一次确认。"),
            retryable=True, script_log=log, elapsed=elapsed, exit_code=rc,
            report_name=report_name, verified=False, script_output=script_output,
            port_count=ports, setup_name=setup, solution_name=sn,
        )

    return {
        "success": True,
        "sem_path": resolved,
        "report_name": report_name,
        "setup_name": setup,
        "exit_code": rc,
        "verified": verified,
        "port_count": ports,
        "results_dir_before": results_dir_before,
        "results_dir_after": _dir_stat(_results_dir(resolved)),
        "results_present_before": results_present,
        "elapsed_seconds": elapsed,
        "stale_lock_removed": locks_removed,
        "solution_name": sn,
        "solution_candidates": candidates,
        "solved_first": solved_first,
        "script_path": script_path,
        "script_log": log,
        "script_log_tail": _read_script_log(log),
        "script_output": script_output,
        "message": (f"已在 LASPCEM 中创建报告 {report_name}"
                    f"（verified={verified}：{'工程已被 SaveAs 改写' if verified else '未见工程改写，请到 GUI 确认'}）"),
    }


def _snapshot(directory: Path, pattern: str) -> dict[str, tuple[int, float]]:
    """目录里符合 pattern 的文件的 (size, mtime) 快照。"""
    snap: dict[str, tuple[int, float]] = {}
    try:
        for f in directory.glob(pattern):
            try:
                st = f.stat()
            except OSError:
                continue
            snap[str(f)] = (st.st_size, st.st_mtime)
    except OSError:
        pass
    return snap


def _sem_snapshot(directory: Path) -> dict[str, tuple[int, float]]:
    """OutputDir 里现有 .sem 的 (size, mtime) 快照，用来判断导入产出了哪个文件。"""
    return _snapshot(directory, "*.sem")


def _remove_generated_script(sem_path: str, before: dict[str, tuple[int, float]]) -> list[str]:
    """删掉导入时 LASPCEM 自动生成的同名建模脚本 `<stem>.py`。

    实测：每次 aedt→sem 都会在输出目录留下 `<工程名>.py`（与 .sem 同 stem）。
    只删**本次新建或被改写**的那份（与 before 快照比对），用户原有的同名脚本不动。
    """
    script = Path(sem_path).with_suffix(".py")
    try:
        st = script.stat()
    except OSError:
        return []
    if before.get(str(script)) == (st.st_size, st.st_mtime):
        return []                      # 本来就存在且未变 ⇒ 不是本次生成
    try:
        script.unlink()
        return [str(script)]
    except OSError:
        return []


def _wait_for_new_sem(output_dir: str, before: dict[str, tuple[int, float]],
                      proc: subprocess.Popen | None, timeout_seconds: int,
                      poll: float = 2.0, script_log: str = "") -> str | None:
    """等 `aedt.Import` 产出新/被改写的 `.sem`（Background=True 时导入是异步的）。

    产物文件名由 aedt 内的 design 名决定（`CPWG-1.aedt` → `CPWG-1-HFSSDesign1.sem`），
    所以这里靠"目录里哪个 .sem 是新的/变过的"来认定，不按输入文件名硬推。

    `script_log` 给了就顺便盯脚本日志：**出现 Traceback/Import failed 立刻返回 None**。
    实测（2026-11-13）：导入失败时脚本抛 `Exception: Import AEDT file failed`，但 GUI 没有
    Exit 会一直活着 ⇒ 只看进程/超时要白等 600 s，看日志 10 s 就结束。
    """
    directory = Path(output_dir)
    t0 = time.time()

    def _pick() -> str | None:
        now = _sem_snapshot(directory)
        changed = [p for p, sig in now.items() if before.get(p) != sig and sig[0] > 0]
        return max(changed, key=lambda p: now[p][1]) if changed else None

    def _script_failed() -> bool:
        if not script_log:
            return False
        text = _read_script_log(script_log) or ""
        return ("Traceback (most recent call last)" in text
                or "Import AEDT file failed" in text)

    while True:
        got = _pick()
        if got:
            return got
        if _script_failed():
            time.sleep(2)          # 给宿主留一点落盘时间，再确认一次
            return _pick()
        if proc is not None and proc.poll() is not None:
            time.sleep(2)          # 给宿主流盘留点时间
            return _pick()
        if time.time() - t0 > timeout_seconds:
            return None
        time.sleep(poll)


@mcp.tool()
def import_aedt_as_sem(
    aedt_path: str,
    output_dir: str = "",
    ansys_version: str = "2020.2",
    with_geometry_parameters: bool = True,
    delete_operand_parts: bool = True,
    delete_coordinate_systems: bool = False,
    background: bool = True,
    compatible: bool = True,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    """把 ANSYS HFSS `.aedt` 导入 LASPCEM 并生成 `.sem` 工程（等价于 GUI 的 Import）。

    脚本照抄 GUI 录制：`app.New(0)` → 关掉空的 "Untitled 0" → `aedt.Import({...})`。
    因为 `background=True` 时导入是**异步**的，本工具会轮询 OutputDir 里新出现的 `.sem`，
    拿到文件后关闭 GUI，并回报该工程的求解状态（新导入的工程应为未求解）。
    导入过程会顺带生成同名建模脚本 `<工程名>.py`，本工具事后会删除本次生成的那份
    （`removed_scripts` 可见）；同时清理残留的 `~$<工程名>.lock`。

    用法：把这个 aedt 转成 sem；把 C:/demo.aedt 导入 LASPCEM 并给我 .sem 路径

    Args:
        aedt_path: 源 `.aedt` 文件绝对路径。
        output_dir: `.sem` 输出目录，默认与 `.aedt` 同目录。
        ansys_version: 源文件 ANSYS 版本，默认 "2020.2"。
        with_geometry_parameters: 是否带上几何参数（默认 True）。
        delete_operand_parts: 是否删除布尔运算的 operand 体（默认 True）。
        delete_coordinate_systems: 是否删除坐标系（默认 False）。
        background: 后台导入（异步，默认 True）。
        compatible: 兼容模式（默认 True）。
        timeout_seconds: 等 `.sem` 出现的最长秒数，默认 900。

    Returns:
        {"success": True, "sem_path": "...CPWG-1-HFSSDesign1.sem", "output_dir": "...",
         "elapsed_seconds": 41.2, "is_solved": False, "setup_name": "Setup1",
         "script_path": "...", "script_log": "...", "message": "..."}
        失败码：`IMPORT_NO_OUTPUT`（没产出 .sem）、`INVALID_PATH`、`IMPORT_FAILED`（启动失败）。
    """
    try:
        resolved = validate_file(aedt_path, (".aedt",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe, err = _resolve_exe("")
    if err:
        return err

    out_dir = output_dir.strip() or str(Path(resolved).parent)
    try:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return error_response("INVALID_PATH", f"输出目录不可用: {exc}")

    before = _sem_snapshot(Path(out_dir))
    before_py = _snapshot(Path(out_dir), "*.py")     # 用于事后删掉自动生成的同名建模脚本
    log = default_log_path("import")                 # 工具自己的汇总日志
    script_log = default_log_path("import_script")    # 脚本内部 BEGIN/OK + traceback
    script = build_import_aedt_script(
        resolved, out_dir, ansys_version=ansys_version,
        with_geometry_parameters=with_geometry_parameters,
        delete_operand_parts=delete_operand_parts,
        delete_coordinate_systems=delete_coordinate_systems,
        background=background, compatible=compatible, log_path=script_log,
    )
    proc, script_path, spawn_err = _spawn_script(exe, script)
    if spawn_err:
        return error_response("IMPORT_FAILED", spawn_err, script_log=log)

    t0 = time.time()
    try:
        sem_path = _wait_for_new_sem(out_dir, before, proc, timeout_seconds,
                                     script_log=script_log)
    finally:
        _terminate(proc)
    lock_removed = _clear_stale_lock(sem_path) if sem_path else None   # 导入后清残留锁
    removed_scripts = _remove_generated_script(sem_path, before_py) if sem_path else []
    elapsed = round(time.time() - t0, 1)
    rc = proc.returncode if proc is not None else None

    Path(log).write_text("\n".join([
        f"exe={exe}",
        f"script={script_path}",
        f"source={resolved}",
        f"output_dir={out_dir}",
        f"sem={sem_path}",
        f"stale_lock_removed={lock_removed}",
        f"removed_scripts={removed_scripts}",
        f"elapsed={elapsed}",
        f"exit_code={rc}",
    ]) + "\n", encoding="utf-8")

    if not sem_path:
        tail = _read_script_log(script_log)
        # LASPCEM 导入失败时会先吐出半成品建模脚本（`<aedt名>-<design>.py`），是重要证据
        partial = [f for f, sig in _snapshot(Path(out_dir), "*.py").items()
                   if before_py.get(f) != sig]
        import_failed = "Import AEDT file failed" in tail or "drawing failed" in tail
        resp = error_response(
            "IMPORT_FAILED" if import_failed else "IMPORT_NO_OUTPUT",
            (f"LASPCEM 导入失败：{Path(resolved).name} 无法转换（见 script_log_tail）"
             if import_failed else
             f"{timeout_seconds}s 内 {out_dir} 里没有出现新的 .sem"),
            hint=("源 .aedt 里有 LASPCEM 转换不了的对象（常见：几何操作按 face ID 引用，"
                  "跨版本后 ID 变了，报 'Cannot find face(ID=…)'）；在 AEDT 里另存为目标版本"
                  f"（ansys_version={ansys_version}）、或删掉报错的那个对象后重试。"
                  if import_failed else
                  "看 script_log_tail 与 GUI 的 Message 面板；也确认 aedt 版本与 "
                  f"ansys_version={ansys_version} 是否匹配。"),
            output_dir=out_dir,
            script_log=script_log,
        )
        resp["script_log_tail"] = tail
        resp["partial_outputs"] = partial
        return resp

    setups = detect_setup_names(sem_path)
    return {
        "success": True,
        "aedt_path": resolved,
        "sem_path": sem_path,
        "output_dir": out_dir,
        "sem_size": Path(sem_path).stat().st_size,
        "is_solved": is_solved(sem_path),
        "setup_name": detect_setup_name(sem_path),
        "setup_candidates": setups[:5],
        "elapsed_seconds": elapsed,
        "exit_code": rc,
        "stale_lock_removed": [lock_removed] if lock_removed else [],
        "removed_scripts": removed_scripts,
        "script_path": script_path,
        "script_log": script_log,
        "script_log_tail": _read_script_log(script_log),
        "message": f"已导入生成 {sem_path}（{elapsed}s，未求解={not is_solved(sem_path)}）",
    }


def _dir_stat(path: Path, max_depth: int = 3, cap_files: int = 5000) -> dict[str, Any]:
    """只读统计一个目录：是否存在 / 文件数 / 总字节。

    给"结果目录审计"和"工程状态判定"共用；不改动任何东西。
    `cap_files` 兜底，避免在超大目录（或网络盘）上把一次工具调用拖慢。
    """
    out: dict[str, Any] = {"exists": False, "files": 0, "bytes": 0}
    try:
        if not path.is_dir():
            return out
        out["exists"] = True
    except OSError:
        return out

    files = 0
    total = 0
    try:
        stack = [(path, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                entries = list(cur.iterdir())
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir():
                        if depth + 1 < max_depth:
                            stack.append((e, depth + 1))
                        continue
                    total += e.stat().st_size
                    files += 1
                except OSError:
                    continue
                if files >= cap_files:
                    out["truncated"] = True
                    break
            if files >= cap_files:
                break
    except OSError:
        pass
    out["files"] = files
    out["bytes"] = total
    return out


def _results_dir(sem_path: str) -> Path:
    """LASPCEM 的结果目录：与 `.sem` 同名的目录（`x.sem` → `x/`）。"""
    p = Path(sem_path)
    return p.parent / p.stem


def _project_state(sem_path: str, results_present: bool | None = None) -> dict[str, Any]:
    """工程状态三态：solved_ok / results_missing / unsolved + 推荐下一步。

    实测（2026-11-13，BGA）：结果目录被删后 `.sem` 里仍留着 SolutionId，
    `is_solved=True` 但数据没了 —— LASPCEM 报 error 892（旧版 167）、导出拿不到东西。
    这个组合必须能提前识别，否则调用方要等到导出失败才知道。
    """
    solved = is_solved(sem_path)
    present = _results_present(sem_path) if results_present is None else results_present
    if not solved:
        return {"state": "unsolved", "is_solved": False, "results_present": present,
                "next_action": "run_laspcem_solve_async",
                "hint": "工程还没求解；用 run_laspcem_solve_async(then_export=True / then_report=True) "
                        "让服务端先求解再后处理"}
    if not present:
        return {"state": "results_missing", "is_solved": True, "results_present": False,
                "next_action": "run_laspcem_solve_async",
                "hint": "解记录在但结果目录为空（数据被删/只拷了 .sem）；导出与建报告都会失败"
                        "（LASPCEM error 892，旧版 167）。需重新求解（模型要真的变化）或从源 .aedt 重新导入"}
    return {"state": "solved_ok", "is_solved": True, "results_present": True,
            "next_action": "export_laspcem_snp",
            "hint": "工程已求解且结果数据在；导出/建报告可直接调（5–6 秒）"}


@mcp.tool()
def get_laspcem_setup(sem_path: str) -> dict[str, Any]:
    """读取 .sem 工程当前配置的求解设置参数（cem.EditSolution 那套键，约 64 个）。

    直接从 .sem 二进制文件里按参数名提取，不需要打开 LASPCEM。用于在跑仿真前查看工程
    已配好的求解器设置（算法、扫频、网格、并行等）。

    ⚠️ double 型字段（扫频 RangeStart/RangeEnd/RangeStep、Frequency）在 .sem 记录里**只有
    一个引用序号、没有 8 字节数值**，无法从记录直接读出，故值为 null 并列入
    unresolved_double_keys —— 早前的实现按字符串布局硬解，会返回 1.9e-312 这类无意义次正规数。
    真实扫频范围请以 LASPCEM GUI 里的 setup 为准。其余字符串/布尔字段均已按记录正确解析。

    用法："看看这个 LASPCEM 工程的求解设置"、"这个 .sem 用的什么算法和扫频"

    Args:
        sem_path: .sem 工程文件绝对路径。

    Returns:
        {"success": True, "sem_path": "...", "setup": {...}, "count": N, "resolved_count": M,
         "unresolved_double_keys": [...], "warning"?: "..."}
    """
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    try:
        report = read_setup_report(resolved)
    except OSError as exc:
        return error_response("READ_FAILED", f"无法读取工程: {exc}")

    unresolved = report["unresolved_double_keys"]
    out: dict[str, Any] = {
        "success": True,
        "sem_path": resolved,
        "setup": report["setup"],
        "count": report["count"],
        "resolved_count": report["resolved_count"],
        "unresolved_double_keys": unresolved,
    }
    # 工程状态三态 + 结果目录审计（只读）：让调用方动手前就知道该不该先求解
    out.update(_project_state(resolved))
    out["results_dir"] = _dir_stat(_results_dir(resolved))
    if unresolved:
        out["warning"] = (f"{len(unresolved)} 个 double 型字段在 .sem 记录里只有引用序号、无数值，"
                          f"已置 null：{', '.join(unresolved)}；真实值以 LASPCEM GUI 的 setup 为准")
    return out