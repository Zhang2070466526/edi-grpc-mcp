"""ANSYS HFSS 异步仿真工具 — 复用通用 TaskRunner 串行队列，不阻塞 MCP。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pythoncom

from servers.ansys.config import (
    aedt_is_running, get_setup_module,
    _attach_aedt,
)
from servers.eda.config import validate_file
from servers.task_runner import TaskRunner
from servers.utils import submitted_response
from servers import mcp

# HFSS 全局串行队列：AEDT 是单实例桌面程序，同一时间只能跑一个仿真。
# max_tasks 与原 _MAX_HFSS_TASKS=50 对齐；TTL 用 TaskRunner 默认 2h。
hfss_runner = TaskRunner(name_prefix="hfss", max_tasks=50, max_run_seconds=7200)


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
    pythoncom.CoInitialize()
    try:
        _, desktop = _attach_aedt()
        project = desktop.SetActiveProject(project_name)
        design = project.SetActiveDesign(design_name)
        module, _ = get_setup_module(design)
        setups = list(module.GetSetups())
        if setup_name not in setups:
            raise RuntimeError(f"Setup {setup_name} not found. Available: {setups}")

        if save_before_run:
            project.Save()

        result_dir = str(Path(project_path).with_suffix(".aedtresults"))
        before_mtime = Path(result_dir).stat().st_mtime if Path(result_dir).is_dir() else None

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
        }
    finally:
        pythoncom.CoUninitialize()


def _validate_setups(project_path: str, design_name: str) -> dict:
    """校验 HFSS 项目和 Setup 是否存在，返回 setup 列表。"""
    pythoncom.CoInitialize()
    try:
        _, desktop = _attach_aedt()
        project_name = Path(project_path).stem
        projects = list(desktop.GetProjectList())

        if project_name not in projects:
            return {"success": False, "status": "project_not_open",
                    "project_name": project_name, "open_projects": projects}

        project = desktop.SetActiveProject(project_name)
        try:
            design = project.SetActiveDesign(design_name)
        except Exception:
            names = list(project.GetDesignNames()) if hasattr(project, "GetDesignNames") else []
            return {"success": False, "status": "design_not_found",
                    "requested_design": design_name, "available_designs": names}

        try:
            module, _ = get_setup_module(design)
            setups = list(module.GetSetups())
        except Exception:
            setups = []
        return {"success": True, "project_name": project_name,
                "design_name": design_name, "setups": setups}
    except Exception as exc:
        return {"success": False, "status": "com_error", "error": str(exc)}
    finally:
        pythoncom.CoUninitialize()


@mcp.tool()
def start_hfss_analysis_async(
    project_path: str,
    design_name: str,
    setup_name: str,
    save_before_run: bool = True,
) -> dict[str, Any]:
    """异步启动 HFSS Setup 仿真，立即返回 task_id。"""
    try:
        resolved = validate_file(project_path, (".aedt", ".aedtz"))
    except (FileNotFoundError, ValueError) as exc:
        return {"success": False, "status": "invalid_path", "message": str(exc)}

    if not aedt_is_running():
        return {"success": False, "status": "aedt_not_running",
                "message": "AEDT 未运行，请先用 open_hfss_project 打开工程"}

    validation = _validate_setups(resolved, design_name)
    if not validation["success"]:
        return validation

    if setup_name not in validation["setups"]:
        return {"success": False, "status": "setup_not_found",
                "requested_setup": setup_name, "available_setups": validation["setups"]}

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
            return {"success": False, "status": "analysis_busy",
                    "message": "当前已有 HFSS 仿真正在运行"}
        return {"success": False, "status": "task_limit_reached",
                "message": "HFSS 任务数已达上限，请稍后重试"}

    return submitted_response(task_id, project_name=project_name, design_name=design_name,
                              setup_name=setup_name, message="HFSS 仿真任务已提交")


@mcp.tool()
def get_hfss_analysis_status(
    task_id: str,
    refresh_from_aedt: bool = False,
) -> dict[str, Any]:
    """查询 HFSS 异步仿真状态（默认只读本地，不访问 AEDT）。"""
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
            pythoncom.CoInitialize()
            _, desktop = _attach_aedt()
            result["aedt_refresh_succeeded"] = True
            result["aedt_simulations_running"] = desktop.AreThereSimulationsRunning(True)
        except Exception as exc:
            result["aedt_refresh_succeeded"] = False
            result["aedt_refresh_error"] = str(exc)
        finally:
            pythoncom.CoUninitialize()

    return result
