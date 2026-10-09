r"""Report_Exporter.exe 包装 — 独立报告导出（document.pdf / document.docx / report.json）。

命令（见 exe 同目录 Report_Exporter使用说明.md）：
    Report_Exporter.exe --json-file <json> --output <dir> [--schematic <img>]
stdout 单行 UTF-8 JSON：{"success":bool,"pdf_path","docx_path","report_data_path","errors":[]}
退出码 0/1。本工具用 --json-file 方式（避免 Windows 内联 JSON 引号/长度限制），
JSON 结构透传、校验交给 exe（errors 会原样带回 code/stage/message）。
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import REPORT_EXPORTER_PATH, fmt_errors, run_exe
from servers.utils import build_artifact, build_file_link, error_response, per_tool_mutex

_logger = logging.getLogger("simulation_tool.report_export")


def _absolutize_images(report: dict) -> dict:
    """把 report 里的图片路径统一转成绝对路径。

    exe 的规则是「JSON 文件里的相对路径按 JSON 所在目录解析」，而本工具把 JSON 写在临时
    目录 ⇒ 相对路径必然找不到图片（实测 `FileNotFoundError: 图片不存在：…\Temp\edi_report_xx\…`）。
    这里按服务进程当前目录转绝对，覆盖 `schematic_path` / `charts[].image_path` /
    旧版 `chart_paths[]` 三处。
    """
    report = dict(report)
    p = report.get("schematic_path")
    if isinstance(p, str) and p:
        report["schematic_path"] = str(Path(p).expanduser().resolve())
    charts = report.get("charts")
    if isinstance(charts, list):
        report["charts"] = [
            dict(c, image_path=str(Path(c["image_path"]).expanduser().resolve()))
            if isinstance(c, dict) and isinstance(c.get("image_path"), str) and c["image_path"]
            else c
            for c in charts
        ]
    paths = report.get("chart_paths")          # 旧版单链路兼容字段
    if isinstance(paths, list):
        report["chart_paths"] = [
            str(Path(x).expanduser().resolve()) if isinstance(x, str) and x else x
            for x in paths
        ]
    return report


@mcp.tool()
@per_tool_mutex
def export_simulation_report(
    report: dict,
    output_dir: str,
    schematic: str = "",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """用 Report_Exporter.exe 把报告 JSON 渲染成 PDF/DOCX/report.json（独立 exe，不依赖 HTTP 渲染服务）。

    用法："把这组电参数导出成仿真报告"、"生成 PDF 到 D:/reports/run001"

    report 透传 exe 的原生 JSON（见 exe 同目录 Report_Exporter使用说明.md），常用字段：
        model_name(封面型号) / description(简介) / conclusion(结论) /
        components([{type,model,manufacturer,specs}]) /
        links([{name, electrical_parameters:[{indicator,minimum,typical,maximum,unit,requirement,result}]}]) /
        charts([{link,indicator,image_path}]) / schematic_path(拓扑图)
    links 的 electrical_parameters 结构与 run_tr_simulation 产出的 results 一致，可直接衔接。

    Args:
        report: 报告数据对象（透传 Report_Exporter 原生 JSON，非空 dict）。
            ⚠️ 图片路径请传绝对路径：`schematic_path` / `charts[].image_path` 若写成相对路径，
            会被本工具按服务进程当前目录转成绝对（exe 只按「JSON 文件所在目录」解析，而本工具
            把 JSON 写在临时目录，相对路径必然找不到图）。
        output_dir: 输出目录（自动创建，产出 document.pdf / document.docx / report.json；三个文件任一已存在时 exe 会拒绝）。
        schematic: 可选，原理图图片路径（覆盖 report 里的 schematic_path）。
        timeout_seconds: 最长等待秒数，默认 300。

    Returns:
        {"success": True, "report_dir": "...", "pdf_path": "...", "docx_path": "...",
         "report_data_path": "...", "chart_count": 0, "errors": [],
         "artifacts": [...], "links": {"pdf": {...}, "docx": {...}}}
    """
    # 1. report 必须是非空对象
    if not isinstance(report, dict) or not report:
        return error_response("INVALID_REPORT_JSON", "report 必须是包含报告数据的非空对象")

    # 2. output_dir 非空，解析为绝对路径（exe 相对路径按进程 cwd 算，这里统一转绝对）
    if not isinstance(output_dir, str) or not output_dir.strip():
        return error_response("INVALID_OUTPUT_DIR", "output_dir 不能为空")
    out_dir = Path(output_dir).expanduser().resolve()

    # 2.5 输出目录占用预检：exe 也会拒绝，但这里能给出语义化错误码 + 冲突文件，省一次 exe 白跑
    conflicts = [out_dir / n for n in ("document.pdf", "document.docx", "report.json")
                 if (out_dir / n).exists()]
    if conflicts:
        return error_response(
            "OUTPUT_FILE_BUSY",
            f"输出目录里已存在同名文件，请换一个新目录：{conflicts[0]}",
            hint="每次导出请用一个新目录（三个目标文件任一已存在都会被拒绝）",
            conflicts=[str(c) for c in conflicts])

    # 3. schematic（可选）存在性
    if schematic:
        if not isinstance(schematic, str) or not Path(schematic).is_file():
            return error_response("FILE_NOT_FOUND", f"原理图图片不存在: {schematic}",
                                  hint="图片路径建议传绝对路径；相对路径按服务进程当前目录解析")

    # 4. 图片路径统一转绝对，再写临时 JSON（--json-file 方式，UTF-8）
    #    exe 按「JSON 文件所在目录」解析相对路径，而 JSON 写在临时目录 ⇒ 必须先转绝对。
    report = _absolutize_images(report)
    try:
        payload = json.dumps(report, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return error_response("INVALID_REPORT_JSON", f"report 含无法 JSON 序列化的值: {exc}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="edi_report_"))
    tmp_json = tmp_dir / "report.json"
    tmp_json.write_text(payload, encoding="utf-8")

    args = ["--json-file", str(tmp_json), "--output", str(out_dir)]
    if schematic:
        args += ["--schematic", str(Path(schematic).resolve())]

    # 5. 运行 exe
    try:
        try:
            run = run_exe(REPORT_EXPORTER_PATH, args, timeout_seconds)
        except RuntimeError as exc:
            return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                                  hint="报告较大时可调大 timeout_seconds 后重试（上限 3600）")
        except ValueError as exc:
            return error_response("INVALID_PARAMETERS", str(exc))
        except FileNotFoundError as exc:
            return error_response("EXE_NOT_FOUND", str(exc), report_exporter_path=REPORT_EXPORTER_PATH,
                                  hint="确认 EDI 安装目录下 simulation_tool/simulation_report/Report_Exporter.exe 存在")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 6. 解析 stdout JSON
    data = run["json"]
    if data is None:
        return error_response(
            "REPORT_EXPORT_FAILED",
            run["json_error"] or "exe 未输出合法 JSON",
            stdout=run["stdout"], stderr=run["stderr"], return_code=run["returncode"],
        )
    if not isinstance(data, dict):
        return error_response("REPORT_EXPORT_FAILED", "exe 返回格式异常", stdout=run["stdout"])

    errors = data.get("errors") or []
    if run["returncode"] != 0 or not data.get("success"):
        return error_response(
            "REPORT_EXPORT_FAILED",
            fmt_errors(errors) or "报告导出失败",
            hint="详见 details.report_errors / stdout 里的 exe 原文；修正 report 内容后重试",
            report_errors=errors, stdout=run["stdout"], stderr=run["stderr"],
            return_code=run["returncode"],
        )

    # 7. 成功返回（pdf/docx/report.json 均已落盘）
    pdf = data.get("pdf_path", "")
    docx = data.get("docx_path", "")
    report_data = data.get("report_data_path", "")
    artifacts: list[dict] = []
    for typ, p in (("pdf", pdf), ("docx", docx), ("json", report_data)):
        if p and Path(p).is_file():
            artifacts.append(build_artifact(typ, p, "export_simulation_report"))

    links: dict[str, dict] = {}
    if pdf:
        links["pdf"] = build_file_link(pdf, "打开PDF报告", disposition="inline")
    if docx:
        links["docx"] = build_file_link(docx, "打开DOCX报告", disposition="attachment")

    resp: dict[str, Any] = {
        "success": True,
        "report_dir": data.get("report_dir", str(out_dir)),
        "pdf_path": pdf,
        "docx_path": docx,
        "report_data_path": report_data,
        "schematic_path": data.get("schematic_path", ""),
        "chart_count": data.get("chart_count", 0),
        "errors": errors,
        "artifacts": artifacts,
        "links": links,
        "message": f"报告已导出到 {data.get('report_dir', out_dir)}（document.pdf + document.docx + report.json）",
    }
    return resp
