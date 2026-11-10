"""LASPCEM 工具 — 启动软件 / 打开 .sem 工程 / 跑仿真。

LASPCEM 无 COM/gRPC 接口，其唯一程序化入口是内嵌 Python 脚本 API（app/cem/viewer），
等价于 HFSS 的 COM Analyze：跑一个脚本（app.Open → cem.FEMAnalyze → app.SaveAs）。
⚠️ 实测（2026-11-10）：laspcem.exe 没有可用的 headless/后台开关，脚本只能在 GUI 里
File → Script → Run Script 手工执行；命令行带 .sem 启动一律新起一个 GUI 实例（无进程间投递）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from servers import mcp
from servers.laspcem.config import find_laspcem_exe, find_laspcem_processes, laspcem_is_running
from servers.laspcem.script_gen import build_laspcem_script
from servers.laspcem.setup_reader import read_setup_report
from servers.utils import error_response, validate_file


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
    """在 LASPCEM 中打开 .sem 工程文件。

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


# 暂搁置不注册：LASPCEM 无 headless 求解接口，全自动需 GUI 自动化（脆），
# 当前只做到「生成脚本 + 打开工程」，最后 Run Script 一步未打通。需要时加回 @mcp.tool()。
def run_laspcem_simulation(
    sem_path: str,
    setup_name: str = "Setup1",
    script_path: str = "",
    open_project: bool = True,
) -> dict[str, Any]:
    """用 LASPCEM 对 .sem 工程跑仿真：生成通用脚本 + 在 LASPCEM 打开工程。

    通用：对任意已配置好 setup 的 .sem 工程，生成「打开工程 → 分析 setup → 保存」脚本
    （app.Open / cem.FEMAnalyze / app.SaveAs），工程路径和 setup 名都可传。LASPCEM 的自动化
    接口是 GUI 内 Python 脚本（控制台 / Run Script），无 headless 入口，故生成脚本后需在 GUI
    里 File → Script → Run Script 选择该脚本执行求解，结果自动 SaveAs 回工程。

    用法："用 LASPCEM 仿真这个工程"、"对 C:/demo.sem 的 Setup1 跑一次 FEM"

    Args:
        sem_path: .sem 工程文件绝对路径。
        setup_name: 要分析的 setup 名（工程里已配置好的），默认 "Setup1"。
        script_path: 脚本输出路径，默认写到 .sem 同目录下 <工程名>_run.py。
        open_project: 是否在 LASPCEM 中打开该工程（默认 True）。

    Returns:
        {"success": True, "script_path": "...", "sem_path": "...", "setup_name": "...", "project_opened": bool, "message": "..."}
    """
    try:
        resolved = validate_file(sem_path, (".sem",))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    out = Path(script_path) if script_path.strip() else (Path(resolved).with_name(Path(resolved).stem + "_run.py"))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_laspcem_script(resolved, setup_name=setup_name), encoding="utf-8")
    except OSError as exc:
        return error_response("WRITE_FAILED", f"无法写入脚本: {exc}")

    project_opened = False
    if open_project:
        project_opened = bool(open_laspcem_project(resolved).get("success"))

    return {
        "success": True,
        "script_path": str(out),
        "sem_path": resolved,
        "setup_name": setup_name,
        "project_opened": project_opened,
        "message": (f"已生成脚本 {out}；"
                    f"在 LASPCEM 里 File → Script → Run Script 选择该脚本执行求解，跑完自动保存回 {resolved}"),
    }


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
    if unresolved:
        out["warning"] = (f"{len(unresolved)} 个 double 型字段在 .sem 记录里只有引用序号、无数值，"
                          f"已置 null：{', '.join(unresolved)}；真实值以 LASPCEM GUI 的 setup 为准")
    return out