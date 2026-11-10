"""LASPCEM 电磁仿真基础设施 — 可执行路径检测与进程状态。

LASPCEM 是西安电子科技大学的电磁仿真平台（Qt GUI + 内嵌 Python），无 COM/gRPC
自动化接口，这里只负责「定位 laspcem.exe」与「判断是否在跑」两件事。

路径检测顺序：环境变量 LASPCEM_PATH → 注册表卸载项 → 注册表 .sem 文件关联。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import psutil

from servers.settings import get_settings

logger = logging.getLogger("laspcem")

#: .sem 文件关联的 ProgID（安装时写入），shell open command 形如 "bin\laspcem.exe" "%1"
_LASPCEM_PROGID = "LASPCEMFile.sem"


def _first_quoted_arg(cmd: str) -> str:
    """从 shell open command 提取首个引号包裹的可执行路径。"""
    m = re.match(r'^\s*"([^"]+)"', cmd or "")
    return m.group(1) if m else ""


def _exe_from_progid() -> str:
    """从注册表 .sem 文件关联读取 laspcem.exe 路径（无需硬编码安装目录）。"""
    try:
        import winreg
    except ImportError:  # 非 Windows
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"{_LASPCEM_PROGID}\shell\open\command") as key:
            cmd, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return ""
    exe = _first_quoted_arg(cmd)
    return exe if Path(exe).is_file() else ""


def _exe_from_uninstall() -> str:
    """从注册表卸载项（DisplayName 含 LASPCEM）读取安装目录，拼 bin\\laspcem.exe。"""
    try:
        import winreg
    except ImportError:  # 非 Windows
        return ""
    try:
        for rp in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                   r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rp)
            except OSError:
                continue
            for j in range(winreg.QueryInfoKey(key)[0]):
                sub_name = winreg.EnumKey(key, j)
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{rp}\{sub_name}") as sub:
                        try:
                            name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        except OSError:
                            continue
                        if "laspcem" not in str(name).lower():
                            continue
                        try:
                            loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                        except OSError:
                            continue
                        exe = Path(loc) / "bin" / "laspcem.exe"
                        if exe.is_file():
                            return str(exe)
                except OSError:
                    continue
    except OSError:
        pass
    return ""


def find_laspcem_exe() -> str:
    """定位 laspcem.exe：环境变量 → 注册表卸载项 → 注册表 .sem 关联。"""
    configured = (get_settings().laspcem_path or "").strip()
    if configured:
        p = Path(configured).expanduser()
        if p.is_file():
            return str(p)

    from_uninstall = _exe_from_uninstall()
    if from_uninstall:
        return from_uninstall

    return _exe_from_progid()


def _project_arg(cmdline: list[str]) -> str:
    """从命令行里取 .sem 工程路径（laspcem.exe "<file>.sem"；未带工程返回空串）。"""
    for arg in (cmdline or [])[1:]:
        if arg.lower().endswith(".sem"):
            return arg
    return ""


def find_laspcem_processes() -> list[dict[str, Any]]:
    """列出在跑的 LASPCEM 进程（按进程名匹配 laspcem.exe / LaspcemApp.exe）。

    LASPCEM 没有单实例转发：每次带 .sem 启动都会新起一个 GUI 进程，所以调用方
    需要靠本函数判断「目标工程是否已经在某个实例里打开」，避免重复起实例。

    Returns:
        [{"pid": int, "exe": str, "project": str, "cmdline": str}, ...]
        project 为命令行里带的 .sem 路径（未带工程时为空串）。
    """
    procs: list[dict[str, Any]] = []
    try:
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in ("laspcem.exe", "laspcemapp.exe"):
                    continue
                cmdline = list(proc.info.get("cmdline") or [])
                procs.append({
                    "pid": proc.info.get("pid"),
                    "exe": proc.info.get("exe") or "",
                    "project": _project_arg(cmdline),
                    "cmdline": " ".join(cmdline),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
    except (psutil.AccessDenied, OSError):
        pass
    return procs


def laspcem_is_running() -> bool:
    """LASPCEM 是否已在运行（按进程名匹配 laspcem.exe / LaspcemApp.exe）。"""
    return bool(find_laspcem_processes())