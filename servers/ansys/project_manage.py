"""ANSYS HFSS 工程管理 — 打开/关闭/启动 AEDT，COM 附着操作。

open_hfss_project: 锁文件检查 → COM 附着打开或 subprocess 启动
close_hfss_project: COM 关闭 → 等待锁文件释放 → 清理残留锁
launch_aedt: 已运行则返回状态，否则 subprocess 启动
get_hfss_project_info: 只读查询，不启动 AEDT
"""

from __future__ import annotations

import atexit
import psutil
import subprocess
import time
from pathlib import Path
from typing import Any

from servers.ansys.config import (
    AEDT_PATH, _AEDT_LOCK,
    aedt_is_running, get_aedt_pids, query_desktop_state,
    cleanup_stale_project_lock, get_project_lock_path,
    _attach_aedt, com_session, logger,
)
from servers.utils import validate_file, error_response
from servers import mcp

_LAST_PID: int | None = None  # MCP 最后一次启动的 AEDT 进程 PID
_OPEN_PROJECT_PATHS: dict[str, str] = {}  # project_name -> project_path

#: 本进程 Popen 起来、尚未回收的 AEDT 句柄（持有对象才能 terminate；只存 PID 不够）
_LAUNCHED: list[Any] = []


def _register_launched(proc: Any) -> None:
    """登记自启动进程；顺手剔除已退出的，避免长期运行列表只增不减。"""
    _LAUNCHED[:] = [p for p in _LAUNCHED if p.poll() is None]
    _LAUNCHED.append(proc)


def _terminate_launched() -> None:
    """退出时回收自己拉起的 AEDT：先 terminate，5 s 未退再 kill。

    只处理 `_LAUNCHED` 里的（= MCP 自己 Popen 的），用户手动启动的实例不受影响。
    进程被强杀（taskkill /F）时 atexit 不触发 —— 那种情况仍只能靠 close_hfss_project。
    """
    for proc in list(_LAUNCHED):
        if proc.poll() is not None:
            continue
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            logger.info("回收自启动 AEDT：PID=%s", proc.pid)
        except Exception:  # noqa: BLE001 — 退出路径不能反过来抛
            logger.debug("回收 AEDT 失败：PID=%s", getattr(proc, "pid", "?"), exc_info=True)


atexit.register(_terminate_launched)


def _com_open_project(project_path: str) -> dict[str, Any]:
    """在现有 AEDT 中通过 COM 打开工程。"""
    with com_session():
        try:
            _, desktop = _attach_aedt()
            project_name = Path(project_path).stem

            if project_name in list(desktop.GetProjectList()):
                desktop.SetActiveProject(project_name)
                return {"status": "already_open", "project_name": project_name}

            result = desktop.OpenProject(project_path)
            if result is not None or project_name in list(desktop.GetProjectList()):
                return {"status": "opened", "project_name": project_name}

            return {"status": "COM_OPEN_FAILED", "project_name": project_name,
                    "error": "OpenProject returned None and project not in list"}
        except Exception as exc:
            logger.exception("COM OpenProject failed")
            return {"status": "COM_OPEN_FAILED", "project_name": Path(project_path).stem,
                    "error": str(exc)}


@mcp.tool()
def open_hfss_project(
    project_path: str,
    aedt_path: str = "",
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """启动 AEDT 并打开 .aedt 项目（COM 附着优先，subprocess 单次启动兜底）。

    流程：检查锁文件→清理失效锁→COM 附着打开或 subprocess 启动→轮询确认工程打开
    用法："帮我打开 C:/demo.aedt"、"在 AEDT 中打开这个 HFSS 项目"

    Args:
        project_path: .aedt 项目文件绝对路径。
        aedt_path: AEDT（ansysedt.exe）路径，默认自动检测。
        timeout_seconds: 等待工程打开的超时秒数，默认 30（1-120）。

    Returns:
        {"success": True, "status": "opened/already_open", "project_opened": True,
         "method": "com/subprocess", "duration_s": 1.2}
    """
    t0 = time.monotonic()
    timeout_seconds = max(1, min(timeout_seconds, 120))

    try:
        resolved = validate_file(project_path, (".aedt", ".aedtz"))
    except (FileNotFoundError, ValueError) as exc:
        return error_response("INVALID_PATH", str(exc))

    exe = aedt_path or AEDT_PATH
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        return error_response("AEDT_NOT_FOUND", str(exe_path))
    if exe_path.name.lower() != "ansysedt.exe":
        return error_response("INVALID_AEDT_PATH", "必须以 ansysedt.exe 结尾")

    project_name = Path(resolved).stem
    global _LAST_PID

    # 检查工程锁
    lock_result = cleanup_stale_project_lock(resolved)
    if not lock_result["removed"] and lock_result.get("lock_pid"):
        if lock_result.get("status") == "lock_active":
            return error_response("PROJECT_LOCKED",
                                  f"工程正在 AEDT PID={lock_result['lock_pid']} 中使用",
                                  lock_pid=lock_result["lock_pid"])

    with _AEDT_LOCK:
        aedt_was_running = aedt_is_running()

        if aedt_was_running:
            state = query_desktop_state()
            if not state["connected"]:
                logger.warning("AEDT running but COM attach failed: %s", state.get("error"))
                return error_response("EXISTING_INSTANCE_COM_UNAVAILABLE",
                                      "AEDT 已运行但无法附着 COM，未创建第二个实例",
                                      aedt_running=True, project_opened=False)

            if project_name in state["projects"]:
                attach_error = None
                with com_session():
                    try:
                        _, dtop = _attach_aedt()
                        dtop.SetActiveProject(project_name)
                    except Exception as exc:
                        attach_error = str(exc)
                        logger.warning("SetActiveProject 失败 (%s): %s", project_name, exc)
                if attach_error is not None:
                    # 工程已在 AEDT 中列出但激活失败，不能假装成功
                    return error_response("COM_OPEN_FAILED", "工程已在 AEDT 中列出，但激活失败，请重试",
                                          aedt_running=True, project_opened=False,
                                          project_name=project_name, project_path=resolved,
                                          method="com", com_error=attach_error)
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": "already_open",
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "com", "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"工程已打开并激活: {project_name}",
                    **({"stale_lock_removed": True} if lock_result["removed"] else {}),
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
            return error_response("COM_OPEN_FAILED", "AEDT 已运行，但打开工程失败",
                                  aedt_running=True, project_opened=False, verified=True,
                                  project_name=project_name, project_path=resolved,
                                  method="com", com_error=result.get("error"))

        # AEDT 未运行，单次启动
        try:
            proc = subprocess.Popen([str(exe_path), resolved], cwd=str(exe_path.parent))
            _LAST_PID = proc.pid
            _register_launched(proc)
            logger.info("Launched AEDT PID=%d for %s", proc.pid, project_name)
        except Exception as exc:
            logger.exception("AEDT launch failed")
            return error_response("LAUNCH_FAILED", str(exc))

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return error_response("PROCESS_EXITED", f"AEDT 进程已退出，退出码: {proc.returncode}",
                                      aedt_running=False, project_opened=False)
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


_SAME_LAUNCH_WINDOW_SECONDS = 30.0
"""认「同一批启动」的时间**上界**（秒）。

启动器进程与它拉起的 `-Embedding` GUI 进程在本机实测只差 0.01 s（2026-11-16），但慢机器 /
网络许可下 `-Embedding` 可能要几十秒才出现 —— 窗口太小会漏杀、把孤儿留在后台（回到重构前的坑）。
所以窗口放宽，改由 `_is_same_launch()` 里的**特征判据**决定是不是同批，而不是只靠时间。
"""

_EMBEDDING_SLACK_SECONDS = 5.0
"""不带 `-Embedding` 特征时，允许的最大时间差（秒）。

`-Embedding` 是 AEDT 被 COM / 自动化拉起时的命令行标志，启动器分裂出的 GUI 进程一定带它；
用户手工双击启动的 AEDT 不带。用它替代「纯时间窗口」：慢启动也认得出，几分钟前手动开的
实例又不会被卷进来。
"""

_LAUNCH_CLOCK_SKEW_SECONDS = 2.0
"""允许 `create_time` 略早于 `_LAST_PID`（采样 / 时钟误差），负向不超过该值。"""


def _is_same_launch(info: dict[str, Any], t0: float) -> bool:
    """判断一条进程记录是否与 `t0` 那次启动同批。

    条件：`create_time` 落在 `[t0 - skew, t0 + window]`，且满足其一 ——
      ① 与 `t0` 相差 ≤ `_EMBEDDING_SLACK_SECONDS`（启动瞬间分裂出的进程）；
      ② 命令行含 `-Embedding`（自动化拉起的 GUI，可能晚几十秒才起）。

    ⚠️ 局限：别的自动化工具（如 pyaedt）启动的 AEDT 也带 `-Embedding`，若在窗口内启动会被
    归为同批。要绝对隔离只能按「启动器 PID 的子孙 + 它拉起的那一个」精确追踪，代价高且
    AEDT 分裂后父子关系已断（实测 ppid 互不相干）。
    """
    delta = (info.get("create_time") or 0.0) - t0
    if delta < -_LAUNCH_CLOCK_SKEW_SECONDS or delta > _SAME_LAUNCH_WINDOW_SECONDS:
        return False
    if delta <= _EMBEDDING_SLACK_SECONDS:
        return True
    return "-Embedding" in " ".join(info.get("cmdline") or [])


def _resolve_close_targets() -> tuple[list[int], str]:
    """返回 `(关闭目标 PID, 范围标识)`：范围是 `"managed"`（MCP 自己启动的那批）或 `"all"`。

    优先 MCP 自己启动的那一批 ansysedt.exe（含它分裂出的 `-Embedding` 进程）；
    没记录（或记录已失效）时退化为**机器上所有** ansysedt.exe —— 调用方必须把这个
    范围如实回给用户，别让人以为只关了自己起的那个。
    """
    global _LAST_PID
    if _LAST_PID is not None:
        try:
            proc = psutil.Process(_LAST_PID)
            if proc.is_running() and proc.name().lower() == "ansysedt.exe":
                try:
                    t0 = proc.create_time()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    t0 = None
                pids = [_LAST_PID]
                # AEDT 启动后会分裂成**互不为父子**的多个 ansysedt.exe：
                #   启动器进程（Popen 直接起的，_LAST_PID） + 它拉起的 `-Embedding` GUI 进程。
                # 2026-11-16 实测：ppid 分别 18080 / 1632，create_time 只差 0.01 s ——
                # 所以 `children()` 抓不到它，只认 _LAST_PID 会留下孤儿进程（实测杀 31348 后
                # 32576 残留）。同批判据见 `_is_same_launch()`：时间上界 30 s + `-Embedding`
                # 特征 —— 慢启动也能认出来，又不会卷进几分钟前手动开的实例。
                if t0 is not None:
                    for other in psutil.process_iter(["pid", "name", "create_time", "cmdline"]):
                        info = other.info
                        if info["pid"] in pids:
                            continue
                        if (info["name"] or "").lower() != "ansysedt.exe":
                            continue
                        try:
                            if _is_same_launch(info, t0):
                                pids.append(info["pid"])
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                return pids, "managed"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        _LAST_PID = None
    return get_aedt_pids(), "all"


def _taskkill(pid: int, force: bool) -> tuple[bool, str]:
    """taskkill 单个 PID。force=True 带 /F（硬杀）。返回 (是否成功, 失败原因)。"""
    args = ["taskkill", "/F", "/PID", str(pid)] if force else ["taskkill", "/PID", str(pid)]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=10,
                                   check=False, errors="replace")
    except subprocess.TimeoutExpired:
        return False, "taskkill 超时"
    if completed.returncode != 0:
        return False, completed.stderr.strip() or completed.stdout.strip()
    try:
        psutil.Process(pid).wait(timeout=10)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        return False, f"PID {pid} 未在 10s 内退出"
    return True, ""


def _kill_aedt_processes(pids: list[int], lock_path: str, project_name: str,
                         hard: bool, scope: str = "managed") -> dict[str, Any]:
    """进程级关闭 AEDT。hard=True 直接 /F；hard=False 先 taskkill（WM_CLOSE）再 /F 兜底。

    `scope` 只用于返回体：告诉调用方这次终止的是「MCP 自己起的」（`managed`）还是
    「机器上所有 AEDT」（`all`）—— 后者会连用户手动开的实例一起关掉。
    """
    global _LAST_PID

    killed: list[int] = []
    failed: list[dict[str, Any]] = []
    for pid in pids:
        ok, reason = _taskkill(pid, force=hard)
        if not ok and not hard:
            ok, reason = _taskkill(pid, force=True)   # 优雅失败 → 硬杀
        if ok:
            killed.append(pid)
        else:
            failed.append({"pid": pid, "reason": reason})

    if _LAST_PID is not None and _LAST_PID in killed:
        _LAST_PID = None

    lock_cleanup = {}
    if killed and lock_path:
        time.sleep(1)
        if get_project_lock_path(lock_path).is_file():
            result = cleanup_stale_project_lock(lock_path)
            if result["removed"]:
                lock_cleanup = {"lock_cleanup": result}

    if project_name in _OPEN_PROJECT_PATHS:
        del _OPEN_PROJECT_PATHS[project_name]

    if failed:
        return error_response("CLOSE_FAILED", f"部分 AEDT 进程未能终止：{failed}",
                              killed=killed, method="taskkill", scope=scope)
    return {"success": True, "method": "taskkill",
            "message": f"已终止 AEDT 进程 PID {killed}"
                       + ("（含非 MCP 启动的 AEDT 实例）" if scope == "all" else ""),
            "scope": scope, **lock_cleanup}


def _aedt_modal_dialogs() -> list[str]:
    """枚举 AEDT 进程的可见对话框窗口标题（Win32 `#32770` 标准对话框类）。

    AEDT 的「是否保存改动」确认框就是标准对话框窗口 —— COM 的 `CloseProject` 撞上它会
    阻塞或抛异常。这里主动查一次，好让 `CLOSE_FAILED` 能明确回答"是不是弹框了"，
    而不是让调用方猜（2026-11-16）。
    返回空列表 = 没检测到（或枚举失败），不影响关闭主流程。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                   # noqa: BLE001 —— 非 Windows 环境不阻塞
        return []

    pids = set(get_aedt_pids())
    if not pids:
        return []

    titles: list[str] = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):                             # Win32 回调签名固定
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value not in pids or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770":                       # 标准对话框类（消息框/确认框）
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            titles.append(buf.value or "(无标题对话框)")
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:                                   # noqa: BLE001 —— 枚举失败不影响关闭流程
        return []
    return titles


def _com_close_project(project_name: str, save_before_close: bool,
                       lock_path: str) -> dict[str, Any] | None:
    """COM 优雅关闭指定项目。

    返回 `None` **仅表示附着阶段失败**（连不上 AEDT），调用方走进程级兜底。
    附着成功但 `CloseProject` 失败时返回 `error_response`，**绝不**兜底硬杀 ——
    那多半是「项目有未保存改动 / AEDT 弹了确认框」，硬杀会把改动一起丢掉
    （2026-11-16 复核：原实现一个大 try 把两种失败混在一起，导致关项目被拒也强杀进程）。
    """
    try:
        with com_session():
            # ── 阶段 1：附着（失败可兜底到进程级）──
            try:
                _, desktop = _attach_aedt()
                names = list(desktop.GetProjectList())
            except Exception as exc:                # noqa: BLE001 —— 附着失败才允许兜底硬杀
                logger.exception("COM attach failed, fallback to process kill: %s", exc)
                return None

            # ── 阶段 2：已附着成功，这里任何失败都返回错误，不升级为硬杀 ──
            try:
                # 为空时关闭活动项目（而非 GetProjectList 的第一个，顺序不保证活动项目在首位）
                if project_name.strip():
                    target = project_name.strip()
                else:
                    target = ""
                    try:
                        active = desktop.GetActiveProject()
                        if active is not None:
                            target = active.GetName()
                    except Exception:
                        pass
                    if not target:
                        target = names[0] if names else ""
                if not target:
                    return error_response("NO_PROJECT_TO_CLOSE", "没有可关闭的项目")

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

                _OPEN_PROJECT_PATHS.pop(target, None)

                return {
                    "success": closed, "method": "com", "project_closed": closed,
                    "message": f"已关闭: {target}" if closed else f"未能确认关闭: {target}",
                    **lock_cleanup,
                }
            except Exception as exc:                # noqa: BLE001 —— 已附着，记录后交给调用方决定是否 force
                logger.exception("CloseProject failed (attached OK): %s", exc)
                # 主动查一次弹框：AEDT 的「是否保存改动」确认框会让 CloseProject 撞墙，
                # 这种失败跟「工具坏了」完全不同 —— 要明确告诉调用方去点那个框。
                dialogs = _aedt_modal_dialogs()
                if dialogs:
                    msg = (
                        f"COM 已连通但关闭项目失败：{exc}。"
                        f"检测到 AEDT 上有 {len(dialogs)} 个对话框窗口正等待处理：{dialogs} —— "
                        f"很可能就是「是否保存改动」的确认框；请在 AEDT 窗口里处理掉它，再重试关闭"
                    )
                else:
                    msg = (
                        f"COM 已连通但关闭项目失败：{exc}。"
                        f"未检测到 AEDT 对话框 —— 最常见原因是项目有未保存改动被拒绝关闭"
                    )
                return error_response(
                    "CLOSE_FAILED",
                    msg,
                    hint="先看 AEDT 窗口有没有「保存 / 不保存」确认框并处理掉；"
                         "若确认改动可以直接丢弃，再改传 force=true 强制结束进程",
                    attached=True,
                    modal_dialogs=dialogs,
                )
    except Exception as exc:                     # noqa: BLE001 —— com_session 内部异常才兜底
        logger.exception("com_session failed, fallback to process kill: %s", exc)
        return None


@mcp.tool()
def close_hfss_project(
    project_name: str = "",
    project_path: str = "",
    save_before_close: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """⚠️ 关闭 AEDT 项目（未保存改动会丢；force 会强杀进程）。

    关闭三层兜底（从优雅到粗暴）：COM `CloseProject` → `taskkill`（发 WM_CLOSE）→ `taskkill /F`。
    COM 只关指定项目；COM 附着失败才退回进程级兜底（kill 整个 AEDT，会关掉它名下所有项目）。

    ⚠️ **非 force 也可能硬杀**：进程级兜底先发 WM_CLOSE，10 s 内没退出会自动补 `taskkill /F`。
    这时若 AEDT 正弹「是否保存」确认框，改动会随进程一起丢 —— `save_before_close` 只作用于
    COM 分支，兜底路径不受它保护。别把 `force=False` 当成「一定不会丢改动」。

    `force=True` 的作用范围：MCP 记录到自己启动过 AEDT 时只终止那一批（启动器 + 它分裂出的
    `-Embedding` 进程，按创建时间 ±5 s 认同批）；**没有记录时终止机器上所有 `ansysedt.exe`**，
    包括手动开的实例 —— 返回体的 `scope` 会写明是 `managed` 还是 `all`。

    ⚠️ 跨权限（UAC 隔离）时 DCOM / UIPI / 进程权限会依次拦截这三层，需 MCP 与 AEDT 同权限运行。

    Args:
        project_name: 项目名，为空关闭活动项目。
        project_path: 项目路径，用于清理锁文件。
        save_before_close: 关闭前保存（**仅 COM 分支生效**；进程级兜底没有保存机会）。
        force: True 跳过 COM / WM_CLOSE，直接 `taskkill /F` 硬杀。
    """
    lock_path = project_path or _OPEN_PROJECT_PATHS.get(project_name, "")

    target_pids, scope = _resolve_close_targets()

    if force:
        if not target_pids:
            return error_response("NO_MANAGED_PROCESS", "没有可终止的 AEDT 进程")
        return _kill_aedt_processes(target_pids, lock_path, project_name, hard=True, scope=scope)

    # ── 1) COM 优雅关闭 ──
    if aedt_is_running():
        result = _com_close_project(project_name, save_before_close, lock_path)
        if result is not None:
            return result

    # ── 2) 进程级兜底 ──
    if not target_pids:
        return error_response(
            "CLOSE_FAILED",
            "COM 关闭失败，且未找到可终止的 AEDT 进程",
            hint="确认 AEDT 是否在运行；若跨权限（UAC 隔离），需 MCP 与 AEDT 同权限运行",
        )
    return _kill_aedt_processes(target_pids, lock_path, project_name, hard=False, scope=scope)


@mcp.tool()
def launch_aedt(
    aedt_path: str = "",
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """启动 AEDT（不打开项目）。已运行时跳过启动、返回状态（含 COM 就绪）。

    用法："启动 AEDT"、"打开 AEDT 桌面"

    只启动 ansysedt.exe 进程，不打开任何 .aedt 工程；已在运行时跳过启动直接返回状态。

    Args:
        aedt_path: AEDT（ansysedt.exe）路径，默认自动检测（注册表/默认目录）。
        timeout_seconds: 等待 COM 就绪的超时秒数，默认 30（1-120）。

    Returns:
        {"success": True, "status": "started/already_running",
         "com_ready": bool, "pid": int | None, "pids": [int], "message": "..."}

    `success=True` 只代表**进程层面**完成（ansysedt.exe 在跑）；能不能用 COM 要看
    `com_ready`。两个分支都会按 `timeout_seconds` 等 COM 就绪，且都返回 `pid`/`pids`。
    """
    timeout_seconds = max(1, min(timeout_seconds, 120))
    global _LAST_PID

    exe = aedt_path or AEDT_PATH
    exe_path = Path(exe).expanduser()
    if not exe_path.is_file():
        return error_response("AEDT_NOT_FOUND", str(exe_path))
    if exe_path.name.lower() != "ansysedt.exe":
        return error_response("INVALID_AEDT_PATH", "必须以 ansysedt.exe 结尾")

    def _wait_com(deadline: float) -> bool:
        """在 deadline 前轮询 COM 是否就绪（新起进程 / 已有进程共用同一判据）。"""
        while True:
            if query_desktop_state()["connected"]:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(1)

    already = False
    pids: list[int] = []
    proc = None
    with _AEDT_LOCK:
        if aedt_is_running():
            already = True
            pids = get_aedt_pids()
        else:
            try:
                proc = subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
                _LAST_PID = proc.pid
                _register_launched(proc)
            except Exception as exc:
                return error_response("LAUNCH_FAILED", str(exc))

    # 释放锁后再等 COM：① 已运行也要等（AEDT 刚启动/加载时 com_ready 短暂为 False，
    # 只查一次会让调用方误判「不可用」）；② 等待不占 _AEDT_LOCK，避免阻塞 open/close/query
    # 等其它 AEDT 工具（2026-11-16 复核）。
    ready = _wait_com(time.monotonic() + timeout_seconds)

    if already:
        return {"success": True, "status": "already_running", "com_ready": ready,
                "pid": pids[0] if pids else None, "pids": pids,
                "message": ("AEDT 已在运行，COM 就绪" if ready
                            else "AEDT 已在运行，但 COM 未在时间内就绪")}
    return {"success": True, "status": "started", "com_ready": ready,
            "pid": proc.pid, "pids": get_aedt_pids() or [proc.pid],
            "message": ("AEDT 已启动，COM 就绪" if ready
                        else "AEDT 已启动，COM 未在时间内就绪")}


@mcp.tool()
def get_hfss_project_info() -> dict[str, Any]:
    """查询当前 AEDT 打开的项目和活动设计（纯查询，不启动 AEDT）。

    用法："AEDT 现在打开了哪些工程"、"当前活动的 HFSS 设计是哪个"

    只读查询，不启动、不附着 AEDT。返回已打开项目列表、活动项目/设计名、
    AEDT 进程 PID 和 COM 可用性。查 design_name/setup_name 时优先用它。

    Returns:
        {"success": True, "aedt_running": bool, "pids": [...],
         "com_available": bool, "open_projects": [...],
         "active_project": str, "active_design": str}
    """
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
