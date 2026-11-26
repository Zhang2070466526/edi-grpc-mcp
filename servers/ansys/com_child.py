"""单次 AEDT COM 操作的隔离子进程。

为什么单独起进程：AEDT 卡在模态框上时，COM 调用既不返回也不抛异常。
线程方案里只能"不等人"（join 超时），卡住的线程仍持有 _AEDT_LOCK 和 COM 引用，
一次卡死就把整条通道堵到 AEDT 重启为止 —— 2026-11-25 真机复现：
16:03 弹了隐藏模态框 `Show Queued Simulations`，之后 28 分钟内所有 COM 调用
全部 10s 超时，而同样的 COM 从外部 cscript 秒通。改成子进程后，父进程超时
直接 terminate，COM 资源、Python 线程、模块状态随进程一起消失，服务侧零残留。

父进程用法（servers/ansys/project_manage.py::_call_com_child）：
    源码态：python -m servers.ansys.com_child state
            python -m servers.ansys.com_child open  <project_path>
            python -m servers.ansys.com_child quit
    冻结态：edi_mcp_server.exe --com-child state      （见 edi_entry.py）
打包后包内没有解释器，父进程不能再 `python 脚本路径`（会撞 exe 自己的 argparse，
exit=2）；两条路的子进程逻辑完全相同，冻结态只是换了个入口。

输出约定：stdout 最后一行是单行 JSON
    {"ok": true,  "result": {...}}
    {"ok": false, "error": "..."}
窗口模式（console=False）下子进程 sys.stdout 可能是 None，所以父进程还会通过环境变量
`EDI_COM_CHILD_OUT` 指定一个结果文件，JSON 同时写入该文件；父进程优先读文件。
诊断信息一律走 stderr，避免污染 JSON。
"""

from __future__ import annotations

import json
import os
import sys

#: 与 config._COM_PROGIDS 保持一致：AEDT 2020 R2 不注册 ROT，
#: GetActiveObject 会 MK_E_UNAVAILABLE，真正的通道是 Dispatch。
PROGIDS = ("AnsoftHfss.HfssScriptInterface", "Ansoft.ElectronicsDesktop")


def _ansysedt_instances() -> dict[int, str]:
    """PID -> 命令行。用来区分 GUI 启动器（无参数）与 `-Embedding` 辅助进程。"""
    import psutil
    out: dict[int, str] = {}
    for p in psutil.process_iter(["name", "pid", "cmdline"]):
        if p.info.get("name") == "ansysedt.exe":
            out[p.info["pid"]] = " ".join(p.info.get("cmdline") or [])
    return out


def _kill_embedding_shell(pid: int) -> bool:
    """杀掉抢镜的 `-Embedding` 空壳（清掉后 Dispatch 才能连到 GUI）。"""
    # ⚠️ 本组「-Embedding 空壳处理」与 servers/ansys/config.py 的 _kill_embedding_shell /
    # _reap_orphan_shells 逻辑一致，改这里要同步改另一边（本进程是隔离脚本，不能 import config）。
    import psutil
    try:
        psutil.Process(pid).kill()
        return True
    except Exception:                               # noqa: BLE001 —— 已退出/无权
        return False


def _reap_orphan_shells(infos: dict[int, str]) -> list[int]:
    """只剩 `-Embedding` 空壳（无 GUI）时清掉，返回被清 PID。

    2026-11-25 真机：强杀 GUI 后空壳 COM 服务活下来继续被 `Dispatch` 抢到，
    之后所有附着都只连到它。空壳不持有工程，清它不丢数据；有真 GUI 时不动手。
    """
    if not infos:
        return []
    shells = [p for p, cmd in infos.items() if "-embedding" in cmd.lower()]
    if len(shells) != len(infos):
        return []
    import psutil
    killed: list[int] = []
    for pid in shells:
        try:
            psutil.Process(pid).kill()
            killed.append(pid)
        except Exception:                           # noqa: BLE001 —— 已退出/无权
            continue
    return killed


def _aedt_running() -> bool:
    """AEDT 是否在跑。子进程必须自己判一次：Dispatch 在实例不存在时会**新建**一个，
    那会违反「附着现有实例、绝不创建」的约定。

    不做 try/except 兜底：依赖缺失或枚举失败要如实报错，否则会被伪装成
    「AEDT 未运行」误导排查（2026-11-25 实测：uv base python 缺 psutil 即踩此坑）。
    """
    return bool(_ansysedt_instances())


def _attach():
    """附着现有 AEDT 实例，返回 desktop。失败抛异常，并带上每条通道的错误。"""
    if not _aedt_running():
        raise RuntimeError("AEDT 未运行：com_child 只附着现有实例，绝不创建新实例")

    import pythoncom
    from win32com.client import Dispatch, GetActiveObject

    pythoncom.CoInitialize()
    errors: list[str] = []
    instances = _ansysedt_instances()
    for progid in PROGIDS:
        for name, getter in (("GetActiveObject", GetActiveObject), ("Dispatch", Dispatch)):
            for attempt in (1, 2):          # 撞到空壳先清掉，再重连一次（实测清完即连上 GUI）
                try:
                    app = getter(progid)
                    desktop = app.GetAppDesktop()
                    list(desktop.GetProjectList())      # 探活：空壳/瘦壳在这里抛
                    pid = _desktop_pid(desktop)
                    if pid and "-embedding" in instances.get(pid, "").lower():
                        # 2026-11-25 真机：空壳的 GetProjectList() 返回 [] 且不抛，
                        # 探活形同虚设 → 必须按 PID 认出来，否则下游只看到"空工程列表"。
                        gui = [p for p, cmd in instances.items() if "-embedding" not in cmd.lower()]
                        if attempt == 1 and gui and _kill_embedding_shell(pid):
                            errors.append(f"{progid} via {name}: 连到 -Embedding 空壳(PID {pid})，已清掉，重试")
                            continue
                        errors.append(f"{progid} via {name}: 连到的 PID {pid} 是 -Embedding 空壳，跳过")
                        break
                    return desktop
                except Exception as exc:                # noqa: BLE001 —— 换下一条通道
                    errors.append(f"{progid} via {name}: {exc}")
                    break
    reaped = _reap_orphan_shells(instances)
    if reaped:
        raise RuntimeError(
            f"只连得到残留的 -Embedding 空壳（PID {reaped}，无 GUI 实例），已清理；"
            f"重新 launch_aedt / open_hfss_project 即可")
    raise RuntimeError(f"attached none of {PROGIDS}: {' | '.join(errors)}")


def _desktop_pid(desktop) -> int | None:
    """当前 COM 连到的 AEDT 进程 PID（AEDT 的 Desktop.GetProcessID）。"""
    try:
        return int(desktop.GetProcessID())
    except Exception:                               # noqa: BLE001 —— 拿不到就算了
        return None


def _instance_diagnosis(connected_pid: int | None) -> str | None:
    """连到 `-Embedding` 空壳而机器上还有 GUI 实例时，给出权限不匹配的诊断。

    2026-11-25 真机根因：服务以普通权限运行、AEDT 是管理员权限（UAC 隔离）→
    COM 无法跨完整性级别附着 GUI 实例，`Dispatch` 只能新建一个自己的
    `-Embedding`（空壳、无工程、无窗口），于是 GetProjectList() 恒为空、
    每次调用还多出一个空壳进程。这条诊断把「静默空列表」变成可读结论。
    """
    if not connected_pid:
        return None
    infos = _ansysedt_instances()
    if "-embedding" not in infos.get(connected_pid, "").lower():
        return None                                 # 连的是真实 GUI 实例，正常
    gui = [pid for pid, cmd in infos.items() if "-embedding" not in cmd.lower()]
    if not gui:
        return None                                 # 只有空壳，没有可选的真实实例
    return (f"COM 连到了 -Embedding 空壳（PID {connected_pid}），真实 GUI 实例是 {gui}："
            f"要么服务与 AEDT 权限不一致（UAC 隔离，AEDT 以管理员启动时普通权限服务连不上 GUI），"
            f"要么 Dispatch 抢在 GUI 注册前连上了 AEDT 自带的空壳。先让两者同权限运行；"
            f"仍不行就重启该 AEDT 实例再试")


def cmd_state() -> dict:
    desktop = _attach()
    connected_pid = _desktop_pid(desktop)
    projects = list(desktop.GetProjectList())
    if not projects and (diag := _instance_diagnosis(connected_pid)):
        raise RuntimeError(diag)
    active = desktop.GetActiveProject()
    active_name = active.GetName() if active is not None else ""
    active_design = ""
    if active is not None:
        try:
            d = active.GetActiveDesign()
            if d is not None:
                active_design = d.GetName()
        except Exception:                           # noqa: BLE001 —— 设计可能没打开
            pass
    return {
        "connected": True,
        "pid": connected_pid,
        "projects": projects,
        "active_project": active_name,
        "active_design": active_design,
    }


def cmd_open(project_path: str) -> dict:
    from pathlib import Path

    desktop = _attach()
    connected_pid = _desktop_pid(desktop)
    names = list(desktop.GetProjectList())
    if not names and (diag := _instance_diagnosis(connected_pid)):
        raise RuntimeError(diag)
    name = Path(project_path).stem
    if name in names:
        desktop.SetActiveProject(name)
        return {"status": "already_open", "project_name": name, "pid": connected_pid}
    result = desktop.OpenProject(project_path)
    if result is not None or name in list(desktop.GetProjectList()):
        return {"status": "opened", "project_name": name, "pid": connected_pid}
    return {
        "status": "COM_OPEN_FAILED",
        "project_name": name,
        "pid": connected_pid,
        "error": "OpenProject returned None and project not in list",
    }


def _snport_from_content(path) -> tuple:
    """按 Touchstone 数据列数推断端口数。

    每行 = 1 个频率 + 2·N² 列（N 端口 → N² 个复数，每个实部/虚部各 1 列）。
    N=2 → 9 列；N=4 → 33 列。
    ⚠️ 不能信 `GetExcitations()` 的长度：CPW_SL 实测 2 端口却返回 4 项（2026-11-25），
    会把 2 端口数据写成 `.s4p`，下游按 4 端口解析会越界。
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith(("!", "#")):
                    continue
                cols = len(s.split())
                n = int(round(((cols - 1) / 2) ** 0.5))
                if n >= 1 and 1 + 2 * n * n == cols:
                    return n
                return 0
    except Exception:                               # noqa: BLE001 —— 推断失败交给兜底
        return 0
    return 0


def cmd_export_snp(project_path: str, design_name: str, setup: str,
                   sweep: str, output_path: str, port_count: str = "0") -> dict:
    """导出 HFSS 已求解的 S 参数为 Touchstone。

    等价 edi-server `apps/star/hfss/hfss_export.py` 的做法：
        oModule.ExportNetworkData("", [solution], 3, path, ["all"], True, 50, "S",
                                  -1, 0, 15, True, True, False)
    solution 形如 "Setup1 : Sweep"（自适应解为 "Setup1 : LastAdaptive"）。
    端口数由**文件内容列数**推断后定名 .s<N>p（HFSS 不会替你改后缀）。
    """
    from pathlib import Path

    desktop = _attach()
    pid = _desktop_pid(desktop)
    name = Path(project_path).stem
    names = list(desktop.GetProjectList())
    if name not in names:
        raise RuntimeError(f"工程 {name} 未在 AEDT 打开（open_projects={names}）")

    project = desktop.SetActiveProject(name)
    try:
        design = project.SetActiveDesign(design_name)
    except Exception:                               # noqa: BLE001 —— 换成可读错误
        avail: list = []
        for getter in ("GetDesignNames", "GetTopDesignList"):
            try:
                avail = list(getattr(project, getter)())
                break
            except Exception:                       # noqa: BLE001 —— 换下一个方法
                continue
        raise RuntimeError(f"设计 {design_name} 不存在（可用：{avail}）") from None

    # sweep 必须显式给：留空去导自适应解（"Setup : LastAdaptive"）在 AEDT 2020 上
    # 实测会把实例搞崩（2026-11-25：导出后 GUI 进程消失、只剩空壳），不再兜底。
    if not sweep:
        raise RuntimeError(
            "sweep_name 不能为空：请显式指定扫频名（如 'Sweep'）。"
            "AEDT 2020 的自适应解（'Setup : LastAdaptive'）导出不稳定，未提供自动兜底")
    solution = f"{setup} : {sweep}"

    out = Path(output_path)
    if not out.parent.is_dir():
        raise RuntimeError(f"输出目录不存在：{out.parent}")
    placeholder = out.suffix.lower() == ".snp"        # 调用方用 .sNp 占位，后缀待定
    export_path = out.with_suffix(".snptmp") if placeholder else out
    if export_path.is_file():
        export_path.unlink()

    o_mod = design.GetModule("Solutions")
    try:
        o_mod.ExportNetworkData("", [solution], 3, str(export_path), ["all"], True, 50, "S",
                                -1, 0, 15, True, True, False)
    except Exception as exc:                        # noqa: BLE001 —— 换成可读错误
        raise RuntimeError(
            f"ExportNetworkData 失败：solution={solution!r}"
            f"（setup/sweep 名需与 AEDT 里一致，例如 'Setup1 : Sweep'）；"
            f"原始错误：{exc}") from None
    if not export_path.is_file() or export_path.stat().st_size <= 0:
        raise RuntimeError(f"导出未生成文件：{export_path}（solution={solution}）")

    n = _snport_from_content(export_path) or int(port_count or 0) or 2
    if placeholder:
        final = out.with_suffix(f".s{n}p")
        if final.exists():
            final.unlink()
        export_path.replace(final)
    else:
        final = export_path

    return {"snp_path": str(final), "size": final.stat().st_size,
            "solution": solution, "port_count": n, "pid": pid}


def cmd_quit() -> dict:
    desktop = _attach()
    desktop.QuitApplication()
    return {"status": "quit_sent"}


TASKS = {"state": cmd_state, "open": cmd_open,
         "export_snp": cmd_export_snp, "quit": cmd_quit}

#: 父进程指定的结果文件（窗口模式没有可用 stdout）。需与 project_manage._COM_CHILD_OUT_ENV 一致。
_OUT_ENV = "EDI_COM_CHILD_OUT"


def _emit(payload: dict, code: int) -> int:
    """输出单行 JSON：优先写父进程给的结果文件，再顺手写 stdout（若有）。"""
    line = json.dumps(payload, ensure_ascii=False)
    out = os.environ.get(_OUT_ENV)
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass                                    # 写不了文件就退到 stdout
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.write(line + "\n")
            stream.flush()
        except Exception:                           # noqa: BLE001 —— 窗口模式句柄不可用
            pass
    return code


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in TASKS:
        return _emit({"ok": False,
                      "error": f"usage: com_child.py {'|'.join(TASKS)} [args]"},
                     2)
    task, args = sys.argv[1], sys.argv[2:]
    try:
        result = TASKS[task](*args)
        return _emit({"ok": True, "result": result}, 0)
    except Exception as exc:                        # noqa: BLE001 —— 原样回给父进程
        return _emit({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    sys.exit(main())
