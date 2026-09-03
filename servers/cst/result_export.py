"""CST 结果导出工具 —— 导出 S 参数（无会话）与远场方向图（需会话，自动判断求解）。"""

from __future__ import annotations

import glob
import os
import re
from typing import Any

import numpy as np

from servers import mcp
from servers.utils import error_response, validate_file, submitted_response, queue_full_response
from servers.cst.cst_api import CstApi, cst_runner, query_task, to_writable_copy

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


class CstResultExporter:
    """CST 结果导出器 —— S 参数（无会话）与远场方向图（需会话，自动判断求解）。"""

    def __init__(self) -> None:
        self._api = CstApi()

    # ── 公共辅助 ──

    def _open_3d_results(self, model_path: str):
        """无会话打开 .cst 的 3D 结果模块，返回 (project_file, result_3d)。

        S 参数导出与远场结果检测共用。project_file 持有结果文件句柄，
        用完后必须调 _close_3d_results 释放（避免句柄泄漏、锁住 .cst 文件）。
        """
        _, cst_results = self._api.load()
        project_file = cst_results.ProjectFile(model_path, allow_interactive=True)
        return project_file, project_file.get_3d()

    @staticmethod
    def _close_3d_results(project_file) -> None:
        """释放 ProjectFile 结果文件句柄（best-effort，老版本可能无 close）。"""
        close = getattr(project_file, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _s_param_indices(item_path: str) -> tuple[int, int]:
        """从 S 参数项路径提取端口号 (i, j)；无法解析返回 (0, 0)。

        用宽松正则（容忍空格/逗号差异，如 "S1,1"、"S1, 1"、"S 1,1"），
        排序与提取共用同一套解析，避免两套正则漂移导致合法项被丢弃。
        """
        stem = item_path.rsplit("\\", 1)[-1]
        m = re.search(r"S\s*(\d+)\s*,?\s*(\d+)", stem)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    @staticmethod
    def _freq_unit(xlabel) -> str:
        """从 x 轴标签提取频率单位。"""
        text = str(xlabel or "")
        for unit in ("GHz", "MHz", "kHz"):
            if unit in text:
                return unit
        return "Hz"

    @staticmethod
    def _write_touchstone(output_path, freq, sp_data, port_count, freq_unit, ref_z) -> str:
        """把 S 参数写成 Touchstone 格式文件。"""
        lines = ["! Touchstone file exported by cst",
                 "# {} S MA R {:.1f}".format(freq_unit, ref_z)]
        for idx in range(len(freq)):
            vals = []
            for j in range(1, port_count + 1):
                for i in range(1, port_count + 1):
                    z = complex(sp_data[(i, j)][idx])
                    vals.append("{:.10e} {:.10e}".format(abs(z), np.angle(z, deg=True)))
            lines.append("  ".join(["{:.10e}".format(freq[idx])] + vals))
        with open(output_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return output_path

    # ── S 参数导出（无会话） ──

    def export_snp(self, model_path: str, output_dir: str, port_count: int | None) -> str:
        """读取求解后的 .cst，导出 S 参数为 Touchstone .sNp，返回文件路径。"""
        model_path = os.path.abspath(str(model_path))
        project_file, result_3d = self._open_3d_results(model_path)
        try:
            return self._export_snp_from_3d(model_path, output_dir, port_count, result_3d)
        finally:
            self._close_3d_results(project_file)

    def _export_snp_from_3d(self, model_path, output_dir, port_count, result_3d) -> str:
        """用已打开的 result_3d 导出 S 参数（由 export_snp 调用，负责释放句柄）。"""
        tree_items = result_3d.get_tree_items()

        s_items = sorted([t for t in tree_items if "S-Parameters" in t], key=self._s_param_indices)
        if not s_items:
            raise RuntimeError("No S-Parameter results found: %s" % tree_items)

        sp_map = {}
        for item in s_items:
            i, j = self._s_param_indices(item)
            if i >= 1 and j >= 1:
                sp_map[(i, j)] = item

        if not sp_map:
            raise RuntimeError("No valid S-Parameter items parsed: %s" % s_items)
        if port_count is None:
            port_count = max(max(i, j) for i, j in sp_map.keys())
        if port_count < 1:
            raise RuntimeError("port_count 必须是正整数，收到 %s" % port_count)

        stem = os.path.splitext(os.path.basename(model_path))[0]
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, stem + ".s{}p".format(port_count))
        else:
            output_path = os.path.splitext(model_path)[0] + ".s{}p".format(port_count)

        expected = {(i, j) for j in range(1, port_count + 1) for i in range(1, port_count + 1)}
        missing = expected - set(sp_map.keys())
        if missing:
            raise RuntimeError("Incomplete S-matrix: missing %s" % sorted(missing))

        run_ids = result_3d.get_all_run_ids()
        run_id = 0
        for r in run_ids:
            # run id 可能带前缀/后缀（不同 CST 版本格式差异），用正则提取数字
            m = re.search(r"\d+", str(r))
            if m:
                run_id = max(run_id, int(m.group()))

        freq = None
        sp_data = {}
        for (i, j), item_path in sorted(sp_map.items()):
            item = result_3d.get_result_item(item_path, run_id=run_id)
            xdata, ydata = item.get_xdata(), item.get_ydata()
            if freq is None:
                freq = list(xdata)
                freq_unit = self._freq_unit(item.xlabel)
            sp_data[(i, j)] = list(ydata)

        return self._write_touchstone(output_path, freq, sp_data, port_count, freq_unit, ref_z=50.0)

    # ── 远场方向图导出（需会话，自动判断求解） ──

    def _has_farfield_result(self, model_path: str) -> bool:
        """无会话读结果树，判断模型是否已存在远场（Farfield）结果。"""
        project_file, result_3d = self._open_3d_results(model_path)
        try:
            tree_items = result_3d.get_tree_items()
            return any("Farfield" in t for t in tree_items)
        finally:
            self._close_3d_results(project_file)

    def _run_farfield_vba(self, prj, output_dir: str) -> list[str]:
        """在已打开的会话内执行 VBA 宏导出远场，返回 txt 路径列表。"""
        os.makedirs(output_dir, exist_ok=True)
        vba = _FARFIELD_VBA.replace("OUTDIR", output_dir.replace("\\", "\\\\"))
        if prj.schematic.execute_vba_code(vba) is False:
            raise RuntimeError("export_farfield: execute_vba_code returned False")
        return sorted(glob.glob(os.path.join(output_dir, "*.txt")))

    def export_farfield(self, model_path: str, output_dir: str) -> list[str]:
        """导出远场方向图，自动判断是否已求解，返回 txt 路径列表。

        流程：无会话检测结果树 → 无远场结果则先 run_solver + save → execute_vba 导出。
        """
        model_path = to_writable_copy(os.path.abspath(str(model_path)))
        needs_solve = not self._has_farfield_result(model_path)

        de, prj = self._api.open_session(model_path)
        try:
            if needs_solve:
                if prj.modeler.run_solver() is False:
                    raise RuntimeError("run_solver returned False")
                prj.save()
            return self._run_farfield_vba(prj, output_dir)
        finally:
            self._api.close_session(de, prj)


_result_exporter = CstResultExporter()


@mcp.tool()
def cst_export_snp(
    model_path: str,
    output_dir: str = "",
    port_count: int | None = None,
) -> dict[str, Any]:
    """导出 CST 模型的 S 参数为 Touchstone .sNp 文件（需先求解）。

    用法："把这个 CST 模型的 S 参数导出来"

    直接读取求解后的结果文件，不需要打开会话。导出后可通过文件路径
    查看或进一步处理。

    Args:
        model_path: .cst 模型文件绝对路径（需已求解，存在 S 参数结果）。
        output_dir: 导出目录（空则默认导出到模型同级目录）。
        port_count: 端口数（None 则自动从结果推断）。

    Returns:
        {"success": True, "snp_path": "C:/.../xxx.s2p", "message": "S 参数已导出"}
    """
    resolved = validate_file(model_path, (".cst",))
    # 走 cst_runner 串行执行：与求解/远场导出的会话互斥，避免无会话读结果时
    # 与正在求解的会话并发（读到半写结果或 CST 单实例冲突）
    try:
        snp_path = cst_runner.run_sync(_result_exporter.export_snp, resolved, output_dir, port_count)
    except Exception as exc:
        return error_response("CST_EXPORT_FAILED", str(exc))
    return {"success": True, "snp_path": snp_path, "message": "S 参数已导出"}


@mcp.tool()
def cst_export_farfield(model_path: str, output_dir: str = "") -> dict[str, Any]:
    """导出 CST 模型的远场方向图为 ASCII .txt（自动判断是否已求解）。

    用法："把这个模型的远场方向图导出来"

    先无会话检测模型是否已求解：已有远场结果则直接导出；否则先 run_solver
    求解再导出。求解可能耗时数分钟，任务在后台串行执行，通过
    cst_export_farfield_query 查询进度和结果。

    Args:
        model_path: .cst 模型文件绝对路径（模型需配置 farfield 监视器）。
        output_dir: 导出目录（空则默认模型同级目录）。

    Returns:
        {"success": True, "task_id": "cst-a1b2...", "status": "QUEUED"}
    """
    resolved = validate_file(model_path, (".cst",))
    if not output_dir:
        output_dir = os.path.dirname(os.path.abspath(resolved))
    task_id = cst_runner.submit(_result_exporter.export_farfield, resolved, output_dir)
    if task_id is None:
        return queue_full_response("CST_QUEUE_FULL", message="当前已有 CST 任务在进行，请稍后重试")
    return submitted_response(task_id, message="远场导出任务已提交")


@mcp.tool()
def cst_export_farfield_query(task_id: str) -> dict[str, Any]:
    """查询远场导出任务：返回进度，完成时附带 farfield_txts。

    一次调用同时拿到进度和结果，无需先查 status 再取 result。

    Args:
        task_id: cst_export_farfield 返回的 task_id。
    """
    return query_task(
        task_id, cst_runner.snapshot(task_id),
        not_found_msg="CST 远场导出任务不存在、已过期或服务已重启",
        running_msg="导出进行中",
        success_key="farfield_txts",
        success_msg="远场方向图已导出",
        fail_code="CST_EXPORT_FARFIELD_FAILED",
        fail_msg="导出失败",
    )
