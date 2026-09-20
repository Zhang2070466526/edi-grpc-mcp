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
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from servers.eda.config import TURBOCHARTS_PATH
from servers.utils import build_artifact, build_file_link, error_response, per_tool_mutex, validate_file
from servers.turbocharts.config import run_turbocharts
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
    (re.compile(r'^VSWR_(?P<var>.+)$', re.IGNORECASE), "VSWR",
     "VSWR_S[i,j]：说明中的 vswr(驻波)，注明只支持单条曲线"),
    (re.compile(r'^(?:APS|AF|MAS|MV|PSS)_(?P<var>.+)$', re.IGNORECASE), "数控量",
     "APS_/AF_/MAS_/MV_/PSS_：数控衰减器/移相器专有量（AF 为幅度波动；"
     "无对应器件时只有表头、无数据）"),
)

# HB 数据类型下引擎真正认的前缀；其它前缀会被静默忽略、退化成 dBm 图
_HB_USEFUL_PREFIXES = frozenset({"dBm", "DB", "real"})

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


def _suggest_curves(
    var_name: str, var_type: str, plot_type: str = ""
) -> tuple[list[str], list[str]]:
    """Generate TurboCharts-compatible curve names for a variable.

    Returns (curves, notes)，notes 说明被排除的写法与实测注意事项。

    Rules（2026-09-11 实测校准）:
      - type=HB 只认 dBm_/real_（phase_/imag_ 会被静默忽略并回退成 dBm 图）
      - S.delay[x,y] → real_delayS[x,y]（实测出 ps(S.delay[x,y])，有崩溃历史，note 提示）
      - real 类型 → real_{name}（nf 变量保留，引擎在缺该曲线时会段错误 → 调用前需校验存在性）
      - complex S[n,n]（反射）→ dBm_/DB_/real_/phase_/imag_/VSWR_
      - complex S[n,m]（传输）→ dBm_/DB_/real_/phase_/imag_
      - 其它 complex（如 HB 节点电压）→ dBm_/real_（SP 下再加 phase_/imag_）
    """
    curves: list[str] = []
    notes: list[str] = []
    chart = (plot_type or "").upper()

    delay_m = re.match(r'S\.delay\[(\d+),(\d+)\]', var_name)
    if delay_m:
        i, j = delay_m.group(1), delay_m.group(2)
        curves.append(f"real_delayS[{i},{j}]")
        notes.append(
            f"real_delayS[{i},{j}] 实测可出 ps(S.delay[{i},{j}])(引擎会打印 complex→real 警告)；"
            "历史上有 0xC0000005 崩溃记录，若失败请改用其它量并反馈。"
        )
        return curves, notes

    if var_type == "real":
        if re.fullmatch(r'nf(?:min|[[(]\d+[\])])?', var_name, re.IGNORECASE):
            curves.append(f"real_{var_name}")
            notes.append(
                f"real_{var_name} 为噪声系数：引擎在 RAW 缺少该曲线时直接段错误，"
                "本工具已在调用前校验变量是否存在。"
            )
            return curves, notes
        # Normalize square brackets → round for other real variables
        safe_name = var_name.replace("[", "(").replace("]", ")")
        curves.append(f"real_{safe_name}")
        return curves, notes

    s_m = re.match(r'S\[(\d+),(\d+)\]', var_name)
    if s_m:
        i, j = s_m.group(1), s_m.group(2)
        curves.append(f"dBm_S[{i},{j}]")
        curves.append(f"real_S[{i},{j}]")
        if chart == "HB":
            notes.append(
                "type=HB：引擎忽略 phase_/imag_ 前缀并回退成 dBm 图，故未列出；"
                "VSWR 对 HB 数据同样无意义。"
            )
            return curves, notes
        curves.append(f"DB_S[{i},{j}]")
        curves.append(f"phase_S[{i},{j}]")
        curves.append(f"imag_S[{i},{j}]")
        if i == j:  # reflection parameter
            curves.append(f"VSWR_S[{i},{j}]")
        else:
            notes.append(
                f"VSWR_S[{i},{j}] 也能出图，但标签退化成 VSWR({i})，非反射参数不建议使用。"
            )
        return curves, notes

    # 其它 complex（HB 的节点电压等）
    curves.append(f"dBm_{var_name}")
    curves.append(f"real_{var_name}")
    if chart == "HB":
        notes.append(
            "type=HB：phase_/imag_ 前缀会被引擎忽略（回退 dBm 图），已排除。"
        )
    else:
        curves.append(f"phase_{var_name}")
        curves.append(f"imag_{var_name}")
    return curves, notes


def _consume_variable_line(current: dict, raw_line: str) -> None:
    """解析一条变量定义行（列格式：<序号> <变量名> <类型描述...>）。"""
    parts = raw_line.replace("\t", " ").split()
    if len(parts) < 3:
        return
    var_name = parts[1]
    var_type = "complex"
    is_indep = False
    for p in parts[2:]:
        if p.startswith("type="):
            var_type = p.split("=", 1)[1]
        elif p.startswith("indep="):
            is_indep = p.split("=", 1)[1] == "yes"

    entry = {"name": var_name, "type": var_type}
    current["variables"].append(entry)

    if is_indep:
        if var_name not in current["dependencies"]:
            current["dependencies"].append(var_name)
    else:
        suggested, notes = _suggest_curves(var_name, var_type,
                                           current.get("plot_type", ""))
        for c in suggested:
            if c not in current["suggested_curves"]:
                current["suggested_curves"].append(c)
        for n in notes:
            if n not in current["curve_notes"]:
                current["curve_notes"].append(n)


def _parse_mds_format(text: str) -> list[dict]:
    """Parse MDS-format RAW file header.

    Format:
        File Format: MDS
        Plotname: SP SP1[1]
        No. Variables: 11
        Variables:
            0 freq frequency type=real indep=yes
            1 S[1,1] s-param type=complex indep=no
    """
    datasets: list[dict] = []
    current: dict | None = None
    in_variables = False

    for line in text.splitlines():
        line = line.strip()

        if line.startswith("Plotname:"):
            if current:
                datasets.append(current)
            plot_name = line.split(":", 1)[1].strip()
            # plot 类型（SP/HB/XDB）决定哪些前缀真实可用
            first_token = plot_name.split()[0].upper() if plot_name else ""
            plot_type = first_token if first_token in ("SP", "HB", "XDB") else ""
            current = {
                "plot_name": plot_name,
                "plot_type": plot_type,
                "dependencies": [],
                "variables": [],
                "suggested_curves": [],
                "curve_notes": [],
            }
            # Extract freq= from plotname (e.g. "SP SP1[1] freq=(1 GHz->10 GHz)")
            freq_m = re.search(r'freq\s*=\s*\(([^)]+)\)', plot_name, re.IGNORECASE)
            if freq_m and "freq" not in current["dependencies"]:
                current["dependencies"].append("freq")
            in_variables = False
            continue

        if current is None:
            continue

        if line.startswith("No. Variables:"):
            continue

        if line.startswith("Variables:"):
            in_variables = True
            # 真实 MDS 文件把第一个变量写在 "Variables:" 行内
            # （Variables:\t0\tfreq\tfrequency type=real indep=yes），不能整行丢弃
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                _consume_variable_line(current, remainder)
            continue

        if line.startswith("Values:"):
            in_variables = False
            continue

        if in_variables and line and not line.startswith("File Format:") \
                and not line.startswith("Plotname:"):
            _consume_variable_line(current, line)

    if current:
        datasets.append(current)
    return datasets


def _parse_raw_header(raw_path: str) -> dict:
    """Parse ADS RAW file header and return structured curve info.

    Returns {"format": str, "datasets": [...], "error": str|None}.
    """
    try:
        with open(raw_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read(65536)
    except OSError as e:
        return {"format": "unknown", "datasets": [], "error": str(e)}

    reached_limit = len(text) >= 65536

    # MDS format
    if "File Format: MDS" in text or "Plotname:" in text:
        datasets = _parse_mds_format(text)
        if not datasets:
            return {"format": "MDS", "datasets": [],
                    "warning": "Found MDS header but no Plotname entries"}
        has_vars = any(d["variables"] for d in datasets)
        if not has_vars:
            return {"format": "MDS", "datasets": datasets,
                    "warning": "MDS format detected but no variables extracted"}
        result = {"format": "MDS", "datasets": datasets}
        if reached_limit:
            result["warning"] = "RAW 文件头可能被截断，结果可能不完整"
        return result

    # XML-style: <Number name="freq"/> <Complex name="S(2,1)"/> <Real name="nf(1)"/>
    xml_vars: list[dict] = []
    xml_deps: list[str] = []
    xml_notes: list[str] = []
    seen_xml = set()
    for m in re.finditer(r'<(Number|Complex|Real)\s+[^>]*name="([^"]+)"', text):
        tag = m.group(1)
        name = m.group(2)
        if name not in seen_xml:
            seen_xml.add(name)
            var_type = "complex" if tag == "Complex" else "real"
            xml_vars.append({"name": name, "type": var_type})

    if xml_vars:
        dep_kw = {"freq", "frequency", "time", "power", "bias"}
        for v in xml_vars[:]:
            if v["name"].lower() in dep_kw or re.match(r'^freq', v["name"], re.IGNORECASE):
                xml_deps.append(v["name"])
                xml_vars.remove(v)
        xml_curves: list[str] = []
        for v in xml_vars:
            suggested, notes = _suggest_curves(v["name"], v["type"], "")
            for c in suggested:
                if c not in xml_curves:
                    xml_curves.append(c)
            for n in notes:
                if n not in xml_notes:
                    xml_notes.append(n)
        result = {"format": "XML", "datasets": [{
            "plot_name": "",
            "plot_type": "",
            "dependencies": xml_deps,
            "variables": xml_vars,
            "suggested_curves": xml_curves,
            "curve_notes": xml_notes,
        }]}
        if reached_limit:
            result["warning"] = "RAW 文件头可能被截断，结果可能不完整"
        return result

    # Unknown
    return {"format": "unknown", "datasets": [],
            "error": "Unsupported RAW format. Expected MDS or XML."}


@mcp.tool()
def list_result_curves(result_path: str) -> dict[str, Any]:
    """解析 ADS RAW 仿真结果文件，返回可用曲线名和依赖轴。

    支持 MDS 和 XML 格式。画图前调用，避免猜测曲线名；曲线名的单位/线段名写法见资源
    edi://reference/turbocharts-guide（引擎自带《RAW 转图像工具使用说明》原文）。
    suggested_curves 只列该 plot 类型下**实测真正有效**的写法（HB 数据不给
    phase_/imag_，因为引擎会静默回退成 dBm 图），被排除的写法与原因见 curve_notes。

    ⚠️ suggested_curves 是可用写法清单，不是"随便挑一条都对"：变量名大小写必须与
    variables 一致，多条曲线用 & 连接（逗号非法）。导出后用 CSV 表头核对实际画出的量。

    Args:
        result_path: RAW 结果文件路径（必须已存在）。
    """
    try:
        resolved = validate_file(result_path)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FILE_NOT_FOUND", str(e))

    result = _parse_raw_header(resolved)

    if result.get("error") and not result.get("datasets"):
        return error_response("UNSUPPORTED_RAW_FORMAT", result["error"],
                          result_path=resolved, format=result.get("format", "unknown"))

    response: dict = {
        "success": True,
        "result_path": resolved,
        "format": result["format"],
        "datasets": result["datasets"],
    }
    if result.get("warning"):
        response["warning"] = result["warning"]
    # Multi-plot warning: turbocharts_app.exe currently only reads the first plot
    if len(result["datasets"]) > 1:
        response["warning"] = (
            response.get("warning", "")
            + f" RAW 包含 {len(result['datasets'])} 个 plot，turbocharts 当前只处理第一个"
            f" ({result['datasets'][0].get('plot_name', '')})。"
            + " 如需画其他 plot 的曲线，请手动提取对应的 plot 数据。"
        ).strip()
    notes: list[str] = []
    for d in result["datasets"]:
        for n in d.get("curve_notes", []):
            if n not in notes:
                notes.append(n)
    if notes:
        response["curve_notes"] = notes
    return response


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


@mcp.tool()
@per_tool_mutex
def turbocharts_convert(
    raw_path: str,
    output_path: str,
    chart_type: str,
    csv_path: str = "",
    linename: str = "",
    dependency: str = "",
    ac_config: str = "",
) -> dict[str, Any]:
    """ADS RAW 转曲线图+CSV（SP/HB/XDB）；linename 为「单位_线段名」，多条用 & 分隔。

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
        output_path: 输出的图像文件路径，支持 PNG/JPG/BMP/SVG（必填）；父目录会自动创建。
        chart_type: 转换类型，如 "SP"、"HB"、"XDB"（必填，且要与 RAW 的 Plotname 一致）。
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
    """
    try:
        raw_file = validate_file(raw_path)
        validate_file(TURBOCHARTS_PATH)
    except (FileNotFoundError, ValueError) as e:
        return error_response("FILE_NOT_FOUND", str(e))

    chart_type = (chart_type or "").strip() or "SP"

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

    # ── 第 0 步：目录准备（引擎不会自建目录，失败时它仍返回 0）──
    for candidate in (output_path, csv_path):
        if candidate:
            err = _ensure_parent(candidate)
            if err:
                return error_response("INVALID_PATH", err)

    # ── 第 0.1 步：linename 校验（语法 + 变量存在性）──
    raw_info = _parse_raw_header(raw_file)
    if raw_info.get("warning"):
        warnings.append(raw_info["warning"])
    datasets = raw_info.get("datasets") or []
    known_vars = [v["name"] for d in datasets for v in d.get("variables", [])]

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
        return error_response("TOOL_TIMEOUT", str(exc))
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
        try:
            cmd_csv = _build_cmd(raw_file, str(tmp_img), chart_type,
                                 linename="&".join(group), dependency=dependency,
                                 csv_path=target_csv, ac_config=ac_config)
            proc = run_turbocharts(cmd_csv, timeout_seconds=120)
        except RuntimeError as exc:
            warnings.append(f"turbocharts 导出超时: {exc}")
            continue
        finally:
            tmp_img.unlink(missing_ok=True)
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

    resp: dict[str, Any] = {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "command": " ".join(cmd_img),
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
        "img_generated": img_generated,
        "csv_generated": csv_count > 0,
        "curves": curves,
        "artifacts": artifacts,
        "output_paths": {"img": output_path} | ({"csv": csv_path} if csv_path and len(csv_plan) <= 1 else {}),
        "message": "曲线图已生成。" if img_generated else "图表生成失败，请检查 RAW 文件和参数。",
    }
    if csv_labels:
        resp["curve_labels"] = csv_labels
    if warnings:
        resp["warnings"] = warnings
    if result.returncode == 0 and img_generated:
        resp.update(build_file_link(output_path, "打开曲线图"))
    return resp
