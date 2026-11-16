r"""Turbocharts MCP 工具 — ADS RAW 文件转曲线图与 CSV。

turbocharts_convert   将 ADS 仿真 RAW 结果转为 PNG 曲线图和 CSV
list_result_curves    解析 RAW 头部、列出可用曲线名（画图前先调用）

命令格式与曲线命名的权威说明见引擎自带文档（同目录，本模块按它实现）：
    servers/turbocharts/RAW 转图像工具使用说明.txt
        RawConverter --raw <raw> --img <image> --type <SP|HB|XDB> [--csv <csv>] [--linename <line>]
        --linename 为「单位_线段名」：常用单位 db、phase、real(实数)，另 vswr、aps(附加移相)、
                   af(幅度波动)；时延的线段名为 delays[i,j]（写作 real_delayS[2,1]）；多条曲线用 & 分隔
        --dependcy（依赖）与 --ac（精度计算，5 组 # 分隔）的定义与示例见该说明文件

实测补充（说明未列出/未覆盖的部分；工具里的校验按这些结论执行）：
    · 说明的单位清单是"常用"：dBm_（绝对功率）与 imag_（虚部）在 SP 数据上同样有效，
      dBm_S[2,1] → dBm(S[21])，与说明里 db_ 画出的 dB(S[21]) 是两张不同的图；
      单位前缀大小写不敏感（说明示例的小写 db_s[1,1] 与 DB_S[1,1] 等价）
    · "_" 后的线段名必须与 RAW 的 Variables 段完全一致、大小写敏感（dBm_out1 出空图）
    · 多条曲线的分隔符是 &（说明示例即 &）；逗号/分号会静默画空图且 rc=0 → 本工具直接拒绝
    · type=HB 时引擎只认 db 类与 real_，phase_/imag_/vswr_ 被静默忽略并回退成 dBm 图
    · 说明的「驻波只支持单条曲线」与 HB 多曲线 CSV 只落最后一条 → 本工具自动逐条拆分
    · 非法曲线（拼错/不存在/裸线段名）：SP 下引擎段错误、HB 下静默出空图，退出码不可作判据
      → 本工具调用前校验，返回 INVALID_LINENAME / CURVE_NOT_FOUND，不再静默出错图
    · 引擎的进度/诊断输出**走 stderr(fd 2)**（2026-09-30 11:19 起的构建，用户改 C++ 后重打包）：
      `main.cpp` 里 `qInstallMessageHandler` + `fwrite(..., stderr)` ⇒ `subprocess` 管道能收到 ——
      实测 `绘制线名： "…"`、`… 导出成功`、`HB CSV导出失败：x_label_为空，当前没有有效曲线数据`
      都会进 `command_runs[].stderr`。⚠️ **11:19 之前的构建**把这几行写控制台设备（CONOUT$）⇒ 管道恒
      0 字节（仅 Qt 参数层报错走 CRT stderr 可捕获）。判成败仍看产物文件：曲线不存在/无数据、RAW 不存在、
      `--type` 非法一律 rc=0 + 照写空图。每次子进程调用（图片 1 次 + CSV 拆分 N 次）逐条记进返回的
      command_runs，便于事后对照
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from servers.eda.config import TURBOCHARTS_PATH
from servers.utils import (build_artifact, build_file_link, default_result_path,
                           error_response, per_tool_mutex, validate_file)
from servers.turbocharts.config import (append_engine_log, engine_failures, engine_log_path,
                                        engine_report, record_run, run_turbocharts)
from servers import mcp

_logger = logging.getLogger("turbocharts.convert")

_IMG_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".svg")

# ── 曲线单位前缀（顺序敏感：dBm 先于 DB、real_delay 先于 real）──
# 每条 = (匹配正则, 前缀标识, 说明)。单位名称与量纲对齐引擎自带说明
# （servers/turbocharts/RAW 转图像工具使用说明.txt），凡说明未列的都在说明里标注。
_PREFIX_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r'^dBm_(?P<var>.+)$', re.IGNORECASE), "dBm",
     "dBm(变量)：绝对功率（说明单位清单未列，示例已用；文件落盘形态 freq,dBm(变量)）"),
    (re.compile(r'^dB_(?P<var>.+)$', re.IGNORECASE), "DB",
     "db(变量)：说明中的常用单位 db，落盘为 dB(变量)（SP 下与 dBm_ 是两张不同的图）"),
    (re.compile(r'^real_delay(?P<var>.+)$', re.IGNORECASE), "real_delay",
     "real_delayS[i,j]：时延（说明：时延的线段名称为 delays[2,1]）"),
    (re.compile(r'^real_(?P<var>.+)$', re.IGNORECASE), "real",
     "real(变量)：说明中的 real(实数)，即线性幅度"),
    (re.compile(r'^phase_(?P<var>.+)$', re.IGNORECASE), "phase",
     "phase(变量)：说明中的常用单位 phase（HB 下引擎静默回退成 dBm 图）"),
    (re.compile(r'^imag_(?P<var>.+)$', re.IGNORECASE), "imag",
     "imag(变量)：虚部（说明未列，实测有效；HB 下同样静默回退成 dBm 图）"),
    (re.compile(r'^mag_(?P<var>.+)$', re.IGNORECASE), "mag",
     "mag(变量)：幅度（引擎 --curve-info 返回的可用单位；SP 下出独立幅度图，HB 下回退 dBm）"),
    (re.compile(r'^VSWR_(?P<var>.+)$', re.IGNORECASE), "VSWR",
     "VSWR_S[i,j]：说明中的 vswr(驻波)，注明只支持单条曲线"),
    (re.compile(r'^(?:APS|AF|MAS|MV|PSS)_(?P<var>.+)$', re.IGNORECASE), "数控量",
     "APS_/AF_/MAS_/MV_/PSS_：数控衰减器/移相器专有量（AF 为幅度波动；"
     "无对应器件时只有表头、无数据）"),
)

# HB 数据类型下引擎真正认的前缀；其它前缀会被静默忽略、退化成 dBm 图
_HB_USEFUL_PREFIXES = frozenset({"dBm", "DB", "real"})

# 「不传 linename 时自动组图」的默认单位优先级（按 chart 类型）。
# HB 只挑 dBm：实测（2026-11-16）HB 下 db/phase/imag/mag/VSWR/APS/MAS/MV/PSS 全部被引擎
# 静默回退成 dBm 图，只有 dBm_ 与 real_ 是真图；自动组图当然选那个真正有意义的绝对量。
_DEFAULT_UNIT_BY_CHART = {
    "HB": ["dBm", "real"],
    "SP": ["db", "dBm", "real"],
    "XDB": ["db", "dBm", "real"],
}

_VSWR_RE = re.compile(r'^VSWR_S\[\d+,\d+\]$', re.IGNORECASE)


def _match_prefix(segment: str) -> tuple[str | None, str, str]:
    """把单条曲线名拆成 (前缀标识, 变量名, 说明)；前缀非法时返回 (None, segment, "")。"""
    for pattern, prefix, label in _PREFIX_RULES:
        m = pattern.match(segment)
        if m:
            return prefix, m.group("var"), label
    return None, segment, ""


def _candidate_vars(prefix: str, var: str) -> list[str]:
    """列出该曲线在 RAW 里可能的变量名写法（括号风格 / 群时延命名差异）。"""
    candidates = [var]
    if prefix == "real_delay" and var[:1].upper() == "S" and len(var) > 1:
        # 引擎写 real_delayS[2,1]，RAW 里的变量名是 S.delay[2,1]
        candidates.append("S.delay" + var[1:])
    for c in list(candidates):
        candidates.append(c.replace("[", "(").replace("]", ")"))
        candidates.append(c.replace("(", "[").replace(")", "]"))
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _split_curves(linename: str) -> list[str]:
    """按 & 拆分曲线名（引擎的多曲线分隔符；逗号/分号非法）。"""
    return [c.strip() for c in linename.split("&") if c.strip()]


def _validate_linename(
    linename: str,
    chart_type: str,
    known_vars: list[str],
    raw_name: str = "RAW",
) -> tuple[list[str], list[str], list[str], list[str]]:
    """校验 linename 语法与变量存在性。

    Returns: (curves, syntax_errors, missing_errors, warnings)
    """
    curves: list[str] = []
    syntax_errors: list[str] = []
    missing_errors: list[str] = []
    warnings: list[str] = []

    text = (linename or "").strip()
    if not text:
        if chart_type.strip().upper() == "HB":
            warnings.append(
                "HB 数据下不传 linename：引擎只画一张空图且不产 CSV；"
                "需要数据请指定曲线（如 dBm_Out1）。"
            )
        return curves, syntax_errors, missing_errors, warnings

    # 括号内的逗号属于变量名本身（S[2,1]），只检查括号外的分隔符
    outside = re.sub(r'\[[^\]]*\]|\([^)]*\)', '', text)
    if re.search(r'[,;，；]', outside):
        syntax_errors.append(
            "linename 含逗号/分号：多条曲线必须用 & 分隔"
            "（实测引擎遇到逗号会静默画空图，且不返回错误码）。"
        )
        return curves, syntax_errors, missing_errors, warnings

    if re.search(r'\s', outside):
        syntax_errors.append(
            "linename 含空白字符：请去掉空格，例如 dBm_S[2,1]&dBm_S[1,1]。"
        )
        return curves, syntax_errors, missing_errors, warnings

    known = set(known_vars)
    known_lower = {v.lower(): v for v in known_vars}
    chart = chart_type.strip().upper()

    for segment in _split_curves(text):
        prefix, var, _label = _match_prefix(segment)
        if prefix is None:
            allowed = "、".join(
                sorted({label.split("：")[0] for _p, _k, label in _PREFIX_RULES})
            )
            syntax_errors.append(
                f"曲线名 {segment!r} 缺少合法单位前缀：必须是 <前缀>_<变量名>，"
                f"常见前缀 {allowed}。裸变量名（如 {segment!r} 去掉前缀）会让引擎出空图或段错误。"
            )
            continue

        if chart == "HB" and prefix not in _HB_USEFUL_PREFIXES:
            warnings.append(
                f"{segment!r}：HB 数据下引擎会忽略该前缀并画成 dBm 图"
                f"（等效 dBm_{var}），需要相位/虚部请确认仿真类型是 SP。"
            )
        if prefix == "数控量":
            warnings.append(
                f"{segment!r}：APS_/MAS_/MV_/PSS_ 等数控量在缺少对应数控器件数据的 RAW 上"
                "只有表头、无数据，导出后请核对 CSV 行数。"
            )

        if known:
            candidates = _candidate_vars(prefix, var)
            if not any(c in known for c in candidates):
                hit = next((known_lower[c.lower()] for c in candidates
                            if c.lower() in known_lower), None)
                if hit:
                    warnings.append(
                        f"{segment!r}：变量名大小写与 RAW 不一致（RAW 中为 {hit!r}）。"
                        "引擎对 HB 节点变量按精确大小写匹配（实测 dBm_out1 出空图、"
                        "dBm_Out1 正常），SP 的 S[i,j] 括号变量较宽松；"
                        "若图片为空请改用 RAW 中的精确写法。"
                    )
                else:
                    preview = "、".join(sorted(known)[:12]) or "（未解析到变量）"
                    missing_errors.append(
                        f"曲线名 {segment!r} 的变量 {var!r} 不在 {raw_name} 的变量列表中；"
                        f"该 RAW 实测变量：{preview}"
                    )
                    continue

        curves.append(segment)

    return curves, syntax_errors, missing_errors, warnings


def detect_chart_type(raw_path: str) -> str:
    """从 RAW 文件头识别第一个 plot 的数据集类型（SP / HB / XDB）。

    给 compare_simulation_results 之类的工具做缺省推断：chart_type 必须与 RAW 的
    Plotname 一致，猜错时 turbocharts 返回码仍为 0 却不出数据（白跑且报错信息为空）。
    识别不出（非 MDS/XML、无 Plotname）返回空串，调用方应要求显式指定。
    只需读 Plotname 行取第一 token，不再走完整的手写头解析。
    """
    try:
        with open(raw_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read(65536)
    except OSError:
        return ""
    for m in re.finditer(r"Plotname:\s*(\S+)", text, re.IGNORECASE):
        token = m.group(1).upper()
        if token in ("SP", "HB", "XDB"):
            return token
    return ""


def _engine_curve_info(raw_path: str) -> dict[str, Any] | None:
    """调用引擎 `--curve-info` 原生列出「依赖 → 曲线名」。

    返回引擎 JSON（`{依赖名: [{曲线名: [单位...]}, ...]}`）；引擎不可用 / 超时 / 输出非
    JSON 时返回 None，由调用方决定如何提示。引擎是单一事实源：手写解析有 64KB 截断、
    HB 多 sweep 读不全的盲区（见 list_result_curves 的 docstring）。
    """
    try:
        proc = run_turbocharts([TURBOCHARTS_PATH, "--raw", raw_path, "--curve-info"],
                               timeout_seconds=60)
    except (RuntimeError, OSError):
        return None
    for text in (proc.stdout, proc.stderr):
        if not text or not text.strip():
            continue
        t = text.strip()
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


@mcp.tool()
def list_result_curves(result_path: str) -> dict[str, Any]:
    """调用引擎原生 `--curve-info` 列出 RAW 中可用的「依赖 → 曲线名 + 可用单位」。

    引擎是唯一事实源（不再手写解析 RAW 头部 —— 手写版有 64KB 截断、HB 多 sweep 读不全的
    盲区）。画图前调用，避免猜曲线名。

    返回结构：`{依赖名: [{曲线名: [可用单位...]}, ...]}`，单位列表由引擎直接给出。
    画图时拼成 `<单位>_<曲线名>`（如 `db_s[1,1]`，单位大小写不敏感），依赖名原样传给
    `--dependcy`。曲线名/依赖名大小写须与 curves 的键一致，多条曲线用 & 连接（逗号非法）。

    Args:
        result_path: RAW 结果文件路径（必须已存在）。
    """
    try:
        resolved = validate_file(result_path)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FILE_NOT_FOUND", str(e))

    engine_curves = _engine_curve_info(resolved)
    if engine_curves is None:
        return error_response(
            "CURVE_INFO_FAILED",
            "引擎 --curve-info 未能列出曲线（引擎不可用 / 超时 / 输出非 JSON）",
            result_path=resolved,
        )
    chart = detect_chart_type(resolved)
    notes: list[str] = []
    if chart == "HB":
        notes.append(
            "HB 实测（2026-11-16，逐个比对产物 CSV 表头+数据行）：--curve-info 会列出 "
            "db/dBm/phase/real/imag/mag 等，但绘图时只有 dBm_ 与 real_ 出真图；"
            "db/phase/imag/mag 以及 VSWR/APS/MAS/MV/PSS 全部被引擎静默回退成 dBm 图"
            "（表头仍为 dBm(x)、数据与 dBm 逐行相同）。画 HB 建议只用 dBm 或 real。"
        )
    elif chart == "SP":
        notes.append(
            "SP 实测（2026-11-16）：db/dBm/phase/real/imag/mag/VSWR 各出不同图；"
            "APS/MAS/MV/PSS 需要 RAW 里含对应数控器件数据，否则图能出但 CSV 只有表头。"
        )
    notes.append(
        "turbocharts_convert 会在传入 csv_path 时自动核对产物表头，"
        "若单位被引擎回退会写进返回的 warnings —— 以那里为准。"
    )
    return {"success": True, "result_path": resolved,
            "chart_type": chart, "curves": engine_curves, "unit_notes": notes}


def _build_cmd(raw_path, output_path, chart_type, *, linename="", dependency="", csv_path="", ac_config=""):
    """构造 turbocharts 命令行参数列表（基础参数 + 可选参数）。"""
    cmd = [TURBOCHARTS_PATH, "--raw", raw_path, "--img", output_path, "--type", chart_type]
    if linename:
        cmd.extend(["--linename", linename])
    if csv_path:
        cmd.extend(["--csv", csv_path])
    if dependency:
        # 注意：--dependcy 是上游 exe 的拼写错误（不是 dependency），这里透传
        cmd.extend(["--dependcy", dependency])
    if ac_config:
        cmd.extend(["--ac", ac_config])
    return cmd


def _ensure_parent(path: str) -> str | None:
    """创建输出文件的父目录。返回错误信息（None 表示成功）。

    turbocharts_app.exe 不会自建目录：目录不存在时进程仍返回 0 且 stdout 为空，
    但不产出任何文件，只在工具层表现为"图表生成失败，请检查 RAW 文件和参数"。
    """
    try:
        parent = Path(path).expanduser().parent
        parent.mkdir(parents=True, exist_ok=True)
        return None
    except OSError as e:
        return f"无法创建输出目录 {Path(path).parent}: {e}"


def _safe_suffix(name: str) -> str:
    """把曲线名转成安全文件名后缀（VSWR_S[1,1] → VSWR_S1_1，保持既有命名习惯）。"""
    return (name.replace("[", "").replace("]", "")
                .replace(",", "_").replace("&", "_").replace(".", "_").replace(" ", ""))


def _file_fingerprint(path: str) -> tuple[int, int] | None:
    """文件指纹 (size, mtime_ns)：用于确认图片没在后续 CSV 调用中被改写。"""
    try:
        st = Path(path).stat()
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None


def _read_csv_labels(path: str) -> tuple[list[str], int]:
    """读取 CSV 表头与数据行数：表头由引擎写成「单位(变量)」，是判断画了什么的最硬证据。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = [ln for ln in f.read(200000).splitlines()]
    except OSError:
        return [], 0
    if not lines:
        return [], 0
    header = lines[0].lstrip("\ufeff").strip()
    labels = [c.strip() for c in header.split(",") if c.strip()]
    data_rows = sum(1 for ln in lines[1:] if ln.strip())
    return labels, data_rows


def _default_unit(units: list[str], chart_type: str) -> str | None:
    """从引擎给出的可用单位里挑一个默认值（用于"不传 linename 时自动组图"）。

    优先级按 chart 类型定：HB 只用 dBm（实测只有 dBm_/real_ 是真图，其余会静默回退成
    dBm）；SP/XDB 用 db（S 参数的工程习惯量，且实测与 dBm 出的是不同图）。
    引擎给的单位大小写不固定（`db`/`dB`），按小写匹配后再返回引擎原始写法。
    """
    lower = {u.lower(): u for u in units}
    for want in _DEFAULT_UNIT_BY_CHART.get((chart_type or "").upper(), ["db", "dBm", "real"]):
        if want.lower() in lower:
            return lower[want.lower()]
    return units[0] if units else None


def _label_unit(label: str) -> str:
    """从 CSV 列名反推引擎实际用的单位前缀。

    引擎的表头写法：`dBm(Out1)` / `dB(S11)` / `phase(S11)` / `VSWR(1)` / `mag(S11)`，
    以及裸变量名 `S11` / `Out1`（等价于 real）。据此判断"请求的单位是否真的生效"。
    """
    text = label.strip()
    if "(" in text:
        return text.split("(", 1)[0].strip()
    return "real"


@mcp.tool()
@per_tool_mutex
def turbocharts_convert(
    raw_path: str,
    output_path: str = "",
    chart_type: str = "",
    csv_path: str = "",
    linename: str = "",
    dependency: str = "",
    ac_config: str = "",
) -> dict[str, Any]:
    """ADS RAW 转曲线图+CSV（SP/HB/XDB）；linename 为「单位_线段名」，多条用 & 分隔。返回含引擎原始输出（stderr/command_runs/engine_report）与 log_path（原文已落盘）；**回复时必须把引擎原文与 warnings 原样转述给用户，不得只回一句 message**。

    用法："把 result.raw 转成增益曲线"、"生成驻波图同时导出 CSV"

    画图前建议先读资源 edi://reference/turbocharts-guide
    （引擎自带《RAW 转图像工具使用说明》原文，本工具入参即其命令行参数）

    命令格式与单位以引擎自带说明为准（同目录 RAW 转图像工具使用说明.txt）：
        RawConverter --raw <raw> --img <image> --type <SP|HB|XDB> [--csv <csv>] [--linename <line>]
        --linename 常用单位：db、phase、real(实数)，另 vswr、aps(附加移相)、af(幅度波动)；
                   时延的线段名为 delays[i,j]（real_delayS[2,1]）
        说明里的典型曲线：DB_S[2,1]（输出增益）、DB_S[1,2]（反向增益）、VSWR_S[1,1]（驻波，
        说明注明只支持单条曲线）、APS_S[2,1]、MAS_S[2,1]/MV_S[2,1]/PSS_S[2,1]（数控器件）
        --dependcy（依赖）、--ac（精度计算，5 组 # 分隔）的定义与示例见该说明文件。
        导出后请核对返回值里的 curve_labels / warnings（rc=0 不代表画对了）。

    实测补充（说明未覆盖，工具按此校验）:
        · dBm_（绝对功率 → dBm(变量)）与 imag_（虚部）有效（说明单位清单未列，示例已用）；单位前缀大小写不敏感
        · "_" 后的线段名大小写敏感且必须存在于 RAW（dBm_out1 出空图、dBm_Out1 正常）
        · 必须「单位_线段名」两段式；逗号/分号/空格、裸线段名会被直接拒绝（说明用 & 分隔）
        · type=HB 时引擎只认 db 类与 real_，phase_/imag_/vswr_ 静默回退成 dBm 图
        · 多条 VSWR（说明：只支持单条）与 HB 多曲线需导 CSV 时，本工具自动逐条拆分

    Args:
        raw_path: 输入的 ADS RAW 文件路径（必填）。
        output_path: 输出的图像文件路径，支持 PNG/JPG/BMP/SVG；留空则落到默认产物目录
                    （<result_dir>/turbocharts_convert_<时间戳>.png）。父目录会自动创建。
        chart_type: 转换类型，如 "SP"、"HB"、"XDB"；留空默认 "SP"（建议传，需与 RAW 的 Plotname 一致）。
        csv_path: 可选，同时导出的 CSV 文件路径（多曲线拆分时在其文件名后加曲线后缀）。
        linename: 可选，绘制线段名，格式 单位_线段名（如说明里的 "db_s[1,1]"、"real_delayS[1,1]"），
                  多条用 & 分隔。
        dependency: 可选，依赖轴名称，通常为 "freq"（说明中的 --dependcy）。
        ac_config: 可选，精度计算，格式 act_type#bit#data#nv_type#nv_value（说明中的 --ac）。

    Returns:
        {"success": True, "img_generated": True, "csv_generated": True,
         "curves": ["dBm_Out1"], "curve_labels": {"C:/.../out.csv": ["freq", "dBm(Out1)"]},
         "artifacts": [{"type": "image", "path": "...", "generated_by": "turbocharts_convert"}, ...],
         "warnings": ["CSV 已生成（2 个文件），请核对..."],
         "output_paths": {"img": "...", "csv": "..."}}

        command_runs：本次实际执行的每一次引擎调用（图片 1 次 + CSV 拆分 N 次，顺序即执行顺序），
        每条含 command / return_code / stdout / stderr（超时那条为 return_code=None + error）。
        engine_ok / engine_report：**引擎自报的执行成败**（True / False / None）与关键输出行（原措辞，去重）。
        ⚠️ 引擎 rc 恒为 0（曲线不存在/无数据也 rc=0 并写空图）⇒ **判成败看 `engine_ok` + `img_generated` /
        `csv_generated` / `output_paths`，不要看 return_code**；engine_ok=None 表示这次没有可判信号。

        log_path / log_lines：引擎原文**追加落盘**到「与 RAW 同目录的 <raw 文件名>.turbocharts.log」，
        log_lines = 本次新增行数（历史调用累积在同一文件，可随时全文核对）。

        返回里始终带引擎原始输出（stderr / stdout / command_runs）与关键摘录（engine_report）。
        调用后应把 engine_report 与 warnings **原样转述给用户、不要省略**，不要只回一句 message。
    """
    try:
        raw_file = validate_file(raw_path)
        validate_file(TURBOCHARTS_PATH)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FILE_NOT_FOUND", str(e))

    chart_type = (chart_type or "").strip() or "SP"
    if not (output_path or "").strip():
        output_path = default_result_path("turbocharts_convert", ".png")

    _logger.info("turbocharts_convert raw=%s img=%s type=%s linename=%s csv=%s dep=%s ac=%s",
                 Path(raw_file).name, Path(output_path).name, chart_type,
                 linename or "(all)", Path(csv_path).name if csv_path else "(none)",
                 dependency or "(none)", ac_config or "(none)")

    # 校验输出图片扩展名
    img_ext = Path(output_path).suffix.lower()
    if img_ext not in _IMG_EXTENSIONS:
        return error_response(
            "INVALID_PATH",
            f"output_path 扩展名不支持: {img_ext}，请使用 PNG/JPG/BMP/SVG",
        )

    warnings: list[str] = []
    artifacts: list[dict] = []
    command_runs: list[dict] = []

    # ── 第 0 步：目录准备（引擎不会自建目录，失败时它仍返回 0）──
    for candidate in (output_path, csv_path):
        if candidate:
            err = _ensure_parent(candidate)
            if err:
                return error_response("INVALID_PATH", err)

    # ── 第 0.1 步：linename 校验（语法 + 变量存在性）──
    # 变量存在性以引擎 --curve-info 为准（与 list_result_curves 同一事实源），
    # 不再手写解析 RAW（手写解析有 HB 多 sweep 读不全 / 64KB 截断的盲区）。
    engine_curves = _engine_curve_info(raw_file)
    # 引擎返回 {"依赖": [{"曲线名": ["单位"]}]}（2026-11-16 实测，JSON 走 stderr）；
    # 提取每个 dict 的 key（曲线名）做存在性校验，否则拼错的曲线名直接送引擎，
    # SP 段错误 / HB 出空图且 rc=0。
    known_vars = sorted({
        name
        for entries in (engine_curves or {}).values()
        for entry in entries
        for name in entry
    })

    # ── 不传 linename ⇒ 按引擎 --curve-info 的清单自动组图 ──
    # 不能交给引擎自己挑：实测（2026-11-16）SP 不传 linename 会画成
    # dB(S11)/dB(S12)/dB(S21)/dB(S22) 四条，单位由引擎定、返回里无从对账；
    # HB 更是只画一张空图且不产 CSV。所以在这里用同一事实源（--curve-info）决定画什么。
    # 依赖也一并定下（引擎只认一个 --dependcy，取清单里的第一个键）。
    if not (linename or "").strip() and engine_curves:
        first_dep = next(iter(engine_curves))
        picked: list[str] = []
        seen: set[str] = set()
        for entry in engine_curves[first_dep]:
            for name, units in entry.items():
                unit = _default_unit(units, chart_type)
                if not unit:
                    continue
                curve = f"{unit}_{name}"
                if curve not in seen:
                    seen.add(curve)
                    picked.append(curve)
        if picked:
            linename = "&".join(picked)
            if not (dependency or "").strip():
                dependency = first_dep
            warnings.append(
                f"未传 linename：已按引擎 --curve-info 自动组图 —— "
                f"依赖 {first_dep!r}、曲线 {linename}"
                f"（默认单位按 chart 类型选取：HB→dBm、SP→db）。要换单位请显式传 linename。"
            )

    curves, syntax_errors, missing_errors, name_warnings = _validate_linename(
        linename, chart_type, known_vars, raw_name=Path(raw_file).name)
    warnings.extend(name_warnings)
    if syntax_errors:
        return error_response("INVALID_LINENAME", " ".join(syntax_errors),
                              raw_path=raw_file, chart_type=chart_type,
                              linename=linename)
    if missing_errors:
        return error_response("CURVE_NOT_FOUND", " ".join(missing_errors),
                              raw_path=raw_file, chart_type=chart_type,
                              linename=linename, known_variables=sorted(known_vars))

    # 自带说明（RAW 转图像工具使用说明.txt）："驻波(只支持单条曲线)"
    if len([c for c in curves if _VSWR_RE.match(c)]) > 1 and not csv_path:
        warnings.append(
            "多条 VSWR：引擎对驻波只支持单条曲线（见本目录 RAW 转图像工具使用说明.txt），"
            "同图多条可能只画出其中一条，建议逐条调用。"
        )

    # ── 第 1 步：生成 PNG 图片（所有曲线一次性合并）──
    cmd_img = _build_cmd(raw_file, output_path, chart_type, linename=linename,
                         dependency=dependency, ac_config=ac_config)

    try:
        result = run_turbocharts(cmd_img, timeout_seconds=120)
    except RuntimeError as exc:
        return error_response("TOOL_TIMEOUT", str(exc),
                              command_runs=[record_run(cmd_img, error=str(exc))])
    command_runs.append(record_run(cmd_img, result))
    img_generated = Path(output_path).exists()
    img_fingerprint = _file_fingerprint(output_path) if img_generated else None
    if img_generated:
        artifacts.append(build_artifact("image", output_path, "turbocharts_convert"))
    else:
        warnings.append(
            "引擎未生成图片（即使返回码为 0）：请核对 chart_type 是否与 RAW 的 Plotname 一致、"
            "linename 变量是否存在于该 RAW、输出路径是否可写。"
        )

    # ── 第 2 步：CSV 导出 ──
    # 引擎限制（2026-09-11 实测 + 说明原文）：HB 多曲线只导出最后一条、VSWR 只支持单条
    # → 这两种情况按曲线拆成多次调用；SP 的多曲线 CSV 一次即可导出多列。
    csv_plan: list[tuple[list[str], str]] = []
    csv_labels: dict[str, list[str]] = {}
    if csv_path:
        # 重复曲线会拆出同名 CSV 互相覆盖，先按唯一曲线去重
        uniq = list(dict.fromkeys(curves))
        if len(uniq) != len(curves):
            warnings.append(
                f"linename 含重复曲线：{len(curves)} 条中只有 {len(uniq)} 条不同，"
                "CSV 按唯一曲线导出（若本意是画两个结果，请检查是否写成了同一条）。"
            )

        vswr = [c for c in uniq if _VSWR_RE.match(c)]
        rest = [c for c in uniq if not _VSWR_RE.match(c)]
        if chart_type.upper() == "HB" and len(uniq) > 1:
            groups = [[c] for c in uniq]
            warnings.append(
                f"chart_type=HB：引擎的多曲线 CSV 只导出最后一条，已自动按曲线拆分为 "
                f"{len(groups)} 次导出（文件名追加曲线后缀）。"
            )
        elif vswr:  # 说明：驻波只支持单条曲线 → 每条 VSWR 单独导出，其余曲线合并一次
            groups = [[v] for v in vswr] + ([rest] if rest else [])
            warnings.append(
                f"VSWR 曲线（说明中只支持单条）已单独导出 {len(vswr)} 次：{', '.join(vswr)}"
            )
        else:
            groups = [uniq]

        if len(groups) <= 1:
            csv_plan = [(uniq, csv_path)]
        else:
            base, seen = Path(csv_path), set()
            for group in groups:
                stem, n = f"{base.stem}_{_safe_suffix('&'.join(group))}", 2
                while f"{stem}{base.suffix}" in seen:  # 兜底：后缀撞名
                    stem, n = f"{base.stem}_{_safe_suffix('&'.join(group))}_{n}", n + 1
                seen.add(f"{stem}{base.suffix}")
                csv_plan.append((group, str(base.parent / f"{stem}{base.suffix}")))

    for group, target_csv in csv_plan:
        err = _ensure_parent(target_csv)
        if err:
            warnings.append(err)
            continue
        # ⚠️ CSV 模式下引擎仍会按 --img 写图片：这里必须给临时图片路径，
        # 否则每次拆分导出都会用"单条曲线"覆盖掉已画好的多曲线图
        # （2026-09-11 实测：HB 两条曲线经拆分成 2 次后，用户看到只剩最后一条）。
        tmp_img = Path(target_csv).with_name(f"__csv_tmp_{Path(target_csv).stem}.png")
        cmd_csv = _build_cmd(raw_file, str(tmp_img), chart_type,
                             linename="&".join(group), dependency=dependency,
                             csv_path=target_csv, ac_config=ac_config)
        try:
            proc = run_turbocharts(cmd_csv, timeout_seconds=120)
        except RuntimeError as exc:
            command_runs.append(record_run(cmd_csv, error=str(exc)))
            warnings.append(f"turbocharts 导出超时: {exc}")
            continue
        finally:
            tmp_img.unlink(missing_ok=True)
        command_runs.append(record_run(cmd_csv, proc))
        if proc.returncode == 0 and Path(target_csv).exists():
            artifacts.append(build_artifact("csv", target_csv, "turbocharts_convert"))
            labels, data_rows = _read_csv_labels(target_csv)
            if labels:
                csv_labels[target_csv] = labels
            expected = len(group)
            if expected > 1 and len(labels) > 1 and len(labels) - 1 < expected:
                warnings.append(
                    f"{Path(target_csv).name}：CSV 只含 {len(labels) - 1} 条曲线（请求 {expected} 条），"
                    f"引擎丢数据，请逐条调用 turbocharts_convert。"
                )
            if labels and data_rows == 0:
                warnings.append(
                    f"{Path(target_csv).name}：只有表头、0 行数据（{labels[1:] or ['未知量']}）——"
                    "该曲线在此 RAW 中无数据，图片大概率是空的。"
                )
            # ── 单位回退检测（唯一能自动发现"画错图"的地方）──
            # 引擎对不支持的「单位×chart」组合会**静默**退回别的量：rc=0、success=true、
            # 图有内容、CSV 有数据，但表头已变成别的单位。实测（2026-11-16）：
            #   type=HB 请求 db/phase/imag/mag/VSWR/APS/MAS/MV/PSS → 产物表头全是 dBm(Out1)；
            #   只有 dBm_ 与 real_ 出真图。SP 无此问题（11 种里 10 种各出不同表头）。
            # 所以这里不信调用方、也不信 --curve-info，只信**产物表头**。
            requested_units = {c.split("_", 1)[0].lower() for c in group if "_" in c}
            actual_units = {_label_unit(x).lower() for x in labels[1:]}
            missing_units = requested_units - actual_units
            if missing_units and actual_units:
                warnings.append(
                    f"{Path(target_csv).name}：请求的单位 {sorted(missing_units)} 未出现在产物表头 "
                    f"{labels[1:]} 中 —— 引擎在 type={chart_type} 下把该单位静默回退了，"
                    f"图上画的是回退后的量（引擎 --curve-info 声明的支持列表与此不符）。"
                    f"请核对数据是否符合预期。"
                )

    # 图片完整性自检：CSV 拆分调用若误用同一 --img，会把多曲线图覆盖成单曲线图
    if img_fingerprint and _file_fingerprint(output_path) != img_fingerprint:
        warnings.append(
            "图片文件在 CSV 导出过程中被改写（引擎的 CSV 模式也会写 --img）——"
            "图中曲线可能只剩最后一次导出的那一条，请核对后重跑。"
        )

    # CSV 完整性提示
    csv_count = sum(1 for a in artifacts if a["type"] == "csv")
    if csv_count > 0:
        warnings.append(f"CSV 已生成（{csv_count} 个文件），请核对行数和列数与预期一致后再使用数据。")
    elif csv_path:
        warnings.append(
            "CSV 未生成：引擎对无效/无数据的曲线会静默出图（HB 下为空图）且不写 CSV，"
            "请核对 linename 的变量名大小写与存在性，以及 chart_type 是否与 RAW 的 Plotname 一致。"
        )

    # ── 执行成功判定（引擎 rc 恒 0 ⇒ 靠引擎措辞 + 产物）──
    _oks = [r.get("ok") for r in command_runs]
    engine_ok = (False if any(o is False for o in _oks)
                 else (True if _oks and all(o is True for o in _oks) else None))
    report = engine_report(command_runs)
    failures = engine_failures(command_runs)
    if engine_ok is False and failures:
        warnings.append("引擎自报未成功：" + "；".join(failures[:3]))

    if not img_generated:
        message = "图表生成失败，请检查 RAW 文件和参数。"
    elif csv_path and csv_count == 0:
        reason = next((l for l in failures if "CSV" in l), failures[0] if failures else "")
        message = f"CSV 未生成：{reason}" if reason else "CSV 未生成（详见 warnings / command_runs）。"
    elif engine_ok is False:
        # 引擎自报未成功（2026-09-30 真机：HB 数据传 --type SP 白跑 → 「绘制线段: ()」
        # + 照写空图 + rc=0）⇒ 不能再说"曲线图已生成"，否则只看 message 的人/agent 会判成功。
        reason = failures[0] if failures else "引擎未报出具体原因（详见 command_runs）"
        message = f"引擎自报未成功：{reason}"
    else:
        tail = report[-1] if report else ""
        message = f"曲线图已生成（引擎：{tail}）" if tail else "曲线图已生成。"

    # 引擎原文落盘（2026-09-30 起）：返回里的 stderr/command_runs 只到模型为止，模型可能省略转述
    # （用户反馈过"只回一句 message"），落盘后 user/agent 随时可读全文，不依赖模型自觉。
    log_path, log_lines = "", 0
    try:
        log_path, log_lines = append_engine_log(
            raw_file, command_runs, tool="turbocharts_convert",
            context={"chart_type": chart_type,
                     "linename": linename or "(all)",
                     "img": Path(output_path).name,
                     "csv": Path(csv_path).name if csv_path else "",
                     "engine_ok": engine_ok},
        )
    except OSError as exc:
        _logger.warning("引擎原文日志落盘失败 path=%s err=%s", engine_log_path(raw_file), exc)
        warnings.append(f"引擎原文日志落盘失败：{exc}（原文仍见返回的 stderr / command_runs）")

    resp: dict[str, Any] = {
        # 引擎 rc 恒为 0（曲线不存在/无数据也 rc=0），判成败看 img_generated 而非 return_code
        "success": img_generated,
        "return_code": result.returncode,
        "command": " ".join(cmd_img),
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "command_runs": command_runs,
        "img_generated": img_generated,
        "csv_generated": csv_count > 0,
        "curves": curves,
        "artifacts": artifacts,
        "engine_ok": engine_ok,
        "engine_report": report,
        # 引擎原文日志（追加写，与 RAW 同目录）：log_lines = 本次新增行数
        "log_path": log_path,
        "log_lines": log_lines,
        # 只在 CSV 真的落盘时才回填路径（TC-1：以前无条件回填，调用方按它取文件必扑空）
        "output_paths": {"img": output_path}
        | ({"csv": csv_path} if csv_path and csv_count == 1 and len(csv_plan) <= 1 else {}),
        "message": message,
    }
    if csv_labels:
        resp["curve_labels"] = csv_labels
    if warnings:
        resp["warnings"] = warnings
    if result.returncode == 0 and img_generated:
        resp.update(build_file_link(output_path, "打开曲线图"))
    return resp
