"""仿真结果对比工具 — 2-8 个 RAW 文件同曲线对比叠图。

流程：逐 RAW 调 turbocharts 导出 CSV → 依赖轴对齐（intersection/interpolation）
→ Matplotlib 叠图 → 计算 max/mean/rms 差异 → 可选 CSV 导出
"""

from __future__ import annotations

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
from servers.utils import build_artifact, build_file_link, error_response
from servers.turbocharts.config import run_turbocharts
from servers import mcp


@mcp.tool()
def compare_simulation_results(
    result_paths: list[str],
    curve: str,
    output_path: str,
    chart_type: str = "SP",
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
        chart_type: 图表类型，默认 "SP"。
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

    # Step 1: export each RAW to temp CSV (serialized via runner)
    dep_key = dependency
    raw_curves: list[tuple[list[float], list[float]]] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, rp in enumerate(result_paths):
            tmp_csv = os.path.join(tmpdir, f"tmp_{i}.csv")
            cmd = [
                TURBOCHARTS_PATH,
                "--raw", rp,
                "--csv", tmp_csv,
                "--type", chart_type,
                "--linename", curve,
            ]
            if dependency:
                cmd.extend(["--dependcy", dependency])

            result_proc = run_turbocharts(cmd, timeout_seconds=60)
            if result_proc.returncode != 0:
                return error_response("TOOL_EXECUTION_FAILED",
                                  f"turbocharts 导出 {rp} 失败: {result_proc.stderr[:200]}")

            x_vals, y_vals = _read_curve_csv_xy(tmp_csv)
            if not x_vals:
                return error_response("INVALID_RAW_DATA", f"无法解析 {rp} 的 CSV 数据")
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
    img_ok = Path(output_path).exists()

    # Step 5: write comparison CSV
    csv_ok = False
    if csv_path:
        with open(csv_path, "w", encoding="utf-8") as f:
            headers = [dep_key] + [f"{labels[i]}_{curve}" for i in range(file_count)]
            f.write(",".join(headers) + "\n")
            for j, x in enumerate(common_x):
                row = [str(x)] + [str(aligned[i][j]) for i in range(file_count)]
                f.write(",".join(row) + "\n")
        csv_ok = Path(csv_path).exists()

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
        "alignment": alignment,
        "series": series,
        "metrics": metrics,
        "artifacts": artifacts,
        "message": "对比图已生成。" if img_ok else "对比图生成失败。",
    }
    if img_ok and result.get("success"):
        result.update(build_file_link(output_path, "打开对比图"))
    return result


def _read_curve_csv_xy(path: str) -> tuple[list[float], list[float]]:
    """Read turbocharts CSV — returns (x_values, y_values) from first two columns."""
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return [], []
    if len(lines) < 2:
        return [], []
    headers = lines[0].strip().split(",")
    if len(headers) < 2:
        return [], []
    x_vals: list[float] = []
    y_vals: list[float] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        vals = line.strip().split(",")
        if len(vals) < 2:
            continue
        try:
            x = float(vals[0])
            y = float(vals[1])
        except ValueError:
            continue
        # 成对追加，避免 x 成功、y 失败导致两个列表长度错位
        x_vals.append(x)
        y_vals.append(y)
    return x_vals, y_vals
