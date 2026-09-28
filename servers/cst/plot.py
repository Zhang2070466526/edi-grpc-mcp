"""CST 远场方向图绘图 —— 解析远场 ASCII txt 出极坐标 + 直角 PNG。"""

from __future__ import annotations

import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from servers import mcp
from servers.utils import build_artifact, build_file_link, error_response, per_tool_mutex, validate_file

_logger = logging.getLogger("cst.plot")

_CUT_TOLERANCE_DEG = 1.0


def _parse_cut(cut: str) -> tuple[str, float]:
    """解析切面参数：phi=0 / theta=-90，返回 (axis, angle)。格式非法抛 ValueError。"""
    m = re.match(r"^\s*(phi|theta)\s*=\s*(-?\d+(?:\.\d+)?)\s*$", cut, re.IGNORECASE)
    if not m:
        raise ValueError("cut 格式应为 phi=0 或 theta=0（角度值）")
    return m.group(1).lower(), float(m.group(2))


def _parse_farfield(text: str) -> dict[str, Any]:
    """解析远场 ASCII 文本，返回 {theta_col, phi_col, gain_col, rows}。

    容错解析：按表头关键词定位列（大小写不敏感、容忍列顺序/数量差异），
    数据行按「整行可转浮点」识别。找不到表头/数据抛 ValueError。
    """
    lines = text.splitlines()
    header_idx = -1
    for i, ln in enumerate(lines):
        low = ln.lower()
        if "theta" in low and "phi" in low:
            header_idx = i
            break
    if header_idx < 0:
        raise ValueError("未找到远场表头（需含 Theta / Phi 列）")

    # 多频段守卫：单个 txt 里若还有第二个「Theta+Phi」表头，说明混了多个频点，
    # 本工具一次只画一份（cst_export_farfield 每频点各导一份 txt），直接拒绝并说清。
    for ln in lines[header_idx + 1:]:
        low = ln.lower()
        if "theta" in low and "phi" in low:
            raise ValueError(
                "该 txt 含多个频点表头（多频段混在一个文件里）；"
                "请用 cst_export_farfield 每频点分开导出的单频 txt 逐个画"
            )

    # 列名：CST 表头把单位**塞在列名 token 里**（`Abs(Gain)[dBi   ]`），并且会产生
    # 孤立的 `]` token。直接按空格切会把它数成 14 个「列名」，而数据行只有 8 列，
    # 于是门槛 `len(vals) >= len(col_names)` 恒假、每行都被丢掉 → 报「未解析到数据行」
    # （2026-09-28 实测：本工具在 cst_export_farfield 自己产出的文件上必失败）。
    # 归一化：从第一个 `[` 截断，丢掉空 token / 纯括号残片。
    col_names: list[str] = []
    for tok in lines[header_idx].split():
        name = tok.split("[", 1)[0].strip()
        if not name or not name[0].isalpha():
            continue  # 空 token / 孤立的 `]`、`)[dBi` 之类残片
        col_names.append(name.lower())

    def _col(*keys: str) -> int | None:
        for idx, name in enumerate(col_names):
            if any(k in name for k in keys):
                return idx
        return None

    theta_col = _col("theta")
    phi_col = _col("phi")
    gain_col = _col("gain", "dir", "directivity")
    if gain_col is None:
        gain_col = _col("abs")  # 回退：任何 Abs(...) 列
    if theta_col is None or phi_col is None or gain_col is None:
        raise ValueError("远场表头缺少必要列（Theta/Phi/Gain）：%s" % lines[header_idx])

    rows: list[list[float]] = []
    for ln in lines[header_idx + 1:]:
        parts = ln.split()
        if not parts:
            continue
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            continue  # 非数据行（频率标记/分隔线等）
        if len(vals) >= len(col_names):
            rows.append(vals)
    if not rows:
        raise ValueError("未解析到数据行（表头后无有效数值行）")
    return {"theta_col": theta_col, "phi_col": phi_col, "gain_col": gain_col, "rows": rows}


@mcp.tool()
@per_tool_mutex
def cst_plot_farfield(txt_path: str, cut: str = "phi=0", output_dir: str = "") -> dict[str, Any]:
    """把 CST 远场 ASCII .txt 画成方向图（极坐标切面 + 直角曲线各一张 PNG）。

    用法："把这个远场结果画成方向图"

    解析 cst_export_farfield 导出的远场 txt（列为 Theta / Phi / Abs(Gain)[dBi] /
    Phase / Ax.Ratio），按 cut 指定的切面（phi=0 固定 phi 扫 theta）出两张图。

    注意：cst_export_farfield 会对每个频点各导一份 txt（min/centre/max 多份），
    请显式传入要画的那一份，本工具不「默认取第一份」。

    Args:
        txt_path: 远场 txt 绝对路径（cst_export_farfield 返回的 farfield_txts 之一）。
        cut: 切面，格式 phi=0 或 theta=0（默认 phi=0）。
        output_dir: 图片输出目录（空则与 txt 同目录）。

    Returns:
        {"success": True, "artifacts": [...], "output_paths": {"polar": ..., "rect": ...},
         "gain_peak_dbi": ..., "message": "..."}
    """
    try:
        resolved = validate_file(txt_path)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FILE_NOT_FOUND", str(e))

    try:
        cut_axis, cut_angle = _parse_cut(cut)
    except ValueError as e:
        return error_response("INVALID_PARAMETERS", str(e))

    try:
        parsed = _parse_farfield(Path(resolved).read_text(encoding="utf-8", errors="ignore"))
    except ValueError as e:
        return error_response("CST_PLOT_FAILED", str(e))

    theta_col, phi_col, gain_col = parsed["theta_col"], parsed["phi_col"], parsed["gain_col"]
    if cut_axis == "phi":
        fix_col, sweep_col, sweep_label = phi_col, theta_col, "Theta"
    else:
        fix_col, sweep_col, sweep_label = theta_col, phi_col, "Phi"

    # 找最接近请求的固定角（实数据是网格点，允许 ±1° 容差），避免浮点硬匹配
    fixed_vals = sorted({round(r[fix_col], 3) for r in parsed["rows"]})
    target = min(fixed_vals, key=lambda v: abs(v - cut_angle))
    if abs(target - cut_angle) > _CUT_TOLERANCE_DEG:
        return error_response(
            "INVALID_PARAMETERS",
            "cut=%s 未命中（可用固定角：%s）" % (cut, fixed_vals[:24]),
        )

    pts = sorted(
        (r[sweep_col], r[gain_col]) for r in parsed["rows"] if abs(r[fix_col] - target) < 1e-6
    )
    if not pts:
        return error_response("CST_PLOT_FAILED", "cut=%s 下没有匹配数据" % cut)
    sweep = [p[0] for p in pts]
    gain = [p[1] for p in pts]

    out_dir = output_dir or str(Path(resolved).parent)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stem = Path(resolved).stem
    out_rect = os.path.join(out_dir, stem + "_rect.png")
    out_polar = os.path.join(out_dir, stem + "_polar.png")
    title = "%s @ %s=%.1f°" % (Path(resolved).name, cut_axis, target)

    # 直角曲线：x = 扫角，y = 增益(dBi)
    plt.figure(figsize=(7, 4.5))
    plt.plot(sweep, gain, marker="o", markersize=3, linewidth=1.2)
    plt.xlabel("%s [deg]" % sweep_label)
    plt.ylabel("Gain [dBi]")
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_rect, dpi=120)
    plt.close()

    # 极坐标：r = 线性增益（10^(dBi/10)），theta = 扫角
    linear = [10 ** (g / 10.0) for g in gain]
    plt.figure(figsize=(5.5, 5.5))
    ax = plt.subplot(111, projection="polar")
    ax.plot([math.radians(a) for a in sweep], linear, linewidth=1.2)
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_title(title, va="bottom")
    plt.tight_layout()
    plt.savefig(out_polar, dpi=120)
    plt.close()

    artifacts = [
        build_artifact("image", out_rect, "cst_plot_farfield"),
        build_artifact("image", out_polar, "cst_plot_farfield"),
    ]
    resp: dict[str, Any] = {
        "success": True,
        "cut": "%s=%.1f" % (cut_axis, target),
        "sweep_points": len(pts),
        "gain_peak_dbi": round(max(gain), 3),
        "artifacts": artifacts,
        "output_paths": {"polar": out_polar, "rect": out_rect},
        "message": "方向图已生成（极坐标 + 直角曲线）",
    }
    resp.update(build_file_link(out_rect, "打开直角方向图"))
    return resp
