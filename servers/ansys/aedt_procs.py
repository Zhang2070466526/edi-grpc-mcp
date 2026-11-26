"""AEDT 进程 / COM ProgID 的公共小工具（**只用 stdlib + psutil**）。

为什么单独一个模块：`servers/ansys/config.py`（服务进程内 COM）与
`servers/ansys/com_child.py`（隔离子进程）原本各有一份 `-Embedding` 空壳处理逻辑，
两处注释互相提醒「改一处要同步改另一边」—— 历史上漏过。逻辑收敛到这里，两边 import。

⚠️ com_child 是**独立子进程**（`exe --com-child` / `python com_child.py`），不能 import
config（会拖进 win32com、settings 等重依赖），所以本模块只允许 stdlib + psutil。

版本相关背景（2026-11-26 真机）：机器同时装了 AEDT 2020 R2 与 2024.2 时，裸 ProgID
`Ansoft.ElectronicsDesktop` 走注册表 `CurVer`，可能指向**另一个版本**；而用户看着的 GUI
是第三个版本 → `Dispatch` 连到的不是那个 GUI，于是所有查询都像"未响应"。
AEDT 另外注册了带版本的 ProgID（`Ansoft.ElectronicsDesktop.2020.2`，有自己的 CLSID），
按"运行中实例的版本"优先试它，才能连对实例。
"""

from __future__ import annotations

import re

#: 裸 ProgID（走 CurVer）与 HFSS 脚本接口（只有裸名，无版本化形式）。
BASE_PROGID = "Ansoft.ElectronicsDesktop"
HFSS_SCRIPT_PROGID = "AnsoftHfss.HfssScriptInterface"

#: 安装路径 → 版本。三种常见布局：
#:   C:\Program Files\AnsysEM\AnsysEM20.2\Win64\ansysedt.exe   → 2020.2
#:   C:\Program Files\AnsysEM\v242\Win64\ansysedt.exe          → 2024.2
#:   C:\Program Files\AnsysEM\AnsysEM2024R2\Win64\ansysedt.exe → 2024.2
_RE_YEAR_R = re.compile(r"(?i)ansysem(\d{4})r(\d)")
_RE_YY_X = re.compile(r"(?i)ansysem(\d{2})\.(\d)(?!\d)")
_RE_VXXX = re.compile(r"(?i)[\\/]v(\d)(\d)(\d)(?:[\\/]|$)")


def exe_version(exe_path: str) -> str:
    """从 ansysedt.exe 路径推 AEDT 版本（`"2020.2"`）；推不出返回空串。"""
    if not exe_path:
        return ""
    m = _RE_YEAR_R.search(exe_path)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    m = _RE_YY_X.search(exe_path)
    if m:
        return f"{2000 + int(m.group(1))}.{m.group(2)}"
    m = _RE_VXXX.search(exe_path)
    if m:
        return f"{2000 + int(m.group(1)) * 10 + int(m.group(2))}.{m.group(3)}"
    return ""


def _file_version(exe_path: str) -> str:
    """读 exe 的 PE 文件版本（路径里看不出年份时的兜底，如 `D:\\Ansys 2024\\...`）。

    Ansys 用"年份-2000"编号：2020 R2 → 20.2、2024 R2 → 24.2。
    """
    try:
        import win32api

        info = win32api.GetFileVersionInfo(exe_path, "\\")
        ms = info.get("FileVersionMS")
        if ms is None:
            return ""
        major, minor = ms >> 16, ms & 0xFFFF
        if 10 <= major < 100:
            return f"{2000 + major}.{minor}"
        return f"{major}.{minor}" if major else ""
    except Exception:                                   # noqa: BLE001 —— 读不到就算了
        return ""


def instances() -> dict[int, dict]:
    """`pid -> {cmdline, exe, version, is_helper}`，只含 ansysedt.exe。

    ⚠️ **两阶段枚举**：`process_iter(["cmdline"])` 会给每个进程都读一次 PEB ——
    本机 390 进程实测 **1030 ms**；先只按 `name` 过滤（≈0 ms），再对 ansysedt 取
    命令行/exe（≈3 ms）。这个函数在每次工具调用的诊断字段里都会跑，慢 1 s 会
    直接把 `AEDT_NOT_RESPONDING` 的"快速失败"预算吃光（2026-11-26 实测：
    超时 0.3 s 的用例因此涨到 1.4 s+，全量跑时偶发失败）。

    枚举失败直接抛（调用方决定是否兜底）：把"枚举失败"伪装成"AEDT 未运行"会误导排查
    （2026-11-25 实测：uv base python 缺 psutil 时就这么被误导过）。
    """
    import psutil

    out: dict[int, dict] = {}
    for proc in psutil.process_iter(["name", "pid"]):
        if proc.info.get("name") != "ansysedt.exe":
            continue
        pid = proc.info["pid"]
        try:
            handle = psutil.Process(pid)
            cmd = " ".join(handle.cmdline() or [])
            exe = handle.exe() or ""
        except Exception:                               # noqa: BLE001 —— 权限/进程已退
            continue
        out[pid] = {
            "cmdline": cmd,
            "exe": exe,
            "version": exe_version(exe) or _file_version(exe),
            "is_helper": "-embedding" in cmd.lower(),
        }
    return out


def is_embedding(infos: dict[int, dict], pid: int) -> bool:
    """该 PID 是不是 `-Embedding` 空壳（无窗口、无工程）。"""
    return bool(infos.get(pid, {}).get("is_helper"))


def gui_pids(infos: dict[int, dict]) -> list[int]:
    """真实 GUI 实例（不带 `-Embedding`）。"""
    return [pid for pid, info in infos.items() if not info.get("is_helper")]


def gui_versions(infos: dict[int, dict]) -> list[str]:
    """运行中 GUI 实例的版本（去重、保序）。"""
    out: list[str] = []
    for pid in gui_pids(infos):
        ver = infos[pid].get("version") or ""
        if ver and ver not in out:
            out.append(ver)
    return out


def curver() -> tuple[str, str]:
    """注册表里裸 ProgID 的 CurVer，返回 `(progid, version)`；读不到返回 `("", "")`。

    `HKCR\\Ansoft.ElectronicsDesktop\\CurVer` 默认值形如
    `Ansoft.ElectronicsDesktop.2020.2` —— 这就是"裸名会 Dispatch 到哪个版本"。
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, BASE_PROGID + r"\CurVer") as key:
            value = winreg.QueryValueEx(key, "")[0]
    except Exception:                                   # noqa: BLE001 —— 没装/没注册
        return "", ""
    value = str(value or "")
    ver = value[len(BASE_PROGID) + 1:] if value.lower().startswith(BASE_PROGID.lower() + ".") else ""
    return value, ver


def progid_candidates(infos: dict[int, dict] | None = None) -> list[str]:
    """按「运行实例版本优先」给出要试的版本化 ProgID（不含裸名）。

    ⚠️ 只对**确实在跑**的版本返回：`Dispatch` 在该版本实例不存在时会**新建**一个 AEDT，
    违反"只附着、绝不创建"的约定。
    """
    infos = instances() if infos is None else infos
    return [f"{BASE_PROGID}.{ver}" for ver in gui_versions(infos)]


def version_report(infos: dict[int, dict] | None = None) -> dict:
    """给工具层用的版本自证：运行实例版本 vs CurVer 指向的版本。"""
    infos = instances() if infos is None else infos
    curver_progid, curver_ver = curver()
    running = [
        {"pid": pid, "exe": infos[pid].get("exe", ""), "version": infos[pid].get("version", ""),
         "is_embedding": bool(infos[pid].get("is_helper"))}
        for pid in infos
    ]
    gui_vers = gui_versions(infos)
    return {
        "aedt_instances": running,
        "aedt_versions_running": gui_vers,
        "curver_progid": curver_progid,
        "curver_version": curver_ver,
        # 有 GUI 在跑、但 CurVer 指向另一个版本 ⇒ 裸 ProgID 会连错版本
        "version_mismatch": bool(gui_vers and curver_ver and curver_ver not in gui_vers),
    }


# -- `-Embedding` 空壳处理（config 与 com_child 共用；psutil 由调用方注入以便打桩） --

def kill_embedding_shell(pid: int, psutil_mod) -> bool:
    """杀掉抢镜的空壳；已退出/无权返回 False。空壳不持有工程，杀它不丢数据。"""
    try:
        psutil_mod.Process(pid).kill()
        return True
    except Exception:                                   # noqa: BLE001
        return False


def reap_orphan_shells(pids: list[int], is_embedding_fn, psutil_mod) -> list[int]:
    """**只剩**空壳（没有真 GUI）时才清掉，返回被清 PID；有 GUI 时绝不动手。"""
    if not pids:
        return []
    shells = [pid for pid in pids if is_embedding_fn(pid)]
    if len(shells) != len(pids):
        return []
    killed: list[int] = []
    for pid in shells:
        if kill_embedding_shell(pid, psutil_mod):
            killed.append(pid)
    return killed
