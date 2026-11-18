"""ANSYS HFSS 工程管理 — 打开/关闭/启动 AEDT，COM 附着操作。

open_hfss_project: 锁文件检查 → COM 附着打开或 subprocess 启动
close_hfss_project: COM 关闭 → 等待锁文件释放 → 清理残留锁
launch_aedt: 已运行则返回状态，否则 subprocess 启动
get_hfss_project_info: 只读查询，不启动 AEDT
"""

from __future__ import annotations

import atexit
import psutil
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from servers.ansys.config import (
    AEDT_PATH, _AEDT_LOCK,
    aedt_is_running, get_aedt_pids, query_desktop_state,
    cleanup_stale_project_lock, get_project_lock_path,
    read_project_lock_pid,
    _attach_aedt, com_session, logger,
)
from servers.utils import validate_file, error_response
from servers import mcp

_LAST_PID: int | None = None  # MCP 最后一次启动的 AEDT 进程 PID
_OPEN_PROJECT_PATHS: dict[str, str] = {}  # project_name -> project_path

#: 单次 COM 读调用的等待上限（秒）。AEDT（未响应）时会一直不返回 —— 实测挂过 300s+，
#: 把 agent 的调用拖死；这里放弃等待并回结构化错误。COM 本身无法中断，只能不等人。
_COM_READ_TIMEOUT = 10.0
#: 关闭动作的等待上限（秒）：CloseProject 正常 <2s，超过就是实例卡住。
_COM_CLOSE_TIMEOUT = 30.0


def _call_with_timeout(fn, timeout_s: float) -> tuple[bool, Any, str | None]:
    """在守护线程里跑 `fn`，超时放弃等待。

    COM 调用遇到（未响应）实例不会抛异常也不会返回，只能在调用方侧等超时 ——
    线程泄漏一个（daemon），但服务不会被拖死。
    """
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:                    # noqa: BLE001 —— 原样带回给调用方
            box["error"] = exc

    th = threading.Thread(target=_run, daemon=True, name="aedt-com-call")
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        return False, None, f"COM 调用超过 {timeout_s:.0f}s 未返回（AEDT 可能（未响应））"
    if "error" in box:
        return False, None, str(box["error"])
    return True, box.get("value"), None


def _aedt_instance_groups() -> list[list[int]]:
    """把 `ansysedt.exe` 归组成**实例**：一个实例 = 启动器 + 它派生的 `-Embedding` 进程。

    ⚠️ 不能按 `create_time` 归组：2026-11-18 真机 45920（启动器 16:54）与 35932
    （`-Embedding`，17:04 才被 COM 拉起，晚 10 分钟）被判成 2 个实例 → open/close/quit
    全被 `MULTI_INSTANCE_UNSUPPORTED` 挡死。
    改按命令行特征：带 `-Embedding` 的是辅助进程，**不带**的才是实例本体；
    机器上只有 `-Embedding`（无启动器）时整体算 1 个实例。
    """
    try:
        infos = [p.info for p in psutil.process_iter(["name", "pid", "create_time", "cmdline"])
                 if p.info.get("name") == "ansysedt.exe"]
    except Exception:                               # noqa: BLE001 —— 枚举失败按"不知道"处理
        return []

    def _is_helper(info: dict[str, Any]) -> bool:
        return "-embedding" in " ".join(info.get("cmdline") or []).lower()

    launchers = sorted((i for i in infos if not _is_helper(i)),
                       key=lambda i: i.get("create_time") or 0)
    if launchers:
        # 只按启动器计实例；辅助进程不归组（无法可靠判断它属于哪个实例 —— ppid 已断）
        return [[i["pid"]] for i in launchers]
    helpers = [i["pid"] for i in infos if _is_helper(i)]
    return [helpers] if helpers else []             # 只剩辅助进程 → 整体算 1 个实例


def _instance_guard(action: str) -> dict[str, Any] | None:
    """多个 AEDT **实例**时拒绝按工程名操作（AEDT 2020 不注册 ROT，COM 无法按 PID 定向）。

    2026-11-18 决策：宁可明确报错，也不要"随便连一个实例"然后关错/开错工程。
    注意按"启动批次"数实例（启动器 + `-Embedding` 算同一个）。
    """
    groups = _aedt_instance_groups()
    if len(groups) <= 1:
        return None
    return error_response(
        "MULTI_INSTANCE_UNSUPPORTED",
        f"检测到 {len(groups)} 个 AEDT 实例（PID {groups}），无法定向{action}："
        f"AEDT 2020 不注册 ROT，COM 只能连到其中一个实例，做不到按 PID 选实例。",
        hint="先只保留目标实例（关掉多余的 AEDT 窗口）再重试；"
             "确认某个实例可整体丢弃时才用 quit_aedt(force=true)（会终结该实例下所有工程）",
        aedt_pids=[pid for g in groups for pid in g],
        instances=groups,
    )


def _clear_self_lock(project_path: str) -> dict[str, Any] | None:
    """清掉"自己锁自己"的残留锁；返回清理结果或 None。

    2026-11-18 实机复现：命令行带工程启动 AEDT 时，它先写 `.aedt.lock`
    （`DesktopProcessID` 记的是自己），随后又报
    `Project … is locked and cannot be opened`。因为锁里 PID 活着，
    `cleanup_stale_project_lock` 判定"在用中"不敢动，于是打开永远失败。

    判据：锁里的 PID 是本机活着的 ansysedt，但该工程**不在 COM 的 open_projects 里**
    → 判定自锁残留，把锁改名备份后返回（可还原）。
    """
    lock = get_project_lock_path(project_path)
    if not lock.is_file():
        return None
    pid = read_project_lock_pid(lock)
    if pid is None or pid not in get_aedt_pids():
        return None                                 # 死锁 / 读不到 PID：交给 cleanup_stale_project_lock
    name = Path(project_path).stem
    ok, state, _err = _call_with_timeout(query_desktop_state, 5.0)
    if not ok or name in ((state or {}).get("projects") or []):
        return None                                 # COM 不可用或工程确实在打开 → 不动
    backup = lock.with_name(lock.name + ".selflock.bak")
    try:
        if backup.exists():
            backup.unlink()
        lock.replace(backup)
    except OSError as exc:
        logger.warning("自锁清理失败（%s）: %s", lock, exc)
        return None
    logger.warning("已清理自锁残留：%s（PID=%s 活着但工程未打开）→ %s", lock, pid, backup)
    return {"removed": True, "lock_pid": pid, "reason": "self_lock", "backup": str(backup)}

#: 本进程 Popen 起来、尚未回收的 AEDT 句柄（持有对象才能 terminate；只存 PID 不够）
_LAUNCHED: list[Any] = []


def _register_launched(proc: Any) -> None:
    """登记自启动进程；顺手剔除已退出的，避免长期运行列表只增不减。"""
    _LAUNCHED[:] = [p for p in _LAUNCHED if p.poll() is None]
    _LAUNCHED.append(proc)


def _terminate_launched() -> None:
    """退出时回收自己拉起的 AEDT：先 terminate，5 s 未退再 kill。

    只处理 `_LAUNCHED` 里的（= MCP 自己 Popen 的），用户手动启动的实例不受影响。
    进程被强杀（taskkill /F）时 atexit 不触发 —— 那种情况只能靠 `quit_aedt(force=true)` 清理。
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
        timeout_seconds: 等待工程打开的超时秒数，默认 30（1-300）。到点后还会再核 3 次（间隔 2s），
            期间打开则回 `status="opened_after_timeout"`。

    Returns:
        {"success": True, "status": "opened/already_open", "project_opened": True,
         "method": "com/subprocess", "duration_s": 1.2}

    **`success` 只代表工程确实打开了**（2026-11-18 修正）：启动后必须轮询到目标出现在
    `open_projects` 里才回 `success: true`；否则回 `OPEN_TIMEOUT` + `project_opened: false`
    （改前这种情形返回 `success: true` + `status: launch_requested`，会让 agent 误以为开好了）。
    启动后会顺带清理「自己锁自己」的残留 `.aedt.lock`（锁里 PID 活着但工程没打开）。
    同时存在多个 AEDT 实例时直接回 `MULTI_INSTANCE_UNSUPPORTED`（2020 不注册 ROT，无法定向）。
    """
    t0 = time.monotonic()
    # 慢机器上 AEDT 打开工程可能要 2 分钟以上（2026-11-18 实测 129.5s），
    # 之前静默钳到 120s 会让调用方给的 150s 白费 → 上限放到 300s
    timeout_seconds = max(1, min(timeout_seconds, 300))

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

    # 先清「命令行带工程启动」造成的自锁残留（锁里 PID 活着，但工程其实没打开）
    self_lock = _clear_self_lock(resolved)
    # 再按常规判失效锁（PID 已死）
    lock_result = cleanup_stale_project_lock(resolved)
    if not lock_result["removed"] and lock_result.get("lock_pid"):
        if lock_result.get("status") == "lock_active":
            # 锁被活进程持有时，工程很可能**已经开着**（同一个实例）→ 先核对，
            # 别把"已打开"报成锁冲突（2026-11-18 真机：已打开时回 PROJECT_LOCKED 会误导 agent）
            ok_open, st_open, _err_open = _call_with_timeout(query_desktop_state, _COM_READ_TIMEOUT)
            if ok_open and st_open["connected"] and project_name in st_open["projects"]:
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": "already_open",
                    "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "com", "lock_pid": lock_result["lock_pid"],
                    "message": f"工程已经在 AEDT 中打开: {project_name}（PID {lock_result['lock_pid']}）",
                }
            return error_response("PROJECT_LOCKED",
                                  f"工程正在 AEDT PID={lock_result['lock_pid']} 中使用",
                                  lock_pid=lock_result["lock_pid"],
                                  hint="工程可能开在别的 AEDT 实例里：用 get_hfss_project_info 看 "
                                       "open_projects 确认；要关它用 close_hfss_project")

    lock_info: dict[str, Any] = {}
    if self_lock:
        lock_info["self_lock_removed"] = self_lock
    if lock_result["removed"]:
        lock_info["stale_lock_removed"] = True

    with _AEDT_LOCK:
        aedt_was_running = aedt_is_running()

        if aedt_was_running:
            # 多实例时 COM 无法按 PID 定向（AEDT 2020 不注册 ROT）→ 明确拒绝，别猜
            if (guard := _instance_guard("打开工程")) is not None:
                return guard
            ok, state, st_err = _call_with_timeout(query_desktop_state, _COM_READ_TIMEOUT)
            if not ok:
                return error_response(
                    "AEDT_NOT_RESPONDING",
                    f"AEDT 在运行但取不到状态：{st_err}",
                    hint="AEDT 可能（未响应）：看窗口是否卡住/有模态框；确认该实例可整体丢弃时用 quit_aedt(force=true)",
                    aedt_running=True, project_opened=False, verified=False)
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
                    **lock_info,
                }

            ok_com, result, com_err = _call_with_timeout(
                lambda: _com_open_project(resolved), float(timeout_seconds))
            if not ok_com:
                return error_response(
                    "AEDT_NOT_RESPONDING",
                    f"打开工程未在限时内返回：{com_err}",
                    hint="AEDT 可能（未响应）或弹了模态框：处理掉后重试；确认该实例可整体丢弃时用 quit_aedt(force=true)",
                    aedt_running=True, project_opened=False, verified=False,
                    project_name=project_name, project_path=resolved, **lock_info)
            if result["status"] in ("opened", "already_open"):
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": result["status"],
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "com", "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"HFSS 工程已打开: {project_name}",
                    **lock_info,
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
        self_lock_retried = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return error_response("PROCESS_EXITED", f"AEDT 进程已退出，退出码: {proc.returncode}",
                                      aedt_running=False, project_opened=False, **lock_info)
            # 启动 30s 还没开出来、且锁是「自己锁自己」→ 清一次锁再继续等（最多一次）
            if not self_lock_retried and time.monotonic() - t0 > 30:
                again = _clear_self_lock(resolved)
                if again:
                    self_lock_retried = True
                    lock_info["self_lock_removed"] = again
                    logger.warning("启动后检测到自锁，已清理并继续等待：%s", resolved)
            ok_rd, state, _rd_err = _call_with_timeout(query_desktop_state, 5.0)
            if ok_rd and state["connected"] and project_name in state["projects"]:
                _OPEN_PROJECT_PATHS[project_name] = resolved
                return {
                    "success": True, "status": "opened",
                    "aedt_running": True, "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "subprocess", "pid": proc.pid,
                    "duration_s": round(time.monotonic() - t0, 1),
                    "message": f"AEDT 已启动，工程已打开: {project_name}",
                    **lock_info,
                }
            time.sleep(1)

        # 收尾再核：AEDT 常在截止后一段时间才把工程挂进 open_projects
        # 2026-11-18 实测两次：129.5s（窗口 120s）、>246s（窗口 240s）→ 都给过 OPEN_TIMEOUT 假失败。
        # 收尾窗口按 5 次 × 5s = 25s 给足，避免"刚要开出来就被判失败"。
        for _ in range(5):
            ok_g, st_g, _err_g = _call_with_timeout(query_desktop_state, 5.0)
            if ok_g and st_g["connected"] and project_name in st_g["projects"]:
                _LAST_PID = proc.pid
                _OPEN_PROJECT_PATHS[project_name] = resolved
                elapsed = round(time.monotonic() - t0, 1)
                return {
                    "success": True, "status": "opened_after_timeout",
                    "project_opened": True, "verified": True,
                    "project_name": project_name, "project_path": resolved,
                    "method": "subprocess", "pid": proc.pid, "duration_s": elapsed,
                    "message": f"工程已打开（耗时 {elapsed}s，超过 {timeout_seconds}s 等待窗口，"
                               f"收尾核对确认 open_projects 里有它）",
                    "warnings": [
                        f"超过 timeout_seconds={timeout_seconds}s 才打开：慢机器建议直接给 180–300s"
                    ],
                    **lock_info,
                }
            time.sleep(5)

        # 起得来但没确认打开：不能再报 success（2026-11-18 实测会误导 agent）
        lock_now = get_project_lock_path(resolved)
        return error_response(
            "OPEN_TIMEOUT",
            f"AEDT 已启动（PID {proc.pid}），{timeout_seconds}s 内未确认工程打开"
            f"（open_projects 里没有 {project_name}）",
            hint="看一眼 AEDT 窗口：①「Project … is locked and cannot be opened」= 自锁，"
                 "重试一次即可（工具会自动清）；②「read only」= 去掉文件只读属性；"
                 "③（未响应）= 关掉该实例重开",
            aedt_running=aedt_is_running(), project_opened=False, verified=False,
            project_name=project_name, project_path=resolved, method="subprocess", pid=proc.pid,
            duration_s=round(time.monotonic() - t0, 1),
            lock_present=lock_now.is_file(),
            lock_pid=read_project_lock_pid(lock_now) if lock_now.is_file() else None,
            **lock_info,
        )


def _taskkill(pid: int) -> tuple[bool, str]:
    """`taskkill /F` 单个 PID（只用于 force 硬杀路径）。返回 (是否成功, 失败原因)。

    2026-11-18：`quit_aedt` 拆分后只剩硬杀走这里，`force` 参数恒为 True → 去掉参数（KISS）。
    """
    args = ["taskkill", "/F", "/PID", str(pid)]
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


_ACCESS_DENIED_MARKERS = ("拒绝访问", "access is denied", "access denied")


def _is_access_denied(reason: str) -> bool:
    """taskkill 的失败原因是不是「权限不足」（服务令牌不够，跟进程顽固不是一回事）。"""
    r = (reason or "").lower()
    return any(m in r for m in _ACCESS_DENIED_MARKERS)


def _embedding_pids(pids: list[int]) -> list[int]:
    """这些 PID 里哪些是 `-Embedding` **辅助**进程（无窗口、不持有工程）。"""
    out: list[int] = []
    for pid in pids:
        try:
            cmdline = " ".join(psutil.Process(pid).cmdline()).lower()
        except Exception:                           # noqa: BLE001 —— 进程已退出/无权读命令行
            continue
        if "-embedding" in cmdline:
            out.append(pid)
    return out


def _kill_aedt_processes(pids: list[int], rounds: int = 3) -> dict[str, Any]:
    """进程级终止 AEDT（`quit_aedt(force=True)` 与 helper 清理共用）。

    直接 `taskkill /F`；`rounds` 轮重试 —— `-Embedding` 辅助进程会被拉起**新的 PID**
    （2026-11-18 实测：杀掉 35932 后冒出 40936），单轮打完就报成功会留孤儿。
    ⚠️ 这是**整实例终结**：`scope` 恒为 `all`。权限不足单列 `PERMISSION_DENIED`。
    """
    global _LAST_PID

    killed: list[int] = []
    failed: list[dict[str, Any]] = []
    targets = list(dict.fromkeys(pids))
    total_rounds = max(1, rounds)
    for rnd in range(total_rounds):
        failed = []                                 # 每轮重算：只保留最后一轮的结果
        for pid in targets:
            ok, reason = _taskkill(pid)
            if ok:
                killed.append(pid)
            else:
                failed.append({"pid": pid, "reason": reason})
        time.sleep(0.5)
        remaining = [p for p in get_aedt_pids() if p not in killed]
        if not remaining or rnd == total_rounds - 1:
            break
        targets = remaining

    if _LAST_PID is not None and _LAST_PID in killed:
        _LAST_PID = None
    if killed:
        _OPEN_PROJECT_PATHS.clear()     # 进程没了，记录的工程路径全部失效

    if failed:
        denied = any(_is_access_denied(f["reason"]) for f in failed)
        pids_left = [f["pid"] for f in failed]
        return error_response(
            "PERMISSION_DENIED" if denied else "KILL_FAILED",
            (f"taskkill 被拒绝访问（PID {pids_left}）：MCP 服务的令牌权限不足以结束这些进程"
             if denied else f"部分 AEDT 进程未能终止：{failed}"),
            hint=("以管理员身份重启 MCP 服务后重试，或手动在任务管理器结束这些 PID"
                  if denied else "确认进程是否处于求解/挂起状态后重试"),
            killed=killed, pids_left=pids_left, method="taskkill", scope="all")
    return {"success": True, "method": "taskkill",
            "message": f"已终止 AEDT 进程 PID {killed}（含非 MCP 启动的 AEDT 实例）",
            "scope": "all"}


def _aedt_modal_dialogs() -> list[str]:
    """枚举 AEDT 进程的可见对话框窗口标题（Win32 `#32770` + Qt 自绘窗口）。

    AEDT 的「是否保存改动」确认框会让 COM 的 `CloseProject` 阻塞或抛异常。这里主动查一次，
    好让 `CLOSE_FAILED` 能回答"是不是弹框了"，而不是让调用方猜（2026-11-16）。

    ⚠️ 2026-11-18 实测：AEDT 的确认框/输入框是 **Qt 自绘窗口**（类名 `Qt5QWindowIcon` 这类），
    `#32770` 抓不到 —— 只认 `#32770` 会得到空列表，而空列表又被误读成"没弹框"。
    现在按「Qt 类名 + 有 owner 或标题不是主界面」把 Qt 对话框也纳入，仍是**启发式**：
    可能漏（Qt 无 owner 且标题带主界面字样）也可能多报（非对话框的 Qt 子窗）。
    **返回空列表只代表"没检测到"，不代表"一定没弹框"。**
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
    GWL_OWNER = 4                                       # GetWindow 的 uCmd: GW_OWNER

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):                             # Win32 回调签名固定
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value not in pids or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        title = buf.value or ""
        klass = cls.value
        is_dialog = klass == "#32770"                   # 标准对话框类（消息框/确认框）
        if not is_dialog and klass.startswith("Qt") and "QWindowIcon" in klass:
            owned = bool(user32.GetWindow(hwnd, GWL_OWNER))
            # 主窗标题形如「<工程> - HFSS_7…」；真实报错框标题是光秃秃的
            # 「ANSYS Electronics Desktop」（2026-11-18 截图实测），不能按关键字排除
            is_dialog = _looks_like_aedt_dialog(title, owned)
        if is_dialog:
            titles.append(title or "(无标题对话框)")
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:                                   # noqa: BLE001 —— 枚举失败不影响关闭流程
        return []
    return titles


def _looks_like_aedt_dialog(title: str, owned: bool) -> bool:
    """Qt 窗口是否当作对话框处理。

    AEDT 主窗口标题是「<工程> - HFSS_7…」或「<工程> - Ansys Electronics Desktop」；
    而真实的报错框标题是**没有工程名的光秃秃一句**，实测 2026-11-18：
    「ANSYS Electronics Desktop」（写文件失败的框）。所以判据是"有 owner 子窗"或
    "标题不是主窗样式"，不能只看标题里有没有 ansys electronics desktop。
    """
    if owned:
        return True
    main_like = " - " in title and ("hfss" in title.lower() or "ansys electronics desktop" in title.lower())
    return not main_like


def _post_wm_close_to_aedt(pids: list[int]) -> int:
    """给这些 AEDT 进程的可见顶层窗口发 `WM_CLOSE`（等同点右上角 X）。返回发出的消息数。"""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                   # noqa: BLE001 —— 非 Windows 环境
        return 0
    targets = set(pids)
    if not targets:
        return 0
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    sent = [0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):                             # Win32 回调签名固定
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value in targets and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent[0] += 1
        return True

    try:
        user32.EnumWindows(_cb, 0)
    except Exception:                                   # noqa: BLE001
        return 0
    return sent[0]


def _wait_no_aedt(timeout_s: float) -> bool:
    """等 `ansysedt.exe` 全部退出；返回是否等到了。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not get_aedt_pids():
            return True
        time.sleep(1)
    return not get_aedt_pids()


def _is_file_readonly(path: str) -> bool:
    """工程文件是否带 Windows 只读属性；路径为空/不存在/非 Windows → False（不拦保存）。

    2026-11-18 实测：`save_before_close=True` 撞上只读的 .aedt 时，AEDT 不抛 Python 异常，
    而是弹一个「An error occurred while writing to file … Is the file or directory read only?」
    的 Qt 对话框 —— 调用方看到的是卡住/失败，还留下一个要人工点的框。所以保存前先判一下。
    """
    if not path:
        return False
    try:
        attrs = getattr(Path(path).stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_READONLY)


def _readonly_warning(lock_path: str) -> str:
    """只读工程要回给调用方的告警文案（agent 必须转告用户，不能只报 success）。"""
    return (
        f"工程文件是只读的：{lock_path} —— 本次关闭不会保存任何改动"
        f"（AEDT 尝试写入会弹「An error occurred while writing to file … read only?」"
        f"对话框并卡住调用）。如需保存，请先去掉只读属性再关闭；"
        f"**请把这条转告用户，让用户确认是否可以丢弃改动**。")


def _backfill_lock_path(desktop: Any, target: str) -> str | None:
    """从 COM 取工程目录，推出 `.aedt` 路径 —— 用于只读检测与锁清理。

    工程不是本服务打开的时候（用户在 GUI 里开、或第三方进程开），
    `_OPEN_PROJECT_PATHS` 里没有记录，`lock_path` 就是空的：
    2026-11-18 真机实测关闭成功但 `lock_path: null`，只读检测和锁清理全部失效。

    ⚠️ AEDT 的 `GetPath()` 返回的是**工程目录**（不是文件），所以要拼 `<name>.aedt`；
    `.aedtz` 也试一次。取不到就返回 None（不影响关闭本身）。
    """
    try:
        proj = desktop.SetActiveProject(target)
        folder = proj.GetPath() if proj is not None else None
    except Exception:                               # noqa: BLE001 —— 老版本对象可能没这方法
        return None
    if not folder:
        return None
    for ext in (".aedt", ".aedtz"):
        cand = Path(folder) / f"{target}{ext}"
        if cand.is_file():
            return str(cand)
    return None


def _is_managed_aedt(pids: list[int]) -> bool:
    """这些 PID 里是否包含**本服务启动过**的 AEDT（否则就是用户/别的进程开的）。

    用于 `quit_aedt` 的安全闸门：退出别人启动的实例会连带关掉他打开的工程。
    """
    if _LAST_PID is not None and _LAST_PID in pids:
        return True
    for proc in list(_LAUNCHED):
        try:
            if getattr(proc, "pid", None) in pids:
                return True
        except Exception:                           # noqa: BLE001 —— 句柄失效不影响判断
            continue
    return False


def _lock_state(lock_path: str) -> dict[str, Any]:
    """锁文件状态（事后证据）。空路径返回 None，避免调用方误判成"锁已不存在"。"""
    if not lock_path:
        return {"lock_path": None, "lock_exists_after": None}
    p = get_project_lock_path(lock_path)
    return {"lock_path": str(p), "lock_exists_after": p.is_file()}


def _close_result(target: str, closed: bool, names_after: list[str],
                  lock_path: str, warnings: list[str],
                  readonly: bool = False) -> dict[str, Any]:
    """按「事后核对」组装关闭结果，并驱动锁清理 / 注册表清理。

    判定与清理都以 `closed`（target 是否还在 open_projects 里）为准 —— 不是 COM 有没有
    抛异常：AEDT 常在收尾阶段抛异常，那时项目其实已经关掉了。
    """
    lock_cleanup: dict[str, Any] = {}
    if closed:
        _OPEN_PROJECT_PATHS.pop(target, None)
        if lock_path:
            time.sleep(2)                       # 先等 AEDT 自己删锁，再清残余
            if get_project_lock_path(lock_path).is_file():
                result = cleanup_stale_project_lock(lock_path)
                if result["removed"]:
                    lock_cleanup = {"lock_cleanup": result}
    return {
        "success": closed, "method": "com", "project_closed": closed,
        "message": f"已关闭: {target}" if closed else f"未能确认关闭: {target}",
        "open_projects_after": names_after,
        "readonly_project_file": readonly,
        **lock_cleanup,
        **_lock_state(lock_path),
        "warnings": warnings,
    }


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
            target = ""
            warnings: list[str] = []
            readonly = _is_file_readonly(lock_path)
            if readonly:
                # 只读是「需要用户确认」的信息：无论 save_before_close 是什么都要报给调用方，
                # 让 agent 转告用户（否则用户不知道这个工程的改动根本写不回去）。
                warnings.append(_readonly_warning(lock_path))
            if lock_path and not Path(lock_path).is_file():
                # 工程未落盘（从未保存过）或路径写错：锁清理只能按传入路径推断，先讲明白
                warnings.append(
                    f"project_path 指向的文件不存在：{lock_path}"
                    f"（工程可能从未保存；锁清理按该路径推断，可能无效）")
            try:
                # 为空时关闭活动项目（而非 GetProjectList 的第一个，顺序不保证活动项目在首位）
                if project_name.strip():
                    target = project_name.strip()
                    if target.casefold() not in {n.casefold() for n in names}:
                        # 名字写错 / 工程早已关闭：别让 CloseProject 抛异常后被 hint 引向 force
                        return error_response(
                            "NO_SUCH_PROJECT",
                            f"AEDT 当前没有名为 {target} 的工程；已打开：{names or '（无）'}",
                            hint="先用 get_hfss_project_info 看 open_projects 确认拼写；"
                                 "名字对了才谈关闭 —— 别用 quit_aedt(force=true) 兜，"
                                 "它会终止该 AEDT 名下所有工程",
                            open_projects=names,
                            readonly_project_file=readonly,
                            warnings=warnings,
                        )
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

                # 工程不是本服务打开的（GUI 手开 / 第三方进程开）→ 从 COM 补出真实路径，
                # 否则只读检测与锁清理都失效（2026-11-18 真机：关闭成功但 lock_path=null）
                if not lock_path:
                    lock_path = _backfill_lock_path(desktop, target) or ""
                    if lock_path:
                        readonly = _is_file_readonly(lock_path)
                        if readonly:
                            warnings.append(_readonly_warning(lock_path))

                if save_before_close:
                    if not readonly:
                        try:
                            proj = desktop.SetActiveProject(target)
                            if proj is not None:
                                proj.Save()
                        except Exception:
                            pass
                    # readonly 时跳过 Save：原因已在上面的 warnings 里说明（保存必失败并弹框）

                desktop.CloseProject(target)
                names_after = list(desktop.GetProjectList())
                closed = target.casefold() not in {n.casefold() for n in names_after}
                return _close_result(target, closed, names_after, lock_path, warnings, readonly)
            except Exception as exc:                # noqa: BLE001 —— 已附着，记录后交给调用方决定是否 force
                logger.exception("CloseProject failed (attached OK): %s", exc)
                # 关键：AEDT 常在**收尾阶段**抛 DISP_E_EXCEPTION，此时项目其实已经关掉了
                # （2026-11-18 实测：返回 CLOSE_FAILED，但 open_projects 已不含 target、锁也没了）。
                # 判据必须是「事后核对」，不是「调用有没有抛异常」。
                names_after = None
                try:
                    names_after = list(desktop.GetProjectList())
                except Exception:                   # noqa: BLE001 —— 核对也失败，只能退回报错
                    pass
                if names_after is not None and target.casefold() not in {n.casefold() for n in names_after}:
                    result = _close_result(target, True, names_after, lock_path, warnings, readonly)
                    result.update({
                        "closed_despite_exception": True,
                        "message": f"已关闭: {target}（COM 收尾阶段抛异常，按事后核对判定为成功）",
                        "warning": f"{type(exc).__name__}: {exc}",
                    })
                    return result

                # 事后核对也确认没关掉，才是真的失败；再查一次弹框说明原因
                dialogs = _aedt_modal_dialogs()
                if dialogs:
                    msg = (
                        f"COM 已连通但关闭项目失败：{exc}。"
                        f"检测到 AEDT 上有 {len(dialogs)} 个对话框窗口正等待处理：{dialogs} —— "
                        f"很可能就是「是否保存改动」的确认框；请在 AEDT 窗口里处理掉它，再重试关闭"
                    )
                else:
                    msg = (
                        f"COM 已连通但关闭项目失败：{exc}，且事后核对确认项目仍打开"
                        f"（open_projects 仍含 {target}）。未检测到标准对话框窗口 —— "
                        f"AEDT 的 Qt 自绘对话框可能漏检，请人工看一眼 AEDT 窗口有没有待处理提示"
                    )
                return error_response(
                    "CLOSE_FAILED",
                    msg,
                    hint="先在 AEDT 窗口里处理掉任何等待确认的对话框，再重试；"
                         "quit_aedt(force=true) 会终止该 AEDT 名下**所有**工程（含正在用的其它工程），"
                         "只在确认改动可丢弃时使用",
                    attached=True,
                    modal_dialogs=dialogs,
                    open_projects_after=names_after,
                    readonly_project_file=readonly,
                    **_lock_state(lock_path),
                    warnings=warnings,
                )
    except Exception as exc:                     # noqa: BLE001 —— com_session 内部异常才兜底
        logger.exception("com_session failed, fallback to process kill: %s", exc)
        return None


@mcp.tool()
def close_hfss_project(
    project_name: str = "",
    project_path: str = "",
    save_before_close: bool = False,
) -> dict[str, Any]:
    """关闭 AEDT **工程**（File → Close Project）：不退出 AEDT、绝不杀进程。
    用于「关掉这个工程、AEDT 还要继续用别的工程」/「只释放某个工程的占用或锁」/「关闭前按 COM 语义保存」。
    要退出 AEDT 程序用 `quit_aedt`；要硬杀进程用 `quit_aedt(force=True, confirm=True)`；要打开工程用 `open_hfss_project`。
    不传 `project_name` 时关**当前活动工程**。

    **成败判据是事后核对，不是 COM 有没有抛异常**：`CloseProject` 之后
    再看 `GetProjectList()` / 锁文件。AEDT 在收尾阶段抛 `DISP_E_EXCEPTION` 时项目往往已经
    关掉了，此时返回 `success=true` + `closed_despite_exception=true`，异常只作 `warning`
    附带。返回体固定带 `open_projects_after` / `lock_exists_after` 作为证据。
    `project_name` 不在已打开列表 → `NO_SUCH_PROJECT`；
    `project_path` 指向不存在的文件（工程从未保存）→ 在 `warnings` 里标注。

    **多实例**：检测到 ≥2 个 AEDT 实例时直接 `MULTI_INSTANCE_UNSUPPORTED`
    （AEDT 2020 不注册 ROT，COM 无法按 PID 定向，猜一个实例去关会关错工程）。
    **未响应**：COM 调用限时 30s，超时回 `AEDT_NOT_RESPONDING`。
    **COM 附着失败**：回 `COM_UNAVAILABLE`，提示改用 `quit_aedt(force=true)`（这是唯一的硬杀入口）。

    返回体固定带 `readonly_project_file: bool`（工程文件是否只读）。只读时**无论
    `save_before_close` 取什么值**都会在 `warnings` 里给出说明并要求转告用户确认 ——
    因为这种工程的改动根本写不回磁盘（AEDT 会弹
    「An error occurred while writing to file … read only?」的 Qt 框，把 COM 调用卡住），
    agent 不能只报 `success: true` 就完事。

    ⚠️ 跨权限（UAC 隔离）时 DCOM / UIPI 会拦 COM，需 MCP 与 AEDT 同权限运行。

    Args:
        project_name: 项目名，为空关闭活动项目。
        project_path: 项目路径，用于只读检测与锁清理（为空时从 COM 的 `GetPath()` 回填）。
        save_before_close: 关闭前保存（只读工程会跳过并告警）。
    """
    lock_path = project_path or _OPEN_PROJECT_PATHS.get(project_name, "")

    if not aedt_is_running():
        return error_response(
            "AEDT_NOT_RUNNING", "AEDT 未运行，没有可关闭的工程",
            hint="要打开工程用 open_hfss_project；要启动 AEDT 用 launch_aedt",
            aedt_running=False)

    # 多实例时 COM 无法按 PID 定向（AEDT 2020 不注册 ROT）→ 拒绝猜
    if (guard := _instance_guard("关闭工程")) is not None:
        return guard

    # COM 优雅关闭（限时：未响应实例不会返回，不能白等几分钟）
    ok_com, result, com_err = _call_with_timeout(
        lambda: _com_close_project(project_name, save_before_close, lock_path),
        _COM_CLOSE_TIMEOUT)
    if not ok_com:
        return error_response(
            "AEDT_NOT_RESPONDING",
            f"关闭未在 {_COM_CLOSE_TIMEOUT:.0f}s 内返回：{com_err}",
            hint="AEDT 可能（未响应）：先在窗口里处理掉模态框再重试；"
                 "要放弃这个实例用 quit_aedt(force=true)（会关掉它名下所有工程）",
            aedt_running=True, project_name=project_name,
            **({"lock_path": lock_path} if lock_path else {}))

    if result is not None:
        return result

    # COM 附着失败：不杀进程（这是 quit_aedt 的职责）
    return error_response(
        "COM_UNAVAILABLE",
        "无法通过 COM 附着 AEDT，未关闭任何工程（也不会杀进程）",
        hint="确认 AEDT 是否正常响应；跨权限（UAC 隔离）需 MCP 与 AEDT 同权限运行；"
             "要强制收掉整个 AEDT 进程用 quit_aedt(force=true)",
        aedt_running=True, project_name=project_name)


@mcp.tool()
def quit_aedt(force: bool = False, confirm: bool = False,
              timeout_seconds: int = 30) -> dict[str, Any]:
    """退出 AEDT **程序**（关掉整个 AEDT 窗口、收掉进程）：不是关工程。
    用于「工程已关、AEDT 停在空窗口要收掉」/「释放 AEDT 的内存或许可证」/「实例卡死（未响应）且内容可丢弃」。
    只关某个工程用 `close_hfss_project`（进程保留）；硬杀必须 `force=True`+`confirm=True`（会终止机器上**所有** ansysedt.exe）。

    **`confirm` 有两个用处**（都不可绕过）：
    ① `force=True`（整机硬杀，`scope=all`）必须配 `confirm=True`；
    ② 目标实例**不是本服务启动的**（用户手开 / 别的工具开）时，`quit_aedt()` 默认回
       `CONFIRM_REQUIRED`（附 PID、`open_projects`），要 `confirm=True` 才退 —— 避免替别人
       把他正在用的 AEDT（可能带未保存工程）关掉。本服务自己启动的实例无需确认。

    顺序：COM `QuitApplication` → 给主窗发 `WM_CLOSE`（等同点右上角 X）→
    仍不退出就回 `STILL_RUNNING` 并说明原因（**不会**自己硬杀，避免丢未保存改动）。

    多实例（≥2 个 AEDT 实例，按启动批次计数）时拒绝执行：AEDT 2020 不注册 ROT，
    COM 无法按 PID 定向，猜一个会把别人在用的实例关掉。`force+confirm` 例外（scope=all）。

    Returns:
        {"success": True, "status": "quit/not_running", "method": "com/wm_close/taskkill",
         "pids_before": [...], "pids_after": [], "open_projects_before": [...],
         "data_loss_risk": bool, "warnings": [...], "duration_s": 1.2}

    Args:
        force: True = 直接 `taskkill /F`（丢未保存改动、关掉该实例所有工程）；需配合 `confirm`。
        confirm: 二次确认闸门；`force=True` 或「目标 AEDT 不是本服务启动的」时必须为 True。
        timeout_seconds: 等待 AEDT 退出的秒数，默认 30（5–120）。
    """
    t0 = time.monotonic()
    timeout_seconds = max(5, min(timeout_seconds, 120))

    if force and not confirm:
        return error_response(
            "CONFIRM_REQUIRED",
            "force=True 会 taskkill /F 掉机器上**所有** ansysedt.exe（含别人打开的实例），"
            "未保存改动一并丢失 —— 需要二次确认",
            hint="确认要这么做请再调一次 quit_aedt(force=True, confirm=True)；"
                 "只想关工程用 close_hfss_project，只想正常退出用 quit_aedt()",
            scope="all")

    pids_before = get_aedt_pids()
    if not pids_before:
        return {"success": True, "status": "not_running", "aedt_running": False,
                "message": "AEDT 未运行（无需退出）",
                "pids_before": [], "pids_after": [],
                "duration_s": round(time.monotonic() - t0, 1)}

    ok_state, state, _err = _call_with_timeout(query_desktop_state, _COM_READ_TIMEOUT)
    open_projects = list((state or {}).get("projects") or []) if (ok_state and state) else None

    warnings: list[str] = []
    if open_projects:
        warnings.append(
            f"AEDT 里还有 {len(open_projects)} 个打开的工程 {open_projects}：退出会连带关闭，"
            f"未保存的改动会丢 —— **请把这条转告用户确认**")

    def _quit_ok(method_: str) -> dict[str, Any]:
        """退出成功的统一返回体（两处复用，别各写一份）。"""
        return {"success": True, "status": "quit", "method": method_,
                "aedt_running": False,
                "pids_before": pids_before, "pids_after": [],
                "open_projects_before": open_projects,
                "data_loss_risk": bool(open_projects),
                "warnings": warnings,
                "message": f"AEDT 已退出（{method_}）",
                "duration_s": round(time.monotonic() - t0, 1)}

    # 不是本服务启动的实例（用户手开 / 别的工具开）→ 未经确认不许退。
    # 注意顺序：多实例先拒（那是硬冲突），单实例才谈"要不要确认"。

    # ── force：整机硬杀（显式逃生口）──
    if force:
        result = _kill_aedt_processes(pids_before)
        result.update({
            "status": "killed",
            "pids_before": pids_before,
            "pids_after": get_aedt_pids(),
            "open_projects_before": open_projects,
            "data_loss_risk": bool(open_projects),
            "warnings": warnings,
            "duration_s": round(time.monotonic() - t0, 1),
        })
        return result

    guard = _instance_guard("退出 AEDT")
    if guard is not None:
        return guard

    # 单实例、且不是本服务启动的（用户手开 / 别的工具开）→ 未经确认不许退
    if not _is_managed_aedt(pids_before) and not confirm:
        return error_response(
            "CONFIRM_REQUIRED",
            f"这个 AEDT 不是本服务启动的（PID {pids_before}），退出会连带关闭它里面的工程，"
            f"未保存改动会丢 —— 需要用户确认",
            hint="确认要退出请再调一次 quit_aedt(confirm=True)；"
                 "只想关某个工程用 close_hfss_project（AEDT 进程保留）",
            aedt_pids=pids_before,
            open_projects=open_projects,
            managed=False)

    method = ""
    # ── 1) COM 优雅退出 ──
    if ok_state:
        def _com_quit() -> None:
            with com_session():
                _app, desktop = _attach_aedt()
                desktop.QuitApplication()

        okq, _v, qerr = _call_with_timeout(_com_quit, _COM_CLOSE_TIMEOUT)
        if okq:
            method = "com"
        else:
            warnings.append(f"COM QuitApplication 失败：{qerr}")

    if not (method and _wait_no_aedt(min(timeout_seconds, 20))):
        # ── 2) 窗口级 WM_CLOSE（等同点 X，不会丢未保存内容）──
        sent = _post_wm_close_to_aedt(get_aedt_pids() or pids_before)
        if sent:
            method = "wm_close"
            warnings.append(f"COM 退出未完成，已向 {sent} 个 AEDT 窗口发送 WM_CLOSE")
        _wait_no_aedt(min(timeout_seconds, 20))

    pids_after = get_aedt_pids()

    # ── 3) 只剩 `-Embedding` 辅助进程 → 直接清理（无窗口、无工程，杀它不丢数据）──
    # 2026-11-18 实测：COM 退出会带走启动器，但 `-Embedding` 留着；它没有窗口，
    # WM_CLOSE 无效 → 旧逻辑报 STILL_RUNNING 让人手工处理。
    if pids_after and len(_embedding_pids(pids_after)) == len(pids_after):
        helper_res = _kill_aedt_processes(pids_after)
        method = f"{method}+helper_cleanup" if method else "helper_cleanup"
        warnings.append(f"启动器已退出，残留 -Embedding 辅助进程 {pids_after} 已自动清理")
        pids_after = get_aedt_pids()
        if not pids_after:
            return _quit_ok(method) if helper_res.get("success") else helper_res

    if not pids_after:
        return _quit_ok(method or "none")

    return error_response(
        "STILL_RUNNING",
        f"AEDT 仍在运行（PID {pids_after}），{timeout_seconds}s 内没退出",
        hint="窗口可能弹了「是否保存」确认框或正在求解：先人工处理窗口再重试；"
             "确认改动可丢弃时才用 quit_aedt(force=true)（会 taskkill /F，关掉该实例所有工程）",
        method=method or "none",
        pids_before=pids_before,
        pids_after=pids_after,
        open_projects_before=open_projects,
        data_loss_risk=bool(open_projects),
        warnings=warnings,
        duration_s=round(time.monotonic() - t0, 1),
    )


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
    ok, state, err = _call_with_timeout(query_desktop_state, _COM_READ_TIMEOUT)
    if not ok:
        return error_response(
            "AEDT_NOT_RESPONDING",
            f"AEDT 在运行（PID {pids}）但取不到状态：{err}",
            hint="AEDT 可能（未响应）：处理掉窗口上的模态框再查；必要时重启该实例",
            aedt_running=True, pids=pids)

    groups = _aedt_instance_groups()
    return {
        "success": True,
        "aedt_running": True,
        "pids": pids,
        "instance_count": len(groups) if groups else 1,
        "instances": groups,
        "multi_instance": len(groups) > 1,
        "com_available": state["connected"],
        "open_projects": state.get("projects", []),
        "active_project": state.get("active_project", ""),
        "active_design": state.get("active_design", ""),
    }
