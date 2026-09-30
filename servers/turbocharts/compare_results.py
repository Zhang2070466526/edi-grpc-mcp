"""仿真结果对比工具 — 2-8 个 RAW 文件同曲线对比叠图。

流程：逐 RAW 调 turbocharts 导出 CSV → 依赖轴对齐（intersection/interpolation）
→ Matplotlib 叠图 → 计算 max/mean/rms 差异 → 可选 CSV 导出
"""

from __future__ import annotations

import csv
import logging
import math
import os
import tempfile
from pathlib import Path
from typing import Any

_logger = logging.getLogger("turbocharts.compare")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from servers.eda.config import TURBOCHARTS_PATH
from servers.utils import build_artifact, build_file_link, error_response, per_tool_mutex
from servers.turbocharts.config import engine_failures, engine_report, record_run, run_turbocharts
from servers.turbocharts.convert_raw import detect_chart_type
from servers import mcp


@mcp.tool()
@per_tool_mutex
def compare_simulation_results(
    result_paths: list[str],
    curve: str,
    output_path: str,
    chart_type: str = "",
    labels: list[str] | None = None,
    dependency: str = "freq",
    csv_path: str = "",
    alignment: str = "intersection",
    reference_index: int = 0,
) -> dict[str, Any]:
    """比较多个 RAW 仿真结果文件中的同一条曲线并生成对比图。

    逐 RAW 调用 turbocharts 导出 CSV 后，读取真实列名，
    按依赖轴对齐，用 Matplotlib 生成叠图并计算差异指标。

    Args:
        result_paths: RAW 文件路径列表（2-8 个）。
        curve: 曲线名，如 "DB_S[2,1]"。
        output_path: 对比图输出路径。
        chart_type: 图表类型 SP / HB / XDB；默认空 = 自动从 RAW 的 Plotname 推断
            （必须与数据一致：HB 数据传 "SP" 会白跑）。
        labels: 每个结果文件的标签，默认使用文件名。
        dependency: 依赖轴名称，默认 "freq"。
        csv_path: 可选，对比数据 CSV 输出路径。
        alignment: 对齐方式，"intersection"(交集) 或 "interpolation"(插值)。
        reference_index: interpolation 模式下的参考文件索引。
    """
    if not Path(TURBOCHARTS_PATH).is_file():
        return error_response("FILE_NOT_FOUND", f"turbocharts_app.exe 不存在: {TURBOCHARTS_PATH}")

    file_count = len(result_paths)
    if file_count < 2 or file_count > 8:
        return error_response("INVALID_PARAMETERS", "result_paths 需要 2-8 个文件")

    if alignment not in ("intersection", "interpolation"):
        return error_response("INVALID_PARAMETERS", "alignment 必须是 intersection 或 interpolation")

    if reference_index < 0 or reference_index >= file_count:
        return error_response("INVALID_PARAMETERS", f"reference_index={reference_index} 超出范围（0-{file_count - 1}）")

    for raw_path in result_paths:
        if not Path(raw_path).is_file():
            return error_response("FILE_NOT_FOUND", f"RAW 文件不存在: {raw_path}")

    # chart_type 必须与 RAW 的 Plotname 一致，否则 turbocharts 会「成功但不出数据」；
    # 没传就按第一个 RAW 自动识别（2026-09-28 体检 GW-2），识别不出退回旧默认 SP 并告警。
    # 另外**逐文件**探测类型：混用不同数据集时后续文件必然白跑（同一个 curve 名不可能同时
    # 存在于两种数据集，实测引擎对错配类型还会直接 0xC0000005 崩溃）⇒ 显式记进 notes。
    file_types: dict[str, str] = {str(p): detect_chart_type(str(p)) for p in result_paths}
    notes: list[str] = []
    if not chart_type:
        detected = file_types[str(result_paths[0])]
        if detected:
            chart_type = detected
            _logger.info("compare_results chart_type auto-detected=%s (%s)",
                         chart_type, result_paths[0])
        else:
            chart_type = "SP"
            notes.append("无法从 RAW 自动识别 chart_type（缺 Plotname 或未知格式），"
                         "已按旧默认 SP 处理；HB / XDB 数据请显式传 chart_type")
            _logger.warning("compare_results chart_type fallback=SP (%s)", result_paths[0])
    mismatched = [p for p, t in file_types.items() if t and t != chart_type]
    if mismatched:
        notes.append(
            f"以下 RAW 的 Plotname 与 chart_type={chart_type} 不一致："
            + "、".join(f"{Path(p).name}({file_types[p]})" for p in mismatched)
            + "（混用不同数据集类型必然白跑，请只对比同类型 RAW）"
        )
        _logger.warning("compare_results chart_type mismatch: %s", notes[-1])

    if labels is None:
        labels = [Path(raw_path).stem for raw_path in result_paths]
    if len(labels) != file_count:
        return error_response("INVALID_PARAMETERS", "labels 数量与 result_paths 不一致")
    if not all(isinstance(lb, str) for lb in labels):
        return error_response("INVALID_PARAMETERS", "labels 每个元素必须是字符串")

    _logger.info("compare_results files=%d curve=%s type=%s align=%s ref=%d csv=%s",
                 file_count, curve, chart_type, alignment, reference_index,
                 Path(csv_path).name if csv_path else "(none)")

    # 规范化输出路径
    output_path = str(Path(output_path).expanduser().resolve())
    if csv_path:
        csv_path = str(Path(csv_path).expanduser().resolve())
    if not Path(output_path).parent.is_dir():
        return error_response("OUTPUT_DIRECTORY_NOT_FOUND", f"输出目录不存在: {Path(output_path).parent}")
    if csv_path and not Path(csv_path).parent.is_dir():
        return error_response("OUTPUT_DIRECTORY_NOT_FOUND", f"CSV 输出目录不存在: {Path(csv_path).parent}")

    # Step 1: export each RAW to temp CSV (per_tool_mutex via runner)
    raw_curves: list[tuple[list[float], list[float]]] = []
    command_runs: list[dict] = []   # 每个 RAW 一次引擎调用，逐条回传（引擎输出基本为空）

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, rp in enumerate(result_paths):
            tmp_csv = os.path.join(tmpdir, f"tmp_{i}.csv")
            cmd = [
                TURBOCHARTS_PATH,
                "--raw", rp,
                "--csv", tmp_csv,
                "--img", os.path.join(tmpdir, f"tmp_{i}.png"),
                "--type", chart_type,
                "--linename", curve,
            ]
            if dependency:
                cmd.extend(["--dependcy", dependency])

            try:
                result_proc = run_turbocharts(cmd, timeout_seconds=60)
            except RuntimeError as exc:
                return error_response("TOOL_TIMEOUT", str(exc),
                                      command_runs=command_runs + [record_run(cmd, error=str(exc))])
            command_runs.append(record_run(cmd, result_proc))
            if result_proc.returncode != 0:
                # 原因必须带上 returncode 与 stdout 兜底：引擎失败时 stderr 可能为空，
                # 只回「失败: 」等于把真因丢掉（2026-09-28 体检 GW-2）。
                detail = (result_proc.stderr or result_proc.stdout or "").strip()[:200]
                return error_response(
                    "TOOL_EXECUTION_FAILED",
                    f"turbocharts 导出 {rp} 失败 (rc={result_proc.returncode}, "
                    f"chart_type={chart_type}{_own_type_hint(file_types, rp, chart_type)}): "
                    f"{detail or '(引擎未输出任何信息)'}",
                    command_runs=command_runs,
                    **(_notes_detail(notes)),
                )

            x_vals, y_vals = _read_curve_csv_xy(tmp_csv)
            if not x_vals:
                return error_response(
                    "INVALID_RAW_DATA",
                    f"无法从 {rp} 的 CSV 解析出数据（chart_type={chart_type!r}、曲线名 "
                    f"{curve!r} 与 RAW 不匹配时会这样"
                    f"{_own_type_hint(file_types, rp, chart_type)}；"
                    "先用 list_result_curves 取 suggested_curves）",
                    **(_notes_detail(notes)),
                )
            raw_curves.append((x_vals, y_vals))

    # Step 2: align data (preserve original index order)
    curves_aligned: list[list[float] | None] = [None] * file_count
    common_x: list[float] = []

    if alignment == "intersection":
        x_sets = [set(xv) for xv, _ in raw_curves]
        common_x = sorted(set.intersection(*x_sets))
        if not common_x:
            return error_response("INVALID_RAW_DATA", "所有 RAW 文件没有共同的依赖轴数据点")
        for i, (xv, yv) in enumerate(raw_curves):
            # 使用 last-wins 去重：重复 X 值取最后一个（与 zip→dict 行为一致）
            x_to_y: dict[float, float] = {}
            for x, y in zip(xv, yv):
                x_to_y[x] = y
            curves_aligned[i] = [x_to_y[float(p)] for p in common_x]
    else:
        # interpolation — 所有文件 X 轴都需严格递增（np.interp 要求，否则结果未定义）
        for i, (xv, _) in enumerate(raw_curves):
            if not all(xv[j] < xv[j + 1] for j in range(len(xv) - 1)):
                return error_response("INVALID_RAW_DATA",
                                  f"interpolation 模式要求所有文件 X 轴严格递增，但 {labels[i]} 中存在无序或重复的依赖轴值")
        ref_x, ref_y = raw_curves[reference_index]
        common_x = ref_x
        curves_aligned[reference_index] = ref_y
        for i, (xv, yv) in enumerate(raw_curves):
            if i == reference_index:
                continue
            interp_y = np.interp(ref_x, xv, yv)
            curves_aligned[i] = interp_y.tolist()

    aligned = [c for c in curves_aligned if c is not None]
    series = [
        {"label": labels[i], "points": len(aligned[i])}
        for i in range(file_count)
    ]

    # Step 3: compute metrics (each vs reference)
    metrics = []
    ref_curve = aligned[reference_index]
    for i in range(file_count):
        if i == reference_index:
            continue
        diffs = [
            abs(ref_curve[j] - aligned[i][j])
            for j in range(len(common_x))
        ]
        if diffs:
            metrics.append({
                "label": labels[i],
                "reference": labels[reference_index],
                "max_absolute_difference": round(max(diffs), 4),
                "mean_absolute_difference": round(sum(diffs) / len(diffs), 4),
                "rms_difference": round(math.sqrt(sum(d * d for d in diffs) / len(diffs)), 4),
            })

    # Step 4: generate comparison image with Matplotlib
    fig, ax = plt.subplots(figsize=(10, 6))
    for i, yv in enumerate(aligned):
        ax.plot(common_x, yv, label=labels[i], linewidth=1.5)
    ax.set_xlabel(dependency)
    ax.set_ylabel(curve)
    ax.set_title(f"Comparison: {curve}")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    img_ok = True

    # Step 5: write comparison CSV
    csv_ok = False
    if csv_path:
        with open(csv_path, "w", encoding="utf-8") as f:
            headers = [dependency] + [f"{labels[i]}_{curve}" for i in range(file_count)]
            f.write(",".join(headers) + "\n")
            for j, x in enumerate(common_x):
                row = [str(x)] + [str(aligned[i][j]) for i in range(file_count)]
                f.write(",".join(row) + "\n")
        csv_ok = True

    artifacts: list[dict] = []
    if img_ok:
        artifacts.append(build_artifact("image", output_path, "compare_simulation_results"))
    if csv_ok:
        artifacts.append(build_artifact("csv", csv_path, "compare_simulation_results"))

    result = {
        "success": img_ok,
        "image_path": output_path,
        "csv_path": csv_path if csv_ok else "",
        "curve": curve,
        "chart_type": chart_type,
        "alignment": alignment,
        "series": series,
        "metrics": metrics,
        "command_runs": command_runs,
        "engine_ok": (False if any(r.get("ok") is False for r in command_runs)
                      else (True if command_runs and all(r.get("ok") is True for r in command_runs) else None)),
        "engine_report": engine_report(command_runs),
        "artifacts": artifacts,
        "message": "对比图已生成。" if img_ok else "对比图生成失败。",
    }
    if notes:
        result["warning"] = "；".join(notes)
    if img_ok:
        result.update(build_file_link(output_path, "打开对比图"))
    return result


def _notes_detail(notes: list[str]) -> dict[str, Any]:
    """把 chart_type / 类型一致性提示也塞进错误信封的 details（别只留在 message 文本里）。"""
    return {"chart_type_notes": list(notes)} if notes else {}


def _own_type_hint(file_types: dict[str, str], raw_path: str, chart_type: str) -> str:
    """失败信息后缀：该 RAW 自己的 Plotname 与本次 chart_type 不一致时点出来。"""
    own = file_types.get(str(raw_path), "")
    return f", 该 RAW 的 Plotname={own}" if own and own != chart_type else ""


def _read_curve_csv_xy(path: str) -> tuple[list[float], list[float]]:
    """Read turbocharts CSV — returns (x_values, y_values) from first two columns."""
    try:
        with open(path, newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return [], []
    if len(rows) < 2 or len(rows[0]) < 2:
        return [], []
    x_vals: list[float] = []
    y_vals: list[float] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        try:
            x = float(row[0])
            y = float(row[1])
        except ValueError:
            continue
        # 成对追加，避免 x 成功、y 失败导致两个列表长度错位
        x_vals.append(x)
        y_vals.append(y)
    return x_vals, y_vals
