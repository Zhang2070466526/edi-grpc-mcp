"""CST 作业执行器 —— 在 CST 自带解释器里运行的独立进程（Python 3.6）。

为什么需要它：CST 官方 cst 库带版本闸门（cst/__init__.py 只接受 3.6~3.9），
而本服务跑在 Python 3.12 上，同进程 sys.path.insert + import 必失败：
    CST_SOLVE_FAILED: The cst package supports only Python 3.6/3.7/3.8/3.9
所以「真正触碰 CST 的调用」（开会话 / 求解 / 读结果 / VBA 导出）全部挪到本进程，
由 CST 自带的 AMD64\\python\\python.exe 启动，作业参数与结果走 JSON 文件。

用法（由主服务调用，别手工跑）：
    <CST>\\AMD64\\python\\python.exe cst_worker.py --job <job.json>

job.json:
    {"op": "solve" | "export_snp" | "export_farfield",
     "model_path": "...", "result_path": "<结果 JSON 写出路径>",
     "output_dir": "...",  # 仅 export_* 使用
     "port_count": 2}      # 仅 export_snp 使用，可为 null

结果 JSON：
    成功 {"success": true, "op": ..., "result": <路径或路径列表>, "elapsed_seconds": ...}
    失败 {"success": false, "op": ..., "error": "...", "error_type": "...", "traceback": "..."}

⚠️ 本文件必须保持 Python 3.6 语法 —— 禁用 `from __future__ import annotations`（3.7+ 才有）、
海象运算符、dataclasses、`subprocess.run(capture_output=)`、PEP 585 泛型注解（`list[str]`）等。
且只能用标准库（CST 自带解释器里的 numpy 是坏的：新版 numpy 用了 := ，3.6 解析不了）。
tests/test_cst_worker.py 用 ast.parse(feature_version=(3, 6)) + 代码 token 黑名单把这条约束锁住。
"""

import cmath
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
import winreg

# 远场方向图导出的 VBA 宏（Sub Main 格式）。
# 踩坑要点（实测）：
#   1. 必须用 prj.schematic.execute_vba_code 执行，add_to_history 导不出远场（farfield txt=0）。
#   2. 不能加 FarfieldPlot.Plot / Reset / Plottype —— 加了反而报 "No plot data available for export"。
#   3. SelectTreeItem 的路径必须带 "[1]"（选中绘图节点，不是文件夹）。
_FARFIELD_VBA = r"""
Sub Main
    Dim paths As Variant, types As Variant, files As Variant, info As Variant, nResults As Long
    nResults = Resulttree.GetTreeResults("Farfields","farfield","",paths,types,files,info)
    Dim n As Long
    Dim strString As String
    For n = 0 To nResults-1
        strString = Split(paths(n), "\")(1)
        strString = Replace(strString, " [1]", "")
        SelectTreeItem(Cstr(paths(n)))
        With ASCIIExport
            .Reset
            .FileName("OUTDIR" + "\" + strString + ".txt")
            .Execute
        End With
    Next
End Sub
"""

_CST_TMP_TTL_SECONDS = 24 * 3600


# ── CST 库定位与加载 ──

def cst_api_path() -> str:
    """从注册表定位 CST 的 python_cst_libraries 目录。"""
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\CST DESIGN ENVIRONMENT_AMD64.exe"
    exe_path = ""
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view) as key:
                exe_path, _ = winreg.QueryValueEx(key, "")
                if exe_path:
                    break
        except OSError:
            continue
    if not exe_path:
        raise RuntimeError("CST not found in registry (%s)" % key_path)
    api_path = os.path.join(os.path.dirname(str(exe_path).strip().strip('"')), "python_cst_libraries")
    if not os.path.isdir(api_path):
        raise RuntimeError("CST python_cst_libraries not found: %s" % api_path)
    return api_path


def load_cst():
    """加载 cst.interface / cst.results，返回 (interface_module, results_module)。"""
    api_path = cst_api_path()
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    import cst.interface
    import cst.results
    return cst.interface, cst.results


# ── 可写副本 ──

def _cleanup_stale_tmp_copies(tmp_dir: str, ttl_seconds: int) -> None:
    """清理超过 TTL 的临时副本（只读模型副本按需创建，避免无限堆积）。"""
    now = time.time()
    try:
        names = os.listdir(tmp_dir)
    except OSError:
        return
    for name in names:
        p = os.path.join(tmp_dir, name)
        try:
            if now - os.path.getmtime(p) > ttl_seconds:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
        except OSError:
            pass


def to_writable_copy(model_path: str) -> str:
    """模型只读时复制到临时可写副本，返回可写模型路径（连同结果目录一起复制）。"""
    if os.access(model_path, os.W_OK):
        return model_path
    tmp_dir = os.path.join(tempfile.gettempdir(), "cst_solve")
    os.makedirs(tmp_dir, exist_ok=True)
    _cleanup_stale_tmp_copies(tmp_dir, _CST_TMP_TTL_SECONDS)
    tmp_model = os.path.join(
        tmp_dir,
        "%s_%s" % (hashlib.md5(model_path.encode()).hexdigest()[:8], os.path.basename(model_path)),
    )
    shutil.copyfile(model_path, tmp_model)
    src_dir = os.path.splitext(model_path)[0]
    dst_dir = os.path.splitext(tmp_model)[0]
    if os.path.isdir(src_dir):
        if os.path.isdir(dst_dir):
            shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns('*.lok'))
    return tmp_model


# ── 会话管理（一次性） ──

def open_session(model_path: str):
    """连接/启动 CST 并打开模型，返回 (de, prj)。"""
    model_path = os.path.abspath(str(model_path))
    if not os.path.isfile(model_path):
        raise RuntimeError("Model file not found: %s" % model_path)
    cst_interface, _ = load_cst()
    # 用官方 API 启动/连接：只有它启动的 CST 才会在 run_solver 后自动加载
    # 场可视化 hex 网格，后续导出远场才不报 "No plot data"。
    de = cst_interface.DesignEnvironment.connect_to_any_or_new()
    try:
        prj = de.open_project(model_path)
    except Exception:
        # open_project 抛异常时也要关闭环境，避免 CST/COM 会话泄漏
        de.close()
        raise
    if prj is None:
        de.close()
        raise RuntimeError("open_project returned None: %s" % model_path)
    return de, prj


def close_session(de, prj) -> None:
    """关闭项目与环境（best-effort）。"""
    for obj in (prj, de):
        try:
            obj.close()
        except Exception:
            pass


# ── 结果读取辅助（与主服务侧解析口径一致） ──

def s_param_indices(item_path: str):
    """从 S 参数项路径提取端口号 (i, j)；无法解析返回 (0, 0)。

    用宽松正则（容忍空格/逗号差异，如 "S1,1"、"S1, 1"、"S 1,1"），
    排序与提取共用同一套解析，避免两套正则漂移导致合法项被丢弃。
    """
    stem = item_path.rsplit("\\", 1)[-1]
    m = re.search(r"S\s*(\d+)\s*,?\s*(\d+)", stem)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2)))


def freq_unit(xlabel) -> str:
    """从 x 轴标签提取频率单位。"""
    text = str(xlabel or "")
    for unit in ("GHz", "MHz", "kHz"):
        if unit in text:
            return unit
    return "Hz"


def has_farfield(tree_items) -> bool:
    """结果树里是否存在远场（Farfield）结果。"""
    return any("Farfield" in t for t in tree_items)


def write_touchstone(output_path, freq, sp_data, port_count, freq_unit_name, ref_z) -> str:
    """把 S 参数写成 Touchstone 文件（cmath 代替 numpy，3.6 且无 numpy 也可用）。"""
    lines = ["! Touchstone file exported by cst",
             "# {} S MA R {:.1f}".format(freq_unit_name, ref_z)]
    for idx in range(len(freq)):
        vals = []
        for j in range(1, port_count + 1):
            for i in range(1, port_count + 1):
                z = complex(sp_data[(i, j)][idx])
                vals.append("{:.10e} {:.10e}".format(abs(z), cmath.phase(z) * 180.0 / cmath.pi))
        lines.append("  ".join(["{:.10e}".format(freq[idx])] + vals))
    with open(output_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return output_path


def open_3d_results(model_path: str):
    """无会话打开 .cst 的 3D 结果模块，返回 (project_file, result_3d)。"""
    _, cst_results = load_cst()
    project_file = cst_results.ProjectFile(model_path, allow_interactive=True)
    return project_file, project_file.get_3d()


def close_3d_results(project_file) -> None:
    """释放 ProjectFile 结果文件句柄（best-effort，老版本可能无 close）。"""
    close = getattr(project_file, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            pass


# ── 作业：求解 ──

def do_solve(job):
    """求解模型（一次性会话），返回求解后的模型路径。"""
    model_path = to_writable_copy(os.path.abspath(str(job["model_path"])))
    de, prj = open_session(model_path)
    try:
        if prj.modeler.run_solver() is False:
            raise RuntimeError("run_solver returned False")
        prj.save()
    finally:
        close_session(de, prj)
    return model_path


# ── 作业：S 参数导出（无会话） ──

def do_export_snp(job):
    """读取求解后的 .cst，导出 S 参数为 Touchstone .sNp，返回文件路径。"""
    model_path = os.path.abspath(str(job["model_path"]))
    output_dir = job.get("output_dir") or ""
    port_count = job.get("port_count")
    project_file, result_3d = open_3d_results(model_path)
    try:
        tree_items = result_3d.get_tree_items()
        s_items = sorted([t for t in tree_items if "S-Parameters" in t], key=s_param_indices)
        if not s_items:
            raise RuntimeError("No S-Parameter results found: %s" % tree_items)

        sp_map = {}
        for item in s_items:
            i, j = s_param_indices(item)
            if i >= 1 and j >= 1:
                sp_map[(i, j)] = item
        if not sp_map:
            raise RuntimeError("No valid S-Parameter items parsed: %s" % s_items)

        if port_count is None:
            port_count = max(max(i, j) for i, j in sp_map.keys())
        port_count = int(port_count)
        if port_count < 1:
            raise RuntimeError("port_count 必须是正整数，收到 %s" % port_count)

        stem = os.path.splitext(os.path.basename(model_path))[0]
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, stem + ".s%dp" % port_count)
        else:
            output_path = os.path.splitext(model_path)[0] + ".s%dp" % port_count

        expected = set()
        for j in range(1, port_count + 1):
            for i in range(1, port_count + 1):
                expected.add((i, j))
        missing = expected - set(sp_map.keys())
        if missing:
            raise RuntimeError("Incomplete S-matrix: missing %s" % sorted(missing))

        run_id = 0
        for r in result_3d.get_all_run_ids():
            # run id 可能带前缀/后缀（不同 CST 版本格式差异），用正则提取数字
            m = re.search(r"\d+", str(r))
            if m:
                run_id = max(run_id, int(m.group()))

        freq = None
        unit = "Hz"
        sp_data = {}
        for (i, j) in sorted(sp_map.keys()):
            item_path = sp_map[(i, j)]
            item = result_3d.get_result_item(item_path, run_id=run_id)
            xdata, ydata = item.get_xdata(), item.get_ydata()
            if freq is None:
                freq = list(xdata)
                unit = freq_unit(item.xlabel)
            sp_data[(i, j)] = list(ydata)

        return write_touchstone(output_path, freq, sp_data, port_count, unit, ref_z=50.0)
    finally:
        close_3d_results(project_file)


# ── 作业：远场导出（需会话，自动判断是否已求解） ──

def detect_farfield(model_path):
    """无会话检测是否已有远场结果，返回 (needs_solve, note)。

    归档（未解包）的 .cst 读不了结果树 —— CST 原话：
    `Archived CST files are not supported. Please open the file in CST Studio Suite to unpack it`。
    这种情况**不能直接失败**：下一步开会话 `run_solver` 会顺带把它解包，
    所以按「需要求解」处理，并把原因放进 note 便于诊断（2026-09-24 实测踩到）。
    """
    try:
        project_file, result_3d = open_3d_results(model_path)
    except Exception as exc:
        return True, "结果树不可读（多为归档未解包，将先求解解包）：%s" % exc
    try:
        return (not has_farfield(result_3d.get_tree_items())), ""
    finally:
        close_3d_results(project_file)


def do_export_farfield(job):
    """导出远场方向图，自动判断是否已求解，返回 txt 路径列表。

    流程：无会话检测结果树（读不了就按「未解」处理）→ 无远场结果则先
    run_solver + save → execute_vba 导出。返回空列表 = 模型里没有 farfield 结果。
    """
    model_path = to_writable_copy(os.path.abspath(str(job["model_path"])))
    output_dir = job.get("output_dir") or os.path.dirname(model_path)
    os.makedirs(output_dir, exist_ok=True)

    needs_solve, note = detect_farfield(model_path)
    if note:
        # 诊断行：父进程只在作业失败时读 stdout/stderr
        print("cst_worker: %s" % note)

    de, prj = open_session(model_path)
    try:
        if needs_solve:
            if prj.modeler.run_solver() is False:
                raise RuntimeError("run_solver returned False")
            prj.save()
        vba = _FARFIELD_VBA.replace("OUTDIR", output_dir)
        if prj.schematic.execute_vba_code(vba) is False:
            raise RuntimeError("export_farfield: execute_vba_code returned False")
        return sorted(glob.glob(os.path.join(output_dir, "*.txt")))
    finally:
        close_session(de, prj)


_OPS = {
    "solve": do_solve,
    "export_snp": do_export_snp,
    "export_farfield": do_export_farfield,
}


def run_job(job):
    """执行一个作业，返回结果 JSON（不抛异常，失败也落盘）。"""
    op = str(job.get("op") or "")
    started = time.time()
    payload = {"op": op, "success": False, "result": None, "elapsed_seconds": 0.0}
    try:
        func = _OPS.get(op)
        if func is None:
            raise RuntimeError("Unknown op: %r (supported: %s)" % (op, sorted(_OPS)))
        payload["result"] = func(job)
        payload["success"] = True
    except Exception as exc:
        payload["error"] = str(exc)
        payload["error_type"] = type(exc).__name__
        payload["traceback"] = traceback.format_exc()
    payload["elapsed_seconds"] = round(time.time() - started, 3)
    return payload


def _parse_args(argv):
    """极简参数解析：--job <path>（只认这一种形态，避免引入 argparse 之外的依赖）。"""
    job_path = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--job" and i + 1 < len(argv):
            job_path = argv[i + 1]
            i += 2
            continue
        i += 1
    if not job_path:
        raise SystemExit("usage: cst_worker.py --job <job.json>")
    return job_path


def main(argv=None):
    """入口：读 job.json → 执行 → 写 result.json。"""
    argv = sys.argv[1:] if argv is None else argv
    job_path = _parse_args(argv)
    # 作业文件由主服务按 UTF-8 写出，这里必须显式按 UTF-8 读（中文路径不能靠本地代码页猜）
    with open(job_path, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    result_path = job.get("result_path")
    out = run_job(job)
    if result_path:
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
    # 同时把结果打到 stdout，便于父进程在结果文件缺失时兜底诊断。
    # 这里用 ensure_ascii=True：CST 解释器的 stdout 在中文 Windows 上是 cp936，
    # 直接吐非 ASCII 字符会 UnicodeEncodeError 把整个作业崩掉。
    sys.stdout.write("CST_JOB_RESULT=" + json.dumps(out, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
