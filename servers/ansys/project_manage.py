"""ANSYS HFSS 工程管理 — 打开/关闭/启动 AEDT，COM 附着操作。

open_hfss_project: 锁文件检查 → COM 附着打开或 subprocess 启动
close_hfss_project: COM 关闭 → 等待锁文件释放 → 清理残留锁
launch_aedt: 已运行则返回状态，否则 subprocess 启动
get_hfss_project_info: 只读查询，不启动 AEDT
"""

from __future__ import annotations

import psutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pythoncom

from servers.ansys.config import (
    AEDT_PATH, _AEDT_LOCK, _LAST_PID,
    aedt_is_running, get_aedt_pids, query_desktop_state,
    cleanup_stale_project_lock, get_project_lock_path,
    _attach_aedt, logger,
)
from servers.eda.config import validate_file
from servers import mcp

_OPEN_PROJECT_PATHS: dict[str, str] = {}  # project_name -> project_path


def _com_open_project(project_path: str) -> dict[str, Any]:
    """在现有 AEDT 中通过 COM 打开工程。"""
    pythoncom.CoInitialize()
    try:
        _, desktop = _attach_aedt()
        project_name = Path(project_path).stem

        if project_name in list(desktop.GetProjectList()):
            desktop.SetActiveProject(project_name)
            return {"status": "already_open", "project_name": project_name}

        result = desktop.OpenProject(project_path)
        if result is not None or project_name in list(desktop.GetProjectList()):
            return {"status": "opened", "project_name": project_name}

        return {"status": "com_open_failed", "project_name": project_name,
                "error": "OpenProject returned None and project not in list"}
    except Exception as exc:
        logger.exception("COM OpenProject failed")
        return {"status": "com_open_failed", "project_name": Path(project_path).stem,
                "error": str(exc)}
    finally:
        pythoncom.CoUninitialize()


@mcp.tool()
def open_hfss_project(
    project_path: str,
    aedt_path: str = "",
    wait_timeout: int = 30,
) -> dict[str, Any]:
    """启动 AEDT 并打开 .aedt 项目（COM 附着优先，subprocess 单次启动兜底）。

    流程：检查锁文件→清理失效锁→COM 附着打开或 subprocess 启动→轮询确认工程打开
    用法："帮我打开 C:/demo.aedt"、"在 AEDT 中打开这个 HFSS 项目"

    Returns:
        {"success": True, "status": "opened/already_open", "project_opened": True,
         "method": "com/subprocess", "duration_s": 1.2}
    """
    t0 = time.monotonic()
    wait_timeout = max(1, min(wait_timeout, 120))

    try:
        resolved = validate_file(project_path, (".aedt", ".aedtz"))
    except (FileNotFoundError, ValueError) as exc:
        return {"success": False, "status": "invalid_path", "message": str(exc)}

    exe = aedt_path or AEDT_PATH
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        return {"success": False, "status": "aedt_not_found", "message": str(exe_path)}
    if exe_path.name.lower() != "ansysedt.exe":
        return {"success": False, "status": "invalid_aedt_path", "message": "必须以 ansysedt.exe 结尾"}

    project_name = Path(resolved).stem
    global _LAST_PID

    # 检查工程锁
    lock_result = cleanup_stale_project_lock(resolved)
    if not lock_result["removed"] and lock_result.get("lock_pid"):
        if lock_result.get("status") == "lock_active":
            return {
                "success": False, "status": "project_locked",
                "lock_pid": lock_result["lock_pid"],
                "message": f"工程正在 AEDT PID={lock_result['lock_pid']} 中使用",
            }

    with _AEDT_LOCK:
        aedt_was_running = aedt_is_running()

        if aedt_was_running:
            state = query_desktop_state()
            if not state["connected"]:
                logger.warning("AEDT running but COM attach failed: %s", state.get("error"))
                return {
                    "success": False, "status": "existing_instance_com_unavailable",
                    "aedt_running": True, "project_opened": False,
                    "message": "AEDT 已运行但无法附着 COM，未创建第二个实例",
                }

            if project_name in state["projects"]:
                attach_error = None
                pythoncom.CoInitialize()
                try:
                    _, dtop = _attach_aedt()
                    dtop.SetActiveProject(project_name)
                except Exception as exc:
                    attach_error = str(exc)
                    logger.warning("SetActiveProject 失败 (%s): %s", project_name, exc)
                finally:
                    pythoncom.CoUninitialize()
                if attach_error is not None:
                    # 工程已在 AEDT 中列出但激活失败，不能假装成功
                    return {
                        "success": False, "status": "com_open_failed",
                        "aedt_running": True, "project_opened": False,
                        "project_name": project_name, "project_path": resolved,
                        "method": "com", "com_error": attach_error,
                        "message": "工程已在 AEDT 中列出，但激活失败，请重试",
                    }
                _OPEN_PROJECT_PATHS[project_name] = resolved
                rc = cleanup_stale_project_lock(resolved) if lock_result["removed"] else {}
                return {
                    "success": True, "status": "already_open",
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "com", "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"工程已打开并激活: {project_name}",
                    **({"stale_lock_removed": True} if rc.get("removed") else {}),
                }

            result = _com_open_project(resolved)
            if result["status"] in ("opened", "already_open"):
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": result["status"],
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "com", "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"HFSS 工程已打开: {project_name}",
                }

            logger.error("COM open failed: %s", result.get("error"))
            return {
                "success": False, "status": "com_open_failed",
                "aedt_running": True, "project_opened": False, "verified": True,
                "project_name": project_name, "project_path": resolved,
                "method": "com", "com_error": result.get("error"),
                "message": "AEDT 已运行，但打开工程失败",
            }

        # AEDT 未运行，单次启动
        try:
            proc = subprocess.Popen([str(exe_path), resolved], cwd=str(exe_path.parent))
            _LAST_PID = proc.pid
            logger.info("Launched AEDT PID=%d for %s", proc.pid, project_name)
        except Exception as exc:
            logger.exception("AEDT launch failed")
            return {"success": False, "status": "launch_failed", "message": str(exc)}

        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return {
                    "success": False, "status": "process_exited",
                    "aedt_running": False, "project_opened": False,
                    "message": f"AEDT 进程已退出，退出码: {proc.returncode}",
                }
            state = query_desktop_state()
            if state["connected"] and project_name in state["projects"]:
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": "opened",
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "subprocess", "pid": proc.pid,
                    "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"AEDT 已启动，工程已打开: {project_name}",
                }
            time.sleep(1)

        return {
            "success": True, "status": "launch_requested",
            "aedt_running": aedt_is_running(),
            "project_opened": None, "verified": False,
            "project_name": project_name, "project_path": resolved,
            "method": "subprocess", "pid": proc.pid,
            "duration_s": round(time.monotonic() - t0, 1),
            "message": "AEDT 启动命令已发送，COM 暂未验证到工程打开",
        }


@mcp.tool()
def close_hfss_project(
    project_name: str = "",
    project_path: str = "",
    save_before_close: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """关闭 AEDT 项目（COM 优先，force 仅结束 MCP 最后启动的 PID）。

    Args:
        project_name: 项目名，为空关闭活动项目。
        project_path: 项目路径，用于清理锁文件。
        save_before_close: 关闭前保存。
        force: 仅结束 MCP 最后启动的 PID。
    """
    global _LAST_PID

    # 确定 lock 清理路径
    lock_path = project_path or _OPEN_PROJECT_PATHS.get(project_name, "")

    if force:
        if _LAST_PID is None:
            return {"success": False, "status": "no_managed_process",
                    "message": "没有由当前 MCP 启动的 AEDT 进程"}

        pid = _LAST_PID
        try:
            proc = psutil.Process(pid)
            if not proc.is_running() or proc.name().lower() != "ansysedt.exe":
                _LAST_PID = None
                return {"success": False, "status": "managed_process_not_found",
                        "message": "记录的 PID 已不存在或不再属于 AEDT，未执行终止"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            _LAST_PID = None
            return {"success": False, "status": "managed_process_not_found",
                    "message": "记录的 PID 已不存在，未执行终止"}

        try:
            completed = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if completed.returncode != 0:
                return {"success": False, "status": "taskkill_failed",
                        "message": completed.stderr.strip() or completed.stdout.strip(),
                        "pid": pid}

            try:
                psutil.Process(pid).wait(timeout=10)
            except psutil.NoSuchProcess:
                pass
            except psutil.TimeoutExpired:
                return {"success": False, "status": "termination_timeout",
                        "message": f"已发送终止信号，但 PID {pid} 未在 10s 内退出"}

            _LAST_PID = None

            lock_cleanup = {}
            if lock_path:
                result = cleanup_stale_project_lock(lock_path)
                if result["removed"]:
                    lock_cleanup = {"lock_cleanup": result}

            if project_name in _OPEN_PROJECT_PATHS:
                del _OPEN_PROJECT_PATHS[project_name]

            return {"success": True, "method": "taskkill",
                    "message": f"已终止 PID {pid}", **lock_cleanup}
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    if not aedt_is_running():
        return {"success": False, "message": "AEDT 未运行"}

    pythoncom.CoInitialize()
    try:
        _, desktop = _attach_aedt()
        names = list(desktop.GetProjectList())

        target = project_name.strip() if project_name else (names[0] if names else "")
        if not target:
            return {"success": False, "message": "没有可关闭的项目"}

        if save_before_close:
            try:
                proj = desktop.SetActiveProject(target)
                if proj is not None:
                    proj.Save()
            except Exception:
                pass

        desktop.CloseProject(target)
        names_after = list(desktop.GetProjectList())
        closed = target not in names_after

        # 清理锁 — 等待 AEDT 自己先删，再检查残余
        lock_cleanup = {}
        if closed and lock_path:
            time.sleep(2)
            if get_project_lock_path(lock_path).is_file():
                result = cleanup_stale_project_lock(lock_path)
                if result["removed"]:
                    lock_cleanup = {"lock_cleanup": result}

        if target in _OPEN_PROJECT_PATHS:
            del _OPEN_PROJECT_PATHS[target]

        return {
            "success": closed, "method": "com", "project_closed": closed,
            "message": f"已关闭: {target}" if closed else f"未能确认关闭: {target}",
            **lock_cleanup,
        }
    except Exception as exc:
        logger.exception("COM close failed")
        return {"success": False, "message": f"关闭失败: {exc}"}
    finally:
        pythoncom.CoUninitialize()


@mcp.tool()
def launch_aedt(
    aedt_path: str = "",
    wait_timeout: int = 30,
) -> dict[str, Any]:
    """启动 AEDT（不打开项目）。已运行时仅返回状态。"""
    wait_timeout = max(1, min(wait_timeout, 120))
    global _LAST_PID

    exe = aedt_path or AEDT_PATH
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        return {"success": False, "message": str(exe_path)}
    if exe_path.name.lower() != "ansysedt.exe":
        return {"success": False, "message": "必须以 ansysedt.exe 结尾"}

    with _AEDT_LOCK:
        if aedt_is_running():
            state = query_desktop_state()
            return {"success": True, "status": "already_running",
                    "com_ready": state["connected"], "message": "AEDT 已在运行"}

        try:
            proc = subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
            _LAST_PID = proc.pid
        except Exception as exc:
            return {"success": False, "status": "launch_failed", "message": str(exc)}

        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if query_desktop_state()["connected"]:
                return {"success": True, "status": "started", "com_ready": True,
                        "pid": proc.pid, "message": "AEDT 已启动，COM 就绪"}
            time.sleep(1)

        return {"success": True, "status": "started", "com_ready": False,
                "pid": proc.pid, "message": "AEDT 已启动，COM 未在时间内就绪"}


@mcp.tool()
def get_hfss_project_info() -> dict[str, Any]:
    """查询当前 AEDT 项目信息（纯查询，不启动 AEDT）。"""
    if not aedt_is_running():
        return {"success": True, "aedt_running": False, "message": "AEDT 未运行", "pids": []}

    pids = get_aedt_pids()
    state = query_desktop_state()

    return {
        "success": True,
        "aedt_running": True,
        "pids": pids,
        "com_available": state["connected"],
        "open_projects": state.get("projects", []),
        "active_project": state.get("active_project", ""),
        "active_design": state.get("active_design", ""),
    }
