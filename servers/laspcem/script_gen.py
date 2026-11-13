"""LASPCEM 仿真脚本封装 — 生成「求解 / 导出 S 参数 / 建报告」三类脚本。

脚本通过 `laspcem /session <脚本>` 执行（GUI 内嵌 Python 宿主，提供 app / cem / report /
viewer 全局对象），等价于 GUI 的 File → Script → Run Script。

三条实测硬约束（2026-11-12，LASPCEM 2026 R1 / BuildNo 28167）：

1. **首行必须是 `#Version Information:`** + `app.SetScriptVersion(...)`，否则宿主直接拒脚本
   （rc=1、1.7s 退出、脚本一行都不执行）。
2. **`cem.FEMAnalyze` 是异步的**：它立刻返回，求解器（mpiexec + phofem）在 GUI 进程里继续跑。
   所以求解脚本**不能**紧跟 `SaveAs` / `app.Exit()`（实测会把求解器掐死：3s 空转、无 mpiexec），
   也不做 `SaveAs`（结果由 GUI 自己写回工程）。
3. **setup 名用显示名**：`.sem` 里存的是 `Setup1(FEM)`（括号里是算法标记），
   传给 `FEMAnalyze` 必须用 `Setup1`；解名则是 `算法_基名` = `FEM_Setup1`。

宿主**不回传 stdout/stderr**，rc 也不代表成败 ⇒ 每个脚本都由 `render_script()` 包一层：
写版本号 → 逐步 BEGIN/OK 日志 → 出错写 traceback →（可选）`app.Exit()`。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from servers.laspcem.setup_reader import split_setup_name

_SCRIPT_VERSION = "{'BuildDate':'2026-09-23','BuildNo':'28167'}"

#: 包装头：逐条写日志（LOGPATH 由 render_script 替换）
_WRAPPER_HEAD = [
    "import traceback",
    "_log = open(LOGPATH, 'w', encoding='utf-8')",
    "",
    "def _w(msg):",
    "    _log.write(msg + chr(10))",
    "    _log.flush()",
    "",
    "try:",
]

_WRAPPER_EXCEPT = [
    "except BaseException:",
    "    _log.write(traceback.format_exc())",
    "    _log.flush()",
]

_WRAPPER_EXIT = [
    "finally:",
    "    try:",
    "        app.Exit()",
    "    except BaseException:",
    "        pass",
]


def default_log_path(tag: str = "script") -> str:
    """给脚本日志一个默认可写的临时路径（工具一般会显式传并回传给调用方）。"""
    fd, path = tempfile.mkstemp(prefix=f"laspcem_{tag}_", suffix=".log")
    os.close(fd)
    return path


def render_script(steps: list[tuple[str, list[str]]], log_path: str,
                  exit_at_end: bool = True) -> str:
    """把 (步骤名, 代码行) 列表渲染成带日志与异常捕获的完整脚本。

    exit_at_end=False：求解用 —— 脚本结束后 GUI 要继续活着跑异步求解器。
    """
    # ⚠️ 前两行必须在最前面：宿主靠 `#Version Information:` 选脚本版本
    lines = [
        "#Version Information: LASPCEM(28167) Sep 23 2026",
        f"app.SetScriptVersion({_SCRIPT_VERSION})",
    ]
    lines += [ln.replace("LOGPATH", repr(log_path)) for ln in _WRAPPER_HEAD]
    for label, code in steps:
        lines.append(f"    _w('BEGIN {label}')")
        lines.extend(f"    {ln}" for ln in code)
        lines.append(f"    _w('OK {label}')")
    lines.extend(_WRAPPER_EXCEPT)
    if exit_at_end:
        lines.extend(_WRAPPER_EXIT)
    return "\n".join(lines) + "\n"


def _display_setup_name(setup_name: str) -> str:
    """`Setup1(FEM)` → `Setup1`：GUI 的 Analyze 录出来用的是显示名。"""
    base, _algo = split_setup_name(setup_name)
    return base


def _solve_steps(sem_path: str, setup_name: str) -> list[tuple[str, list[str]]]:
    """打开工程 → 求解（不 SaveAs、不 Exit）。"""
    p = sem_path.replace("\\", "/")
    return [
        ("open", [f'app.Open("{p}")']),
        ("select-mode", ["viewer.SetSelectionMode(3)"]),
        ("analyze", [f"cem.FEMAnalyze({_display_setup_name(setup_name)!r}, {{'Redo Mesh': True}})"]),
    ]


def _open_steps(sem_path: str) -> list[tuple[str, list[str]]]:
    """只打开工程（工程必须已求解）。"""
    return [("open", [f'app.Open("{sem_path.replace(chr(92), "/")}")']),
            ("select-mode", ["viewer.SetSelectionMode(3)"])]


def _solution_name(solution_name: str, setup_name: str) -> str:
    """解名称：调用方给的优先，否则 = 算法_<setup 基名>（`Setup1(FEM)` → `FEM_Setup1`）。"""
    if (solution_name or "").strip():
        return solution_name.strip()
    base, algo = split_setup_name(setup_name)
    return f"{algo}_{base}"


def build_solve_script(sem_path: str, setup_name: str = "Setup1", log_path: str = "") -> str:
    """生成求解脚本 —— **逐字照抄 GUI 录制**，不做任何包装。

    实测（2026-11-12）：给这份脚本套日志包装（import traceback + def _w + try/except）后，
    同一个工程 2–4 分钟都拉不起求解器；拆回录制原样后 40 秒就起来 mpiexec + 2×phofem。
    所以求解脚本必须保持这个最小形态；完成判定与日志由工具在**进程外面**做
    （`launcher._wait_for_solve` 轮询求解器进程 + `.sem` 静默）。

    录制原样（setup 用显示名，`.sem` 里存的 `Setup1(FEM)` 不能用）：

        #Version Information: LASPCEM(28167) Sep 23 2026
        app.SetScriptVersion(...)
        app.Open(".../x.sem")
        viewer.SetSelectionMode(3)
        cem.FEMAnalyze("Setup1",{'Redo Mesh':True})

    注：`log_path` 参数保留仅为兼容旧调用，本函数**不写**任何日志。

    ⚠️ 不提供「强制重解」选项：GUI 录制里的 `cem.CleanUpFEMSolution(...)` 实测不可靠
    （三次只有一次真的删掉结果，其余两次 `.sem` 里 FEM_n 仍在、Analyze 随即被跳过），
    而且它是破坏性的（先删旧解，中途失败会留下无解工程）。
    需要重算时让模型真的发生变化（改变量/几何）即可，LASPCEM 会自己判定需要重算。
    """
    p = sem_path.replace("\\", "/")
    lines = [
        "#Version Information: LASPCEM(28167) Sep 23 2026",
        f"app.SetScriptVersion({_SCRIPT_VERSION})",
        f'app.Open("{p}")',
        "viewer.SetSelectionMode(3)",
    ]
    lines += [
        f"cem.FEMAnalyze({_display_setup_name(setup_name)!r},{{",
        "        'Redo Mesh':True",
        "    })",
        "",
    ]
    return "\n".join(lines)


def build_import_aedt_script(
    aedt_path: str,
    output_dir: str = "",
    ansys_version: str = "2020.2",
    with_geometry_parameters: bool = True,
    delete_operand_parts: bool = True,
    delete_coordinate_systems: bool = False,
    background: bool = True,
    compatible: bool = True,
    log_path: str = "",
) -> str:
    """生成「ANSYS .aedt → LASPCEM .sem」导入脚本 —— 逐字照抄 GUI 录制（桌面 `1112.py`）。

    录制原样：

        app.New(0)
        viewer.SetSelectionMode(3)
        guiframe3.CloseFile("Untitled 0")
        aedt.Import({'SourceFile':…, 'OutputDir':…, 'WithGeometryParameters':True,
                     'AnsysEM':'2020.2', 'DeleteOperandParts':True,
                     'DeleteCoordinateSystems':False, 'Background':True, 'Compatible':True})

    ⚠️ `Background: True` 时导入是**异步**的，脚本跑完不代表 `.sem` 已生成完毕，
    调用方必须轮询 OutputDir 里新出现的 `.sem`（`launcher._wait_for_new_sem`）。
    ⚠️ 导出文件名由 aedt 里的 design 名决定（`CPWG-1.aedt` → `CPWG-1-HFSSDesign1.sem`），
    不能按输入文件名硬推。
    """
    src = aedt_path.replace("\\", "/")
    out = (output_dir or str(Path(aedt_path).parent)).replace("\\", "/")
    steps = [
        ("new", ["app.New(0)"]),
        ("select-mode", ["viewer.SetSelectionMode(3)"]),
        ("close-untitled", ['guiframe3.CloseFile("Untitled 0")']),
        ("import", [
            "aedt.Import({",
            f"        'SourceFile':'{src}',",
            f"        'OutputDir':'{out}',",
            f"        'WithGeometryParameters':{with_geometry_parameters},",
            f"        'AnsysEM':'{ansys_version}',",
            f"        'DeleteOperandParts':{delete_operand_parts},",
            f"        'DeleteCoordinateSystems':{delete_coordinate_systems},",
            f"        'Background':{background},",
            f"        'Compatible':{compatible}",
            "    })",
        ]),
    ]
    # 不能带 Exit：Background:True 时导入在 CPU 侧异步进行，脚本一退出 GUI 就把导入掐了。
    # 但**要带异常捕获** —— 否则导入失败（.aedt 几何不兼容等）只能看到"没产物"，
    # 拿不到 GUI 里那句 "Import AEDT file failed" 的 traceback。
    return render_script(steps, log_path or default_log_path("import"), exit_at_end=False)


def build_save_script(sem_path: str) -> str:
    """生成「打开工程 → SaveAs 回原路径」脚本 —— 用来把求解结果写回顶层 `.sem`。

    实测（2026-11-12）：`cem.FEMAnalyze` 跑完后结果先落在 `<stem>/1_Result/` 的工作副本里，
    **顶层 `.sem` 仍是求解前状态**（`is_solved=False`）。补一次 Open + SaveAs 才会把结果合并回去。
    脚本里不跟 Analyze、不跟 Exit（Exit 常常不退出，由调用方 terminate）。
    """
    p = sem_path.replace("\\", "/")
    return "\n".join([
        "#Version Information: LASPCEM(28167) Sep 23 2026",
        f"app.SetScriptVersion({_SCRIPT_VERSION})",
        f'app.Open("{p}")',
        "viewer.SetSelectionMode(3)",
        f'app.SaveAs("{p}")',
        "",
    ])


def build_export_snp_script(
    sem_path: str,
    output_path: str,
    setup_name: str = "Setup1",
    solution_name: str = "",
    sweeping_parameters: dict | None = None,
    overriding_parameters: dict | None = None,
    log_path: str = "",
) -> str:
    """生成「打开已求解工程 → 导出 S 参数」脚本。

    求解是异步的、且必须由调用方先等它跑完（见 `run_laspcem_solve`），所以这里不含 Analyze。
    ⚠️ LASPCEM 按端口数命名 Touchstone：请求 `x.sNp`，2 端口工程会落地 `x.s2p`。
    """
    steps = _open_steps(sem_path)
    sn = _solution_name(solution_name, setup_name)
    steps.append(("export-snp", [
        "report.ExportSnpFile({",
        f"    'File Path': {output_path.replace(chr(92), '/')!r},",
        f"    'Solution Name': {sn!r},",
        f"    'Sweeping Parameters': {(sweeping_parameters or {})!r},",
        f"    'Overriding Parameters': {(overriding_parameters or {})!r},",
        "})",
    ]))
    return render_script(steps, log_path or default_log_path("export"))


def build_report_script(
    sem_path: str,
    setup_name: str = "Setup1",
    report_name: str = "Sparameter_1",
    solution_name: str = "",
    source_name: str = "Source1",
    quantity: tuple[str, ...] = (),
    port_count: int = 2,
    sweeping_parameters: dict | None = None,
    overriding_parameters: dict | None = None,
    log_path: str = "",
) -> str:
    """生成「打开已求解工程 → 建网络报告 → 保存」脚本。

    `quantity` 留空时按 `port_count` 生成全部 S(i,j)（顺序与录制一致：
    S(1:1,1:1), S(2:1,1:1), S(1:1,2:1), S(2:1,2:1)…）——写死 2 端口会让四端口模型静默少画线。
    """
    n = max(1, int(port_count or 2))
    if not quantity:
        quantity = tuple(f"S({i}:1,{j}:1)" for j in range(1, n + 1) for i in range(1, n + 1))
    steps = _open_steps(sem_path)
    sn = _solution_name(solution_name, setup_name)
    steps.append(("create-report", [
        "report.CreateNetworkPlot({",
        f"    'Name': {report_name!r},",
        f"    'Solution Name': {sn!r},",
        f"    'Source Name': {source_name!r},",
        "    'Function': ['dB'],",
        f"    'Quantity': {list(quantity)!r},",
        "    'Parameter': 'Sparameter',",
        "    'Create Type': 'Network Report',",
        "    'Specific Type': 'Rectangular Plot',",
        "    'X': 'Freq',",
        f"    'Sweeping Parameters': {(sweeping_parameters or {})!r},",
        f"    'Overriding Parameters': {(overriding_parameters or {})!r},",
        "    'Parameter Type': 'Normal Parameter',",
        "})",
    ]))
    steps.append(("save", [f'app.SaveAs("{sem_path.replace(chr(92), "/")}")']))
    return render_script(steps, log_path or default_log_path("report"))
