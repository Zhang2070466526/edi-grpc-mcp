"""compare_snp —— 多个 Touchstone（.sNp/.s2p/…）对齐、可重归一化、出对比图与 CSV。

为什么单独做：`compare_simulation_results` 只吃 ADS RAW；HFSS 与 LASPCEM 之间没有对比能力时，
只能手写脚本解析 MA/RI、对频率网格、重归一化参考阻抗、再手工画图（2026-11-26 真机 17 分钟）。

关键事实（同一天实测）：
- HFSS 导出的 s2p 是 `MA`（模/角度），LASPCEM 是 `RI`（实/虚）——必须自动判格式。
- LASPCEM 工程 `PortRenormalization=DoNotRenormalize`，sNp 按端口自身阻抗归一
  （CPW_SL 实测 52.66 / 45.05 Ω），直接与 HFSS 的 50 Ω 比较会得到 4–7 dB 的假「偏差」。
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

import numpy as np

from servers import mcp
from servers.snp_compare import align_freqs, parse_touchstone, read_laspcem_z0, renormalize, s_to_db
from servers.utils import build_file_link, error_response, result_dir


def _default_out() -> Path:
    d = Path(result_dir()) / f"compare_snp_{time.strftime('%Y%m%d_%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _z_display(z) -> float | list[float]:
    """参考阻抗 → 可 JSON 的实数值。

    `read_laspcem_z0` 从 CSV 读出来的是 complex（`52.6613+0.0025j`），直接塞进返回体会让
    MCP 出站 JSON 序列化抛 `Object of type complex is not JSON serializable`（线上 tools/call
    直接失败）。重归一化内部本来就只取实部（虚部 <1e-3 Ω），所以对外也只暴露实部。
    """
    if isinstance(z, (int, float)):
        return float(z)
    if np.isscalar(z):
        return float(np.real(complex(z)))
    return [float(np.real(complex(x))) for x in z]


@mcp.tool()
def compare_snp(
    paths: list[str],
    labels: list[str] | None = None,
    renormalize_to: float = 0.0,
    ref_impedance: list[list[float]] | None = None,
    z0_csv: str | None = None,
    output_dir: str = "",
    title: str = "",
    show_phase: bool = False,
) -> dict[str, Any]:
    """把多个 sNp 文件对齐到同一频率网格，按端口数出全部 S(i,j) 对比曲线与 CSV（可选重归一化）。

    用法："对比 CPW_SL_HFSSDesign1.s2p 和 LASPCEM 导出的 sNp"、"把这两个 snp 归一化到 50Ω 再比"

    自动识别 Touchstone 的 MA/RI/DB 三种格式与频率单位；以第一个文件为基准取公共频段，
    差值统计也以第一个文件为基准。

    Args:
        paths: Touchstone 文件绝对路径列表（≥2 个）。
        labels: 图例名，与 paths 一一对应；缺省用文件名。
        renormalize_to: 目标参考阻抗（Ω）。>0 时先把每个文件从 ref_impedance（缺省用文件头 R 值）
            重归一化到该值；0 = 不改口径（原样对比，残差里会包含口径差）。
        ref_impedance: 各文件的原始参考阻抗，与 paths 一一对应；每项是标量、每端口一个值的列表，
            或 None（该文件按文件头 R 处理，便于只覆盖其中一个）。
            例如 LASPCEM 侧传 [[52.66, 45.05]]（取自 Design_POST/S_result.csv 的 Z0）。
        z0_csv: LASPCEM 的 S_result.csv 路径（可选）。提供后自动读端口阻抗，并把它作为「文件名含
            laspcem」的文件的原始参考阻抗（重归一化用），省得手动从 CSV 抄 Z0。混比 HFSS 时，
            HFSS 文件头已带 50 不受影响；仍可用 ref_impedance 逐文件覆盖。
        output_dir: 输出目录；留空落到 <result_dir>/compare_snp_<时间戳>/。
        title: 图标题，缺省自动生成。
        show_phase: 是否额外输出相位对比图（compare_snp_phase.png），默认 False。
            差异图（compare_snp_diff.png）总是输出：每个 Sij 一条 |ΔS|，基准是第一个文件。

    Returns:
        {"success": true, "png_path": "...", "csv_path": "...", "points": 91,
         "freq_range": [1.0, 10.0],
         "png_link" / "media_tag": 对比图的 Markdown 链接 / 内联显示标签,
         "csv_link": 对比数据 CSV 的 Markdown 链接,
         "diff_png_path" / "diff_png_link": 差值图（|ΔS|，恒出）路径与链接,
         "phase_png_path" / "phase_png_link": 相位图路径与链接（show_phase=true 才有，否则空串）,
         "files": [{"path", "label", "nports", "fmt", "z0", "renormalized_from"}],
         "diff": {"S11": {"<label>": {"mean_db", "max_db"}}, "S21": {...}, ...（按端口数 N 生成全部 N² 个 S(i,j)）},
         "warnings": [...]}
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not paths or len(paths) < 2:
        return error_response("INVALID_PARAMETERS", "至少需要 2 个 sNp 文件才能对比",
                              hint="paths 传 [文件1, 文件2]")
    if labels and len(labels) != len(paths):
        return error_response("INVALID_PARAMETERS", "labels 数量必须与 paths 一致",
                              paths=len(paths), labels=len(labels))
    if ref_impedance and len(ref_impedance) != len(paths):
        return error_response("INVALID_PARAMETERS", "ref_impedance 数量必须与 paths 一致",
                              paths=len(paths), ref_impedance=len(ref_impedance))

    laspcem_z0 = None
    if z0_csv:
        try:
            laspcem_z0 = read_laspcem_z0(z0_csv)
        except (OSError, ValueError) as exc:
            return error_response("INVALID_PATH", f"读不了 LASPCEM Z0 CSV: {exc}", path=str(z0_csv))

    parsed: list[dict] = []
    warnings: list[str] = []
    for i, p in enumerate(paths):
        try:
            d = parse_touchstone(p)
        except (OSError, ValueError) as exc:
            return error_response("INVALID_PATH", f"读不了 {p}: {exc}", path=str(p))
        name = (labels[i] if labels else Path(p).stem)
        s = d["s"]
        renorm_from = None
        if renormalize_to and float(renormalize_to) > 0:
            z_from = d["z0"]
            if ref_impedance and i < len(ref_impedance) and ref_impedance[i] is not None:
                z_from = ref_impedance[i]
            elif laspcem_z0 is not None and "laspcem" in Path(p).stem.lower():
                z_from = laspcem_z0
            elif laspcem_z0 is not None:
                # z0_csv 给了但这个文件没命中 ⇒ 说明按什么处理的，别让用户以为已按端口阻抗归一化
                warnings.append(
                    f"{name} 文件名不含 laspcem，按文件头 R={d['z0']}Ω 处理（未用 z0_csv 的端口阻抗）")
            # 对外只暴露实部：renormalize 内部同口径，且 complex 不能 JSON 序列化
            z_disp = _z_display(z_from)
            if isinstance(z_disp, float) and abs(z_disp - float(renormalize_to)) < 1e-9:
                warnings.append(f"{name} 参考阻抗 {z_disp}Ω 已等于目标 {renormalize_to}Ω，未重归一化")
            else:
                s = renormalize(s, z_from, float(renormalize_to))
                renorm_from = z_disp
        parsed.append({"path": str(p), "label": name, "freqs": d["freqs"], "s": s,
                       "nports": d["nports"], "fmt": d["fmt"], "z0": d["z0"],
                       "renorm_from": renorm_from})

    if min(p["nports"] for p in parsed) < 2:
        return error_response("INVALID_PARAMETERS", "至少一个是单端口文件，无法对比 S21",
                              port_counts=[p["nports"] for p in parsed])

    aligned = align_freqs(parsed[0]["freqs"], [(p["freqs"], p["s"]) for p in parsed])
    if aligned is None:
        return error_response("NO_OVERLAP", "这些文件的频段没有重叠，无法对比",
                              freq_ranges=[[float(p["freqs"].min()), float(p["freqs"].max())]
                                           for p in parsed])
    grid, *mats = aligned
    if grid.size < 2:
        return error_response("NO_OVERLAP", "公共频段点数不足（<2），无法绘图", points=int(grid.size))

    nports = min(p["nports"] for p in parsed)
    if len({p["nports"] for p in parsed}) > 1:
        warnings.append(f"端口数不一致（{[p['nports'] for p in parsed]}），只对比公共的前 {nports} 个端口")

    curves: dict[tuple[int, int], list[np.ndarray]] = {}
    for i in range(nports):
        for j in range(nports):
            curves[(i, j)] = [s_to_db(m, i, j) for m in mats]

    out = Path(output_dir) if output_dir else _default_out()
    out.mkdir(parents=True, exist_ok=True)
    png_path = out / "compare_snp.png"
    csv_path = out / "compare_snp.csv"

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        head = ["freq_GHz"]
        for p in parsed:
            for i in range(nports):
                for j in range(nports):
                    head.append(f"{p['label']}_S{i + 1}{j + 1}_dB")
        w.writerow(head)
        for k in range(grid.size):
            row = [f"{grid[k]:.6f}"]
            for pi in range(len(parsed)):
                for i in range(nports):
                    for j in range(nports):
                        row.append(f"{curves[(i, j)][pi][k]:.4f}")
            w.writerow(row)

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    colors = ["#1f5abe", "#c83c2d", "#2e8b57", "#8b5a2b", "#7a3fb5"]
    fig, axes = plt.subplots(nports, nports, figsize=(3.2 * nports + 1.5, 2.6 * nports + 2),
                             dpi=130, squeeze=False)
    for i in range(nports):
        for j in range(nports):
            ax = axes[i][j]
            for pi, p in enumerate(parsed):
                ax.plot(grid, curves[(i, j)][pi], color=colors[pi % len(colors)],
                        lw=1.4, label=p["label"])
            ax.set_title(f"S{i + 1}{j + 1}", fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=8)
            if i == nports - 1:
                ax.set_xlabel("频率 (GHz)")
    axes[0][0].legend(loc="best", fontsize=8)
    ttl = title or (" vs ".join(p["label"] for p in parsed)
                    + (f"（重归一化到 {renormalize_to}Ω）" if renormalize_to else ""))
    fig.suptitle(ttl, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(png_path)
    plt.close(fig)

    # ── 差异图：每格一条 |ΔS|（基准 = 第一个文件），比只给均值/最大值更能看出差异在哪个频段 ──
    diff_png = out / "compare_snp_diff.png"
    fig_d, ax_d = plt.subplots(nports, nports, figsize=(3.2 * nports + 1.5, 2.6 * nports + 1.5),
                               dpi=130, squeeze=False)
    for i in range(nports):
        for j in range(nports):
            ax = ax_d[i][j]
            base = curves[(i, j)][0]
            for pi in range(1, len(parsed)):
                ax.plot(grid, np.abs(curves[(i, j)][pi] - base),
                        color=colors[pi % len(colors)], lw=1.4,
                        label=f"|Δ| vs {parsed[pi]['label']}")
            ax.set_title(f"|ΔS{i + 1}{j + 1}|", fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.tick_params(labelsize=8)
            if len(parsed) > 1:
                ax.legend(fontsize=8)
            if i == nports - 1:
                ax.set_xlabel("频率 (GHz)")
    fig_d.tight_layout(rect=(0, 0, 1, 0.96))
    fig_d.savefig(diff_png)
    plt.close(fig_d)

    phase_png = None
    if show_phase:
        phase_png = out / "compare_snp_phase.png"
        fig_p, ax_p = plt.subplots(nports, nports, figsize=(3.2 * nports + 1.5, 2.6 * nports + 1.5),
                                   dpi=130, squeeze=False)
        for i in range(nports):
            for j in range(nports):
                ax = ax_p[i][j]
                for pi, p in enumerate(parsed):
                    ax.plot(grid, np.angle(mats[pi][:, i, j], deg=True),
                            color=colors[pi % len(colors)], lw=1.4, label=p["label"])
                ax.set_title(f"∠S{i + 1}{j + 1} (°)", fontsize=10)
                ax.grid(True, alpha=0.3)
                ax.tick_params(labelsize=8)
                if i == nports - 1:
                    ax.set_xlabel("频率 (GHz)")
        ax_p[0][0].legend(fontsize=8)
        fig_p.tight_layout(rect=(0, 0, 1, 0.96))
        fig_p.savefig(phase_png)
        plt.close(fig_p)

    diff: dict[str, dict] = {}
    for i in range(nports):
        for j in range(nports):
            series = curves[(i, j)]
            base = series[0]
            diff[f"S{i + 1}{j + 1}"] = {
                parsed[pi]["label"]: {
                    "mean_db": round(float(np.mean(np.abs(series[pi] - base))), 4),
                    "max_db": round(float(np.max(np.abs(series[pi] - base))), 4),
                }
                for pi in range(1, len(parsed))
            }

    png_link = build_file_link(str(png_path), "对比图")
    csv_link = build_file_link(str(csv_path), "对比数据")
    diff_link = build_file_link(str(diff_png), "差值图")
    phase_link = build_file_link(str(phase_png), "相位图") if phase_png else None

    return {
        "success": True,
        "png_path": png_path.resolve().as_posix(),
        "png_link": png_link["markdown_link"],
        "media_tag": png_link["media_tag"],
        "csv_path": csv_path.resolve().as_posix(),
        "csv_link": csv_link["markdown_link"],
        "diff_png_path": diff_png.resolve().as_posix(),
        "diff_png_link": diff_link["markdown_link"],
        "phase_png_path": phase_png.resolve().as_posix() if phase_png else "",
        "phase_png_link": phase_link["markdown_link"] if phase_link else "",
        "points": int(grid.size),
        "freq_range": [round(float(grid[0]), 6), round(float(grid[-1]), 6)],
        "files": [{"path": Path(p["path"]).resolve().as_posix(), "label": p["label"],
                   "nports": p["nports"], "fmt": p["fmt"], "z0": p["z0"],
                   "renormalized_from": p["renorm_from"]} for p in parsed],
        "baseline": parsed[0]["label"],
        "diff": diff,
        "warnings": warnings,
        "message": f"已对比 {len(parsed)} 个文件（{nports} 端口 × {grid.size} 点），图: {png_path.name}",
    }
