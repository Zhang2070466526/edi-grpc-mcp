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
from win32com.client import Dispatch, GetActiveObject

load_dotenv()
from servers.settings import get_settings  # noqa: E402 — 必须在 load_dotenv 之后
from servers.ansys import aedt_procs  # noqa: E402 — 纯 stdlib+psutil，与 com_child 共用

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


def _progids_to_try(infos: dict[int, dict] | None = None) -> tuple[str, ...]:
    """本次附着要试的 ProgID 顺序：**运行实例的版本化 ProgID 优先**，再回退裸名。

    2026-11-26 真机（现场机 star）：裸 `Ansoft.ElectronicsDesktop` 走注册表 CurVer 指到
    AEDT 2024.2，而用户看着的 GUI 是 2020 R2 → `Dispatch` 连到另一个版本实例，
    所有查询都像"未响应"。AEDT 另外注册了 `Ansoft.ElectronicsDesktop.2020.2`（独立 CLSID），
    按运行实例版本先试它就能连对。

    ⚠️ 版本化 ProgID **只在对应版本实例确实在跑时**才加入：`Dispatch` 在该版本不存在时
    会新建一个 AEDT，违反"只附着、绝不创建"。

    `infos` 缺省时内部枚举一次；调用方已枚举过就传进来，省一次 psutil 全表扫描。
    """
    try:
        versioned = aedt_procs.progid_candidates(infos)
    except Exception:                               # noqa: BLE001 —— 枚举失败就只试裸名
        versioned = []
    extra = tuple(p for p in versioned if p not in _COM_PROGIDS)
    return extra + tuple(_COM_PROGIDS)


def com_version_info() -> dict[str, Any]:
    """运行实例版本 / CurVer 版本 / 是否存在版本失配（给工具层做自证字段）。"""
    try:
        return aedt_procs.version_report()
    except Exception as exc:                        # noqa: BLE001 —— 只读诊断，绝不抛
        return {"error": str(exc)}


def _connected_pid(desktop) -> int | None:
    """COM 连到的 AEDT 进程 PID（Desktop.GetProcessID）；拿不到就 None（不阻塞附着）。"""
    # 空壳判定/清理已收敛到 servers/ansys/aedt_procs.py（config 与 com_child 共用，不再两处同步）。
    try:
        return int(desktop.GetProcessID())
    except Exception:                                   # noqa: BLE001
        return None


def _kill_embedding_shell(pid: int) -> bool:
    """Kill 一个 `-Embedding` 空壳（实现收敛在 aedt_procs，两处共用）。

    2026-11-25 真机实测：GUI 在跑时 `Dispatch` 仍会连到 AEDT 自带的空壳（PID 41776），
    杀掉落空壳后同一秒 `Dispatch` 就连到 GUI（com_available=true、读到 Project27）。
    空壳不持有工程，且杀它不影响 GUI —— 这是让附着能到达 GUI 的唯一有效手段。
    """
    return aedt_procs.kill_embedding_shell(pid, psutil)


def _attach_aedt():
    """附着现有 AEDT 实例，返回 (app, desktop)，失败抛异常。**绝不创建新实例。**

    全程持 _AEDT_LOCK：worker 线程、refresh_from_aedt、query_desktop_state 等多条
    调用路径都走这里，串行化 COM 附着避免并发操作 AEDT 单实例桌面。

    ⚠️ 两条通道都要试（2026-11-16 本机 AEDT 2020 R2 实测）：

    1. `GetActiveObject(progid)` —— 只查 ROT，最快，且绝不新建实例。但 **AEDT 2020 R2
       不把对象注册到 ROT**，两个 PROGID 都返回 `MK_E_UNAVAILABLE`（"操作无法使用"），
       AEDT 明明在跑（GUI 已加载、Responding=True）也附着不上。
       ⚠️ 三种启动方式实测都不注册 ROT（2026-11-16，本机 2020 R2）：
         无参数 / `-Embedding`（EDI 嵌入模式）/ `-Automation` —— `GetActiveObject`
         全部 `MK_E_UNAVAILABLE`，而 `Dispatch` 全部能连。所以**不要试图靠换启动参数
         修这个问题**，只有 Dispatch 通道走得通。
    2. `Dispatch(progid)` —— 走 CLSID 找已运行的 COM 服务器，实测 0.0 s 连上现有 pid、
       进程数不变、`GetProjectList()` 正常。**但它在本实例不存在时会新建一个**，
       所以必须先 `aedt_is_running()` 守卫，否则违反"绝不创建新实例"的约定。

    取到对象后还要探活（`GetProjectList()`）：现场机（star）曾出现"对象拿到了但方法全炸"
    的瘦壳情形，探不通就换下一个 PROGID。
    """
    with _AEDT_LOCK:
        try:
            infos = aedt_procs.instances()          # 一次枚举，全程复用（省掉 aedt_is_running /
        except Exception as exc:                    # noqa: BLE001  progid_candidates/空壳判定的 3 次 psutil 全表扫描）
            # ⚠️ 不许把"枚举失败"伪装成"AEDT 未运行"：2026-11-25 实测过（uv base python 缺 psutil
            # 时）被这个假象带偏排查方向。失败就如实抛，并保留原始异常。
            raise RuntimeError(f"枚举 AEDT 进程失败（不是「未运行」）：{exc}") from exc
        if not infos:
            raise RuntimeError(
                "AEDT 未运行：_attach_aedt 只附着现有实例，绝不创建新实例"
                "（需要新建请用 launch_aedt）"
            )
        is_embedding = lambda pid: aedt_procs.is_embedding(infos, pid)  # noqa: E731
        errors: list[str] = []
        progids = _progids_to_try(infos)
        for progid in progids:
            for name, getter in (("GetActiveObject", GetActiveObject), ("Dispatch", Dispatch)):
                # 最多两次：第一次撞到空壳就先清掉再重连一次（实测清完即连上 GUI）
                for attempt in (1, 2):
                    try:
                        app = getter(progid)
                        desktop = app.GetAppDesktop()
                        list(desktop.GetProjectList())      # 探活：瘦壳 / 空壳在这里抛
                        pid = _connected_pid(desktop)
                        if pid and is_embedding(pid):
                            # 空壳 GetProjectList() 返回 [] 且不抛 → 探活抓不到，必须按 PID 认。
                            if attempt == 1 and aedt_procs.gui_pids(infos) and _kill_embedding_shell(pid):
                                errors.append(
                                    f"{progid} via {name}: 连到 -Embedding 空壳(PID {pid})，已清掉，重试")
                                continue
                            errors.append(f"{progid} via {name}: 连到的 PID {pid} 是 -Embedding 空壳，跳过")
                            break
                        logger.info("COM attached: progid=%s pid=%s", progid, pid)
                        return app, desktop
                    except Exception as exc:                # noqa: BLE001 —— 换下一通道/PROGID
                        errors.append(f"{progid} via {name}: {exc}")
                        break
        reaped = aedt_procs.reap_orphan_shells(list(infos), is_embedding, psutil)
        if reaped:
            raise RuntimeError(
                f"只连得到残留的 -Embedding 空壳（PID {reaped}，无 GUI 实例），已清理；"
                f"重新 launch_aedt / open_hfss_project 即可")
        raise RuntimeError(f"attached none of {progids}: {' | '.join(errors)}")


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
            return design.GetModule(mod_name)
        except Exception:
            continue
    raise RuntimeError("No AnalysisSetup or SolveSetups module found")


# -- 工程锁文件 --
def get_project_lock_path(project_path: str) -> Path:
    """C:/demo.aedt -> C:/demo.aedt.lock；C:/demo.aedtz -> C:/demo.aedtz.lock"""
    return Path(str(project_path) + ".lock")


def read_project_lock_pid(lock_path: Path) -> int | None:
    """读取 .aedt.lock 文件中的 DesktopProcessID。

    ⚠️ 真实锁文件那一行是**带制表符缩进**的（`\\tDesktopProcessID=19164`，AEDT 2020 实测），
    所以必须 `strip()` 后再匹配 —— 2026-11-18 发现原来用 `startswith` 直接比对，
    对真实锁永远返回 None（导致「锁在用中」判定一直失效）。
    """
    if not lock_path.is_file():
        return None
    try:
        for line in lock_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
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
