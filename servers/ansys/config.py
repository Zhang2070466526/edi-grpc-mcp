"""ANSYS HFSS 基础设施 — 进程检测、COM 附着（多 ProgID 回退）、锁文件管理。

核心功能：
- AEDT 路径检测：环境变量 → 注册表 → 默认目录（按版本号取最新）
- COM 附着：AnsoftHfss.HfssScriptInterface / Ansoft.ElectronicsDesktop 双 PID 回退
- 锁文件管理：读取 .aedt.lock 中的 DesktopProcessID，PID 存活时绝不删除
"""

from __future__ import annotations

import logging
import psutil
import threading
import winreg
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pythoncom
from dotenv import load_dotenv
from win32com.client import GetActiveObject

load_dotenv()
from servers.settings import get_settings  # noqa: E402 — 必须在 load_dotenv 之后

logger = logging.getLogger(__name__)

_AEDT_LOCK = threading.RLock()


@contextmanager
def com_session():
    """COM 初始化上下文管理器：自动 CoInitialize / CoUninitialize。"""
    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


# -- 路径 --
def _find_aedt() -> str:
    """自动检测 AEDT 安装路径：环境变量 → Windows 注册表 → 默认目录（按版本号取最新）。"""
    from_env = get_settings().aedt_path
    if from_env and Path(from_env).is_file():
        return from_env
    try:
        for reg_path in [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ]:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                for j in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{reg_path}\\{winreg.EnumKey(key, j)}") as sub:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                            if "ANSYS" in name and ("Electromagnetics" in name or "Electronics" in name):
                                loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                                exe = Path(loc) / "ansysedt.exe"
                                if exe.is_file():
                                    return str(exe)
                    except OSError:
                        pass
    except Exception:
        pass
    for base in [r"C:\Program Files\AnsysEM", r"C:\Program Files (x86)\AnsysEM"]:
        for sub in sorted(Path(base).iterdir(), reverse=True) if Path(base).is_dir() else []:
            exe = sub / "Win64" / "ansysedt.exe"
            if exe.is_file():
                return str(exe)
    return ""


AEDT_PATH = _find_aedt()


# -- 进程 --
def get_aedt_pids() -> list[int]:
    """返回所有 ansysedt.exe 进程的 PID 列表。"""
    try:
        return [p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "ansysedt.exe"]
    except Exception:
        return []


def aedt_is_running() -> bool:
    """检查是否有 ansysedt.exe 进程正在运行。"""
    return len(get_aedt_pids()) > 0


# -- COM --
_COM_PROGIDS = ("AnsoftHfss.HfssScriptInterface", "Ansoft.ElectronicsDesktop")


def _attach_aedt():
    """附着现有 AEDT 实例，返回 (app, desktop)，失败抛异常。

    全程持 _AEDT_LOCK：worker 线程、refresh_from_aedt、query_desktop_state 等多条
    调用路径都走这里，串行化 COM 附着避免并发操作 AEDT 单实例桌面。
    """
    with _AEDT_LOCK:
        for pid in _COM_PROGIDS:
            try:
                app = GetActiveObject(pid)
                return app, app.GetAppDesktop()
            except Exception:
                continue
        raise RuntimeError(f"GetActiveObject failed for: {_COM_PROGIDS}")


def query_desktop_state() -> dict[str, Any]:
    """附着现有 AEDT，返回纯字典。绝不创建新实例。"""
    with com_session():
        try:
            _, desktop = _attach_aedt()
            projects = list(desktop.GetProjectList())
            active = desktop.GetActiveProject()
            active_name = active.GetName() if active is not None else ""
            active_design = ""
            if active is not None:
                try:
                    d = active.GetActiveDesign()
                    if d is not None:
                        active_design = d.GetName()
                except Exception:
                    pass
            return {
                "connected": True,
                "projects": projects,
                "active_project": active_name,
                "active_design": active_design,
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}


def get_setup_module(design):
    """统一获取 AnalysisSetup 或 SolveSetups 模块。"""
    for mod_name in ("AnalysisSetup", "SolveSetups"):
        try:
            return design.GetModule(mod_name), mod_name
        except Exception:
            continue
    raise RuntimeError("No AnalysisSetup or SolveSetups module found")


# -- 工程锁文件 --
def get_project_lock_path(project_path: str) -> Path:
    """C:/demo.aedt -> C:/demo.aedt.lock"""
    return Path(project_path).with_suffix(".aedt.lock")


def read_project_lock_pid(lock_path: Path) -> int | None:
    """读取 .aedt.lock 文件中的 DesktopProcessID。"""
    if not lock_path.is_file():
        return None
    try:
        for line in lock_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("DesktopProcessID="):
                return int(line.split("=", 1)[1].strip())
    except Exception:
        pass
    return None


def cleanup_stale_project_lock(project_path: str) -> dict:
    """只清理已失效的工程锁。PID 活跃时绝不删除。"""
    lock = get_project_lock_path(project_path)
    result = {"removed": False, "lock_path": str(lock), "lock_pid": None}

    if not lock.is_file():
        return result

    pid = read_project_lock_pid(lock)
    result["lock_pid"] = pid

    if pid is None:
        # 无法解析 PID：保守处理。仅当根本没有 AEDT 进程时才删除坏格式锁，
        # 否则保留给用户手动排查（避免误删活跃实例的锁）。
        if not aedt_is_running():
            try:
                lock.unlink()
                result["removed"] = True
                result["status"] = "stale_lock_removed"
            except OSError as exc:
                result["status"] = "lock_remove_failed"
                result["error"] = str(exc)
        else:
            result["status"] = "lock_unknown_format"
        return result

    if pid in get_aedt_pids():
        result["status"] = "lock_active"
        return result

    # PID 已不存在，安全删除
    try:
        lock.unlink()
        result["removed"] = True
        result["status"] = "stale_lock_removed"
    except OSError as exc:
        result["status"] = "lock_remove_failed"
        result["error"] = str(exc)

    return result
