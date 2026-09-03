r"""Turbocharts MCP 工具 — ADS RAW 文件转曲线图与 CSV。


turbocharts_convert   将 ADS 仿真 RAW 结果转为 PNG 曲线图和 CSV


自然语言调用示例：
    帮我把 D:\results\result_tr.raw 转成 S 参数增益曲线图，
    输出到 D:\results\gain.png，曲线 DB_S[2,1]，依赖轴 freq

    帮我把 D:\results\result.raw 转成噪声系数图，输出 noise.png，
    曲线 real_nf(1)，同时导出 CSV 到 noise.csv

参数说明：
    raw_path    ADS RAW 结果文件路径（必填）
    img_path    输出图片路径，支持 PNG/JPG 等（必填）
    chart_type  转换类型："SP"（S参数）、"HB"（谐波平衡）、"XDB"（必填）

    可选参数：
    csv_path    同时导出的 CSV 文件路径
    linename    曲线名，格式 单位_曲线名[端口]，多条用 & 分隔
              DB_S[2,1]（增益）  DB_S[1,2]（反向增益）
              real_nf(1)（噪声） VSWR_S[1,1]（驻波）
              real_delayS[2,1]（时延） APS_S[2,1]（附加相移）
    dependency  依赖轴，通常为 "freq"
    ac_config   精度配置，格式 ac_type#bit#data#nv_type#nv_value
              例 "phase#3#S[2,1]#fv#0.1"
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from servers.eda.config import TURBOCHARTS_PATH
from servers.utils import build_artifact, build_file_link, error_response, validate_file
from servers.turbocharts.config import run_turbocharts
from servers import mcp

_logger = logging.getLogger("turbocharts.convert")

def _suggest_curves(var_name: str, var_type: str) -> list[str]:
    """Generate TurboCharts-compatible curve names for a variable.

    Rules:
      - real type → only "real_{name}"
      - complex S-param S[n,n] (reflection) → DB, real, phase, VSWR
      - complex S-param S[n,m] (transmission) → DB, real, phase (no VSWR)
      - S.delay[x,y] → real_delayS[x,y]
      - Other complex → DB, real, phase
    """
    curves: list[str] = []

    # Detect special patterns first (before generic real/complex)
    delay_m = re.match(r'S\.delay\[(\d+),(\d+)\]', var_name)
    if delay_m:
        # Known turbocharts crash (0xC0000005) for real_delayS — skip
        return curves

    if var_type == "real":
        # Skip known crash patterns: nf(1), nf(2), nfmin
        is_nf = bool(re.fullmatch(r'nf(?:min|[[(]\d+[\])])?', var_name, re.IGNORECASE))
        if is_nf:
            return curves  # turbocharts crashes with real_nf(*) regardless of bracket type
        # Normalize square brackets → round for other real variables
        safe_name = var_name.replace("[", "(").replace("]", ")")
        curves.append(f"real_{safe_name}")
        return curves

    # Detect S[n,m] pattern
    s_m = re.match(r'S\[(\d+),(\d+)\]', var_name)
    if s_m:
        i, j = s_m.group(1), s_m.group(2)
        curves.append(f"DB_S[{i},{j}]")
        curves.append(f"real_S[{i},{j}]")
        curves.append(f"phase_S[{i},{j}]")
        if i == j:  # reflection parameter
            curves.append(f"VSWR_S[{i},{j}]")
        return curves

    # Other complex: DB, real, phase
    curves.append(f"DB_{var_name}")
    curves.append(f"real_{var_name}")
    curves.append(f"phase_{var_name}")
    return curves


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
            current = {
                "plot_name": plot_name,
                "dependencies": [],
                "variables": [],
                "suggested_curves": [],
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
            continue

        if line.startswith("Values:"):
            in_variables = False
            continue

        if in_variables and line and not line.startswith("File Format:") \
                and not line.startswith("Plotname:"):
            # Handle both space-separated and tab-separated formats
            parts = line.replace("\t", " ").split()
            if len(parts) < 3:
                continue
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
                for c in _suggest_curves(var_name, var_type):
                    if c not in current["suggested_curves"]:
                        current["suggested_curves"].append(c)

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
            for c in _suggest_curves(v["name"], v["type"]):
                if c not in xml_curves:
                    xml_curves.append(c)
        result = {"format": "XML", "datasets": [{
            "plot_name": "",
            "dependencies": xml_deps,
            "variables": xml_vars,
            "suggested_curves": xml_curves,
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

    支持 MDS 和 XML 格式。画图前调用，避免猜测曲线名。
    返回的 suggested_curves 可直接用于 turbocharts_convert 的 linename 参数。

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
    return response


def _build_cmd(raw_path, img_path, chart_type, *, linename="", dependency="", csv_path="", ac_config=""):
    """构造 turbocharts 命令行参数列表（基础参数 + 可选参数）。"""
    cmd = [TURBOCHARTS_PATH, "--raw", raw_path, "--img", img_path, "--type", chart_type]
    if linename:
        cmd.extend(["--linename", linename])
    if csv_path:
        cmd.extend(["--csv", csv_path])
    if dependency:
        cmd.extend(["--dependcy", dependency])
    if ac_config:
        cmd.extend(["--ac", ac_config])
    return cmd


@mcp.tool()
def turbocharts_convert(
    raw_path: str,
    img_path: str,
    chart_type: str,
    csv_path: str = "",
    linename: str = "",
    dependency: str = "",
    ac_config: str = "",
) -> dict[str, Any]:
    """ADS RAW 转曲线图+CSV（SP/HB/XDB）。多条 VSWR 自动拆分 CSV，导出后核对数据。

    用法："把 result.raw 转成增益曲线"、"生成驻波图同时导出 CSV"
    CSV 注意：多条 VSWR 自动逐条分次导出，DB 类可一次多条。导出后核对行数列数。
    精度配置格式：ac_type#bit#data#nv_type#nv_value（如 phase#3#S[2,1]#fv#0.1）

    支持的转换类型（--type）:
        SP  - S 参数分析（增益、驻波、时延、噪声等）
        HB  - 谐波平衡分析
        XDB - XDB 分析

    曲线名格式（--linename）:
        格式为 单位_曲线名[端口]，多条曲线用 & 分隔。
        常用单位前缀: DB（dB值）、real（实数）、VSWR（驻波）、
                     APS（附加相移）、MAS（衰减态幅度）、
                     MV（幅度波动）、PSS（移相态）
        常用曲线示例:
        ├─ DB_S[2,1]          S参数输出增益
        ├─ DB_S[1,2]          S参数反向增益
        ├─ real_nf(1)         噪声系数
        ├─ VSWR_S[1,1]        输入驻波
        ├─ real_delayS[2,1]   群时延
        ├─ APS_S[2,1]         数控衰减器附加相移
        ├─ MAS_S[2,1]         数控移相器衰减态
        ├─ MV_S[2,1]          数控移相器幅度波动
        └─ PSS_S[2,1]         数控移相器移相态

    CSV 导出:
        DB 类曲线多条可一次导出。
        多条 VSWR 自动拆分为多次 CSV 调用（每次一条），导出后核对行数列数。

    精度配置（--ac）:
        格式: ac_type#bit#data#nv_type#nv_value
        ac_type: phase（相位精度）或 att（衰减精度）
        bit:     精度位数（正整数）
        data:    曲线名称（多条用 & 分隔）
        nv_type: fv（固定间隔）或 cl（完整列表）
        nv_value: 间隔值（fv）或用逗号分隔的值列表（cl）
        示例: "phase#3#S[2,1]#fv#0.1"

    Args:
        raw_path: 输入的 ADS RAW 文件路径（必填）。
        img_path: 输出的图像文件路径，支持 PNG/JPG 等（必填）。
        chart_type: 转换类型，如 "SP"、"HB"、"XDB"（必填）。
        csv_path: 可选，同时导出的 CSV 文件路径。
        linename: 可选，曲线名，格式为 单位_曲线名[端口]。
        dependency: 可选，依赖轴名称，通常为 "freq"。
        ac_config: 可选，精度配置，格式 ac_type#bit#data#nv_type#nv_value。

    Returns:
        {"success": True, "img_generated": True, "csv_generated": True,
         "artifacts": [{"type":"image","path":"...","generated_by":"turbocharts_convert"}, ...],
         "warnings": ["CSV 已生成（2 个文件），请核对..."], "output_paths": {"img":"...","csv":"..."}}
    """
    validate_file(raw_path)
    validate_file(TURBOCHARTS_PATH)

    _logger.info("turbocharts_convert raw=%s img=%s type=%s linename=%s csv=%s dep=%s ac=%s",
                 Path(raw_path).name, Path(img_path).name, chart_type,
                 linename or "(all)", Path(csv_path).name if csv_path else "(none)",
                 dependency or "(none)", ac_config or "(none)")

    # 校验输出图片扩展名
    img_ext = Path(img_path).suffix.lower()
    if img_ext not in (".png", ".jpg", ".jpeg", ".bmp", ".svg"):
        raise ValueError(f"img_path 扩展名不支持: {img_ext}，请使用 PNG/JPG/BMP/SVG")

    warnings: list[str] = []
    artifacts: list[dict] = []

    # ── VSWR CSV 自动拆分 ──
    # turbocharts_app.exe 的限制：CSV 模式下多条 VSWR_S 只输出第一条。
    # 因此当 linename 包含多条 VSWR 且需要 CSV 时，自动拆分为多次调用：
    #   - PNG 图片：所有曲线一起画（图片不受此限制）
    #   - CSV：每条 VSWR 独立一次调用，避免数据丢失
    #   - 非 VSWR 曲线：合并为一次调用
    vswr_curves = re.findall(r'VSWR_S\[\d+,\d+\]', linename) if linename else []
    non_vswr = re.sub(r'&+', '&', re.sub(r'VSWR_S\[\d+,\d+\]', '', linename)).strip('&') if linename else ''
    need_split = csv_path and len(vswr_curves) > 1  # 需要拆分：有 CSV 且多条 VSWR

    # ── 第 1 步：生成 PNG 图片（所有曲线一次性合并，VSWR 不受影响）──
    cmd_img = _build_cmd(raw_path, img_path, chart_type, linename=linename, dependency=dependency, ac_config=ac_config)

    result = run_turbocharts(cmd_img, timeout_seconds=120)
    img_generated = Path(img_path).exists()
    if img_generated:
        artifacts.append(build_artifact("image", img_path, "turbocharts_convert"))

    # ── 第 2 步：CSV 导出（自动拆分 VSWR） ──
    if need_split:
        csv_base = Path(csv_path)
        csv_stem = csv_base.stem
        csv_ext = csv_base.suffix
        csv_dir = csv_base.parent

        # 非 VSWR 曲线：一条调用
        if non_vswr:
            csv_non = str(csv_dir / f"{csv_stem}{csv_ext}")
        else:
            csv_non = None

        # 每条 VSWR：独立调用
        vswr_artifacts = []
        for vswr in vswr_curves:
            csv_vswr = str(csv_dir / f"{csv_stem}_{vswr.replace('[','').replace(']','').replace(',','_')}{csv_ext}")
            cmd_csv = _build_cmd(raw_path, img_path, chart_type, linename=vswr, dependency=dependency, csv_path=csv_vswr, ac_config=ac_config)
            proc = run_turbocharts(cmd_csv, timeout_seconds=120)
            if proc.returncode == 0 and Path(csv_vswr).exists():
                artifacts.append(build_artifact("csv", csv_vswr, "turbocharts_convert"))

        # 非 VSWR 曲线 CSV
        if csv_non and non_vswr:
            cmd_non = _build_cmd(raw_path, img_path, chart_type, linename=non_vswr, dependency=dependency, csv_path=csv_non, ac_config=ac_config)
            proc = run_turbocharts(cmd_non, timeout_seconds=120)
            if proc.returncode == 0 and Path(csv_non).exists():
                artifacts.append(build_artifact("csv", csv_non, "turbocharts_convert"))

        warnings.append(f"VSWR 曲线已自动拆分为 {len(vswr_curves)} 次 CSV 导出：{', '.join(vswr_curves)}")
    elif csv_path:
        # 单条 VSWR 或无 VSWR，正常调用
        cmd_csv = _build_cmd(raw_path, img_path, chart_type, linename=linename, dependency=dependency, csv_path=csv_path, ac_config=ac_config)
        r = run_turbocharts(cmd_csv, timeout_seconds=120)
        if r.returncode == 0 and Path(csv_path).exists():
            artifacts.append(build_artifact("csv", csv_path, "turbocharts_convert"))

    # CSV 完整性提示
    csv_count = sum(1 for a in artifacts if a["type"] == "csv")
    if csv_count > 0:
        warnings.append(f"CSV 已生成（{csv_count} 个文件），请核对行数和列数与预期一致后再使用数据。")

    resp = {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "command": " ".join(cmd_img),
        "stdout": result.stdout.strip() or "",
        "stderr": result.stderr.strip() or "",
        "img_generated": img_generated,
        "csv_generated": csv_count > 0,
        "artifacts": artifacts,
        "output_paths": {"img": img_path} | ({"csv": csv_path} if csv_path and not need_split else {}),
        "message": "曲线图已生成。" if img_generated else "图表生成失败，请检查 RAW 文件和参数。",
    }
    if warnings:
        resp["warnings"] = warnings
    if result.returncode == 0 and img_generated:
        resp.update(build_file_link(img_path, "打开曲线图"))
    return resp


