"""ANSYS HFSS 异步仿真工具 — 复用通用 TaskRunner 串行队列，不阻塞 MCP。"""

from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any

from servers.ansys.config import (
    aedt_is_running, get_setup_module,
    com_session,
    _attach_aedt,
)
from servers.task_runner import TaskRunner
from servers.utils import default_result_path, submitted_response, validate_file, error_response
from servers import mcp

# HFSS 全局串行队列：AEDT 是单实例桌面程序，同一时间只能跑一个仿真。
# max_tasks 与原 _MAX_HFSS_TASKS=50 对齐；TTL 用 TaskRunner 默认 2h。
hfss_runner = TaskRunner(name_prefix="hfss", max_tasks=50, max_run_seconds=7200)


def _hfss_su_evidence(result_dir: str, design_name: str, since_ts: float | None = None) -> dict:
    """扫 `<工程>.aedtresults/<设计>.results/*_SU.txt` 里的收敛证据。

    AEDT 求解成功后 COM 仍可能抛 `GetAppDesktop.AreThereSimulationsRunning`（工具层因此
    只能报 UNKNOWN / result_verified=false），但每个收敛频点都会落一份 `*_SU.txt`，首行
    `Success 1` —— 这比「结果目录 mtime 变了」硬得多。`since_ts` 用来排除上一次求解留下的
    旧文件（不传则只看有没有历史成功记录，供导出预检用）。
    """
    root = Path(result_dir) / f"{design_name}.results"
    if not root.is_dir():
        return {"found": False, "files": [], "count": 0, "directory": str(root)}
    tol = 2.0  # 文件系统 mtime 精度留 2 s 容差
    hits: list[str] = []
    for f in sorted(root.glob("*_SU.txt")):
        try:
            if since_ts is not None and f.stat().st_mtime < since_ts - tol:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"^\s*Success\s+1\s*$", text, re.MULTILINE):
            hits.append(str(f))
    return {"found": bool(hits), "files": hits, "count": len(hits), "directory": str(root)}


def _solved_evidence_or_error(project_path: str, design_name: str):
    """导出前的求解预检：没有任何 `Success 1` 证据就别去调 ExportNetworkData。

    返回 (evidence, None) 或 (None, error_response)。
    """
    result_dir = str(Path(project_path).with_suffix(".aedtresults"))
    su = _hfss_su_evidence(result_dir, design_name)
    if not su["found"]:
        return None, error_response(
            "SOLVE_REQUIRED",
            f"没有找到 {design_name} 的求解结果证据（{su['directory']}\\*_SU.txt 里无 Success 1），"
            "导出会失败或拿不到数据",
            hint="先 start_hfss_analysis_async 跑完该 setup 的扫频，再导出",
            evidence_directory=su["directory"],
        )
    return su, None


def _existing_snp(path: Path) -> Path | None:
    """同名导出是否已存在；`.sNp` 占位按 `.s*p` 通配判（端口数未知）。"""
    try:
        if path.suffix.lower() == ".snp":
            hits = sorted(path.parent.glob(path.stem + ".s*p"))
            return hits[0] if hits else None
        return path if path.is_file() else None
    except OSError:
        return None


def _newest_su_mtime(su: dict) -> float | None:
    """求解证据文件里最新的 mtime —— 用来算 `solution_age_seconds`（判断是否旧解）。"""
    newest: float | None = None
    for f in su.get("files") or []:
        try:
            m = Path(f).stat().st_mtime
        except OSError:
            continue
        newest = m if newest is None else max(newest, m)
    return newest


def _run_hfss_analysis_task(
    project_path: str,
    project_name: str,
    design_name: str,
    setup_name: str,
    save_before_run: bool,
) -> dict:
    """执行单个 HFSS 仿真：COM 附着→验证 setup→Analyze→校验结果，返回结果 dict。

    状态由 TaskRunner 管理（RUNNING/SUCCEEDED/FAILED）；本函数只返回结果字段，
    异常抛给 TaskRunner 记录为 FAILED。
    """
    with com_session():
        _, desktop = _attach_aedt()
        project = desktop.SetActiveProject(project_name)
        design = project.SetActiveDesign(design_name)
        module = get_setup_module(design)
        setups = list(module.GetSetups())
        if setup_name not in setups:
            raise RuntimeError(f"Setup {setup_name} not found. Available: {setups}")

        if save_before_run:
            project.Save()

        result_dir = str(Path(project_path).with_suffix(".aedtresults"))
        before_mtime = Path(result_dir).stat().st_mtime if Path(result_dir).is_dir() else None

        before_ts = time.time()
        design.Analyze(setup_name)

        # AEDT 的 Analyze 失败时通常不抛异常（错误只进 message manager），
        # 所以「目录 mtime」不是可靠的成功信号。用「结果目录含结果文件」作更强信号：
        # - 目录原本已存在且 mtime 严格增加 → 求解有进展
        # - 目录原本不存在 → 失败求解也会创建目录，但不会生成结果文件，据此区分
        result_verified = False
        if Path(result_dir).is_dir():
            if before_mtime is not None:
                result_verified = Path(result_dir).stat().st_mtime > before_mtime
            else:
                result_verified = any(f.is_file() for f in Path(result_dir).rglob("*"))
        # 硬证据优先：本次求解窗口内的 *_SU.txt 里出现 Success 1（目录 mtime 只是弱信号；
        # COM 刷新失败时 AreThereSimulationsRunning 也拿不到，别因此判 UNKNOWN）。
        su = _hfss_su_evidence(result_dir, design_name, before_ts)
        if su["found"]:
            result_verified = True
        still_running = False
        try:
            still_running = desktop.AreThereSimulationsRunning(True)
        except Exception:
            pass

        outcome_ok = result_verified and not still_running
        return {
            "result_directory": result_dir,
            "result_verified": result_verified,
            "outcome_known": outcome_ok,
            "task_success": True if outcome_ok else None,
            "su_evidence": su,
        }


def _validate_setups(project_path: str, design_name: str) -> dict:
    """校验 HFSS 项目和 Setup 是否存在，返回 setup 列表。"""
    with com_session():
        try:
            _, desktop = _attach_aedt()
            project_name = Path(project_path).stem
            projects = list(desktop.GetProjectList())

            if project_name not in projects:
                return error_response("PROJECT_NOT_OPEN", f"工程 {project_name} 未打开",
                                      project_name=project_name, open_projects=projects)

            project = desktop.SetActiveProject(project_name)
            try:
                design = project.SetActiveDesign(design_name)
            except Exception:
                names = list(project.GetDesignNames()) if hasattr(project, "GetDesignNames") else []
                return error_response("DESIGN_NOT_FOUND", f"设计 {design_name} 未找到",
                                      requested_design=design_name, available_designs=names)

            try:
                module = get_setup_module(design)
                setups = list(module.GetSetups())
            except Exception:
                setups = []
            return {"success": True, "project_name": project_name,
                    "design_name": design_name, "setups": setups}
        except Exception as exc:
            return error_response("COM_ERROR", str(exc))


@mcp.tool()
def start_hfss_analysis_async(
    project_path: str,
    design_name: str,
    setup_name: str,
    save_before_run: bool = True,
) -> dict[str, Any]:
    """异步启动 HFSS Setup 仿真，立即返回 task_id。

    用法："跑一下这个 HFSS 工程的 Setup1 仿真"、"对 HFSSDesign1 的 Setup1 求解"

    对 AEDT 中已打开工程的指定 design 下的 setup 发起 Analyze，后台单 worker 串行执行。
    返回 task_id 后用 get_hfss_analysis_status 轮询进度。design_name/setup_name 可先
    用 get_hfss_project_info 查活动设计，或先 open_hfss_project 打开工程。

    Args:
        project_path: .aedt 项目文件绝对路径。
        design_name: HFSS 设计名（如 "HFSSDesign1"）。
        setup_name: 该设计下的 Analysis Setup 名（如 "Setup1"）。
        save_before_run: 求解前是否保存工程，默认 True。

    Returns:
        {"success": True, "task_id": "hfss-...", "status": "QUEUED"}；用 get_hfss_analysis_status(task_id) 查进度。
    """
    try:
        resolved = validate_file(project_path, (".aedt", ".aedtz"))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    if not aedt_is_running():
        return error_response("AEDT_NOT_RUNNING", "AEDT 未运行，请先用 open_hfss_project 打开工程")

    validation = _validate_setups(resolved, design_name)
    if not validation["success"]:
        return validation

    if setup_name not in validation["setups"]:
        return error_response("SETUP_NOT_FOUND", f"Setup {setup_name} 未找到",
                              requested_setup=setup_name, available_setups=validation["setups"])

    project_name = Path(resolved).stem

    # AEDT 单实例：同一时间只能跑一个仿真，禁止排队。
    # require_idle=True 原子地「检查空闲 + 提交」，避免并发请求同时通过检查的 TOCTOU。
    task_id = hfss_runner.submit(
        _run_hfss_analysis_task,
        resolved, project_name, design_name, setup_name, save_before_run,
        metadata={
            "operation": "hfss_analysis",
            "project_path": resolved,
            "project_name": project_name,
            "design_name": design_name,
            "setup_name": setup_name,
        },
        require_idle=True,
    )
    if task_id is None:
        # 原子提交失败：非空闲（analysis_busy）或任务数达上限（task_limit_reached）
        if hfss_runner.pending_count() > 0:
            return error_response("ANALYSIS_BUSY", "当前已有 HFSS 仿真正在运行")
        return error_response("TASK_LIMIT_REACHED", "HFSS 任务数已达上限，请稍后重试")

    return submitted_response(task_id, project_name=project_name, design_name=design_name,
                              setup_name=setup_name, message="HFSS 仿真任务已提交")


@mcp.tool()
def get_hfss_analysis_status(
    task_id: str,
    refresh_from_aedt: bool = False,
) -> dict[str, Any]:
    """查询 HFSS 异步仿真状态（默认只读本地，不访问 AEDT）。

    Args:
        task_id: start_hfss_analysis_async 返回的 task_id。
        refresh_from_aedt: 是否实时访问 AEDT 刷新仿真运行状态，默认 False。
    """
    snap = hfss_runner.snapshot(task_id)
    if snap is None:
        return {
            "success": False,
            "task_id": task_id,
            "status": "UNKNOWN",
            "task_success": None,
            "outcome_known": False,
            "error_code": "TASK_NOT_FOUND",
            "message": "HFSS 仿真任务不存在、已过期或服务已重启",
        }

    meta = snap.get("metadata", {})
    result_data = snap.get("result") or {}
    completed = snap.get("finished_at") is not None
    result: dict[str, Any] = {
        "success": True, "task_id": task_id, "status": snap["status"],
        "completed": completed,
        "task_success": result_data.get("task_success"),
        "outcome_known": result_data.get("outcome_known", False),
        "project_path": meta.get("project_path", ""),
        "project_name": meta.get("project_name", ""),
        "design_name": meta.get("design_name", ""),
        "setup_name": meta.get("setup_name", ""),
        "created_at": snap["created_at"],
        "started_at": snap["started_at"],
        "finished_at": snap["finished_at"],
        "result_directory": result_data.get("result_directory", ""),
        "result_verified": result_data.get("result_verified"),
        "error": snap.get("error", ""),
    }

    # 兼容旧状态语义：
    # - FAILED 视为「明确失败」；SUCCEEDED 但 outcome_known=False 等价旧 "UNKNOWN"
    if snap["status"] == "FAILED":
        result["outcome_known"] = True
        result["task_success"] = False
    elif snap["status"] == "SUCCEEDED" and not result.get("outcome_known"):
        result["status"] = "UNKNOWN"

    if snap["started_at"] is not None:
        end = snap["finished_at"] or time.time()
        result["elapsed_seconds"] = round(end - snap["started_at"], 1)

    if refresh_from_aedt:
        try:
            with com_session():
                _, desktop = _attach_aedt()
                result["aedt_refresh_succeeded"] = True
                result["aedt_simulations_running"] = desktop.AreThereSimulationsRunning(True)
        except Exception as exc:
            result["aedt_refresh_succeeded"] = False
            result["aedt_refresh_error"] = str(exc)

    return result


@mcp.tool()
def export_hfss_snp(
    project_path: str,
    design_name: str,
    setup_name: str,
    sweep_name: str = "Sweep",
    output_path: str = "",
    port_count: int = 0,
    overwrite: bool = False,
) -> dict[str, Any]:
    """导出 HFSS 已求解的 S 参数为 Touchstone 文件（.sNp）。

    用法："把 CPW_SL 的 S 参数导成 snp"、"导出 HFSSDesign1 的 Setup1 扫频结果"

    对 AEDT 中**已打开且已求解**的工程调用 Solutions.ExportNetworkData（做法同
    edi-server `apps/star/hfss/hfss_export.py`），在隔离子进程里执行 —— AEDT 卡死时
    不会拖住服务。端口数会先探测，文件名按端口数落成 .s<N>p。要重新求解请先调
    start_hfss_analysis_async。

    导出前先扫 `<工程>.aedtresults/<设计>.results/*_SU.txt` 里的 `Success 1` 作求解证据；
    没有任何证据时直接返回 `SOLVE_REQUIRED`，不去白调 ExportNetworkData（否则只会拿到
    EXPORT_FAILED 或空/旧数据）。

    Args:
        project_path: .aedt 项目文件绝对路径。
        design_name: HFSS 设计名（如 "HFSSDesign1"）。
        setup_name: Analysis Setup 名（如 "Setup1"）。
        sweep_name: 扫频名（如 "Sweep"）。必填：AEDT 2020 的自适应解
            （"Setup : LastAdaptive"）导出不稳定，传空会直接报错。
        output_path: 输出路径；留空则落到默认产物目录（<result_dir>/export_hfss_snp_<时间戳>.sNp，
            与 export_laspcem_snp / cst_export_snp 口径一致）。
        port_count: 端口数（决定 .sNp 后缀）；0 = 自动探测，探测失败按 2。
        overwrite: 显式给定的 output_path 已存在时是否允许覆盖，默认 False
            （返回 `OUTPUT_ALREADY_EXISTS`；2026-11-26 之前是静默覆盖）。

    Returns:
        {"success": true, "snp_path": "...s2p", "size_bytes": 33101,
         "port_count": 2, "port_count_source": "content", "solution": "Setup1 : Sweep",
         "result_verified": true, "solution_mtime": 1795678493.3, "solution_age_seconds": 12}
        `solution_age_seconds` 很大 ⇒ 导出的是**旧解**（没有重新求解）。
    """
    from servers.ansys.project_manage import _call_com_child

    try:
        resolved = validate_file(project_path, (".aedt", ".aedtz"))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    if not aedt_is_running():
        return error_response("AEDT_NOT_RUNNING", "AEDT 未运行，请先用 open_hfss_project 打开工程")

    project_name = Path(resolved).stem
    # 预检：没有 Success 1 证据就别白调 ExportNetworkData（它会失败或给出空/旧数据）
    su, su_err = _solved_evidence_or_error(resolved, design_name)
    if su_err:
        return su_err
    if not output_path:
        # 默认落 MCP_RESULT_DIR（与 export_laspcem_snp / cst_export_snp 口径一致）
        output_path = default_result_path("export_hfss_snp", ".sNp")
    else:
        existing = _existing_snp(Path(output_path))
        if existing is not None and not overwrite:
            target = Path(output_path)
            return error_response(
                "OUTPUT_ALREADY_EXISTS",                 # 复用 servers/utils._DEFAULT_HINTS 已登记的码
                f"输出文件已存在：{existing}",
                hint="要覆盖请传 overwrite=True；或换一个 output_path"
                     "（不传 output_path 时默认落 MCP_RESULT_DIR，文件名自带时间戳、不会撞名）",
                existing_path=str(existing),
                suggested_path=str(target.with_name(
                    f"{target.stem}_{time.strftime('%H%M%S')}{target.suffix}")))

    ok, result, err = _call_com_child(
        "export_snp",
        (resolved, design_name, setup_name, sweep_name, output_path,
         str(max(0, int(port_count)))),
        180.0,
    )
    if not ok:
        return error_response("EXPORT_FAILED", err or "导出失败",
                              project_name=project_name, design_name=design_name,
                              setup_name=setup_name, sweep_name=sweep_name)

    result = dict(result or {})
    su_mtime = _newest_su_mtime(su)
    return {
        "success": True,
        "project_name": project_name,
        "design_name": design_name,
        "snp_path": result.get("snp_path", ""),
        "size_bytes": result.get("size", 0),
        "port_count": result.get("port_count", 0),
        "port_count_source": result.get("port_count_source", ""),
        "solution": result.get("solution", ""),
        "aedt_pid": result.get("pid"),
        # 求解证据：导出前已确认存在 `Success 1`；age 很大说明导的是旧解
        "result_verified": True,
        "solution_mtime": su_mtime,
        "solution_age_seconds": (round(time.time() - su_mtime, 1) if su_mtime else None),
        "message": f"已导出 {result.get('solution', '')} 的 S 参数: {result.get('snp_path', '')}",
    }
