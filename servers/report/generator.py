"""仿真报告渲染 — 16 步校验管线 → POST 本地渲染服务 → 自动注册预览 Token。

校验顺序：output_path → model_name → description/conclusion → spec_table（固定7列）
→ charts（绝对路径+后缀）→ components（四项全字符串，支持中英文key）→ schematic
调用后自验证：文件存在、非空、路径一致。
返回：preview_url（10 分钟 HTTP Token）+ markdown_link + artifacts + 本地路径兜底。
"""

from __future__ import annotations

import json as _json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from servers import mcp
from servers.settings import get_settings
from servers.utils import build_file_link, is_network_path, error_response
from servers.multimodal_vision.document import register_document_url

_error = error_response  # 别名，内部校验函数使用

load_dotenv()
_logger = logging.getLogger("report.generator")

_settings = get_settings()
_REPORT_URL = _settings.report_render_url
_REPORT_TIMEOUT = _settings.report_timeout
_ALLOWED_EXTENSIONS = {".pdf", ".docx"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_EXPECTED_SPEC_COLUMNS = 7
_MAX_CELL_LENGTH = 10_000
_MAX_FIELD_LENGTHS = {"description": 50_000, "conclusion": 50_000}


# ═══════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════

def _validate_output(path: str, overwrite: bool) -> tuple[str, str] | tuple[None, dict]:
    """校验输出路径（绝对、扩展名、目录存在、overwrite），返回 (文件类型, 错误)。"""
    if not isinstance(path, str) or not path.strip():
        return None, _error("INVALID_OUTPUT_PATH", "output_path 不能为空")
    p = Path(path)
    if not p.is_absolute():
        return None, _error("INVALID_OUTPUT_PATH", "output_path 必须是绝对路径")
    if is_network_path(p):
        return None, _error("INVALID_OUTPUT_PATH", "禁止网络路径")
    ext = p.suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        return None, _error("INVALID_OUTPUT_PATH", f"后缀必须是 .pdf 或 .docx")
    if not p.parent.is_dir():
        return None, _error("OUTPUT_DIRECTORY_NOT_FOUND", f"输出目录不存在: {p.parent}")
    if p.exists() and not overwrite:
        return None, _error("OUTPUT_ALREADY_EXISTS",
                             f"输出文件已存在: {p.name}。如需覆盖，请设置 overwrite=true")
    return ext.lstrip("."), None


def _validate_spec_table(table: list | None) -> dict | None:
    """校验 spec_table 为二维数组、固定 7 列、单元格类型合法。"""
    if table is None:
        return None
    if not isinstance(table, list):
        return error_response("INVALID_REPORT_PARAMETERS", "spec_table 必须是数组")
    if not table:
        return None
    if not all(isinstance(row, list) for row in table):
        return error_response("INVALID_REPORT_PARAMETERS", "spec_table 必须是二维数组")
    if len(table) > 1000:
        return error_response("INVALID_REPORT_PARAMETERS", "spec_table 最多 1000 行")
    if len(table[0]) != _EXPECTED_SPEC_COLUMNS:
        return error_response("INVALID_REPORT_PARAMETERS",
                       f"spec_table 必须固定为 {_EXPECTED_SPEC_COLUMNS} 列")
    for i, row in enumerate(table):
        if len(row) != _EXPECTED_SPEC_COLUMNS:
            return error_response("INVALID_REPORT_PARAMETERS",
                           f"spec_table 第 {i+1} 行列数({len(row)})与表头({_EXPECTED_SPEC_COLUMNS})不一致")
        for cell in row:
            if cell is None or isinstance(cell, bool) or not isinstance(cell, (str, int, float)):
                return error_response("INVALID_REPORT_PARAMETERS",
                               "spec_table 单元格仅允许字符串、整数、浮点数")
            if isinstance(cell, str) and len(cell) > _MAX_CELL_LENGTH:
                return error_response("INVALID_REPORT_PARAMETERS",
                               f"spec_table 单元格过长（最大 {_MAX_CELL_LENGTH} 字符）")
    return None


def _validate_charts(charts: list | None) -> tuple[list | None, list[str], dict | None]:
    """校验 charts 数组（path/title、后缀、数量），返回 (规范化列表, 警告, 错误)。"""
    warnings: list[str] = []
    if charts is None:
        return None, warnings, None
    if not isinstance(charts, list):
        return None, warnings, _error("INVALID_REPORT_PARAMETERS", "charts 必须是数组")
    if not charts:
        return None, warnings, None
    if len(charts) > 50:
        return None, warnings, _error("INVALID_REPORT_PARAMETERS", "charts 最多 50 张")
    validated: list[dict] = []
    for i, c in enumerate(charts):
        if not isinstance(c, dict):
            return None, warnings, _error("INVALID_REPORT_PARAMETERS", f"charts[{i}] 必须是对象")
        path = c.get("path", "")
        title = c.get("title", "")
        if not isinstance(path, str) or not isinstance(title, str):
            return None, warnings, _error("INVALID_REPORT_PARAMETERS",
                                           f"charts[{i}].path 和 title 必须是字符串")
        if len(title) > 500:
            return None, warnings, _error("INVALID_REPORT_PARAMETERS",
                                           f"charts[{i}].title 最长 500 字符")
        p = Path(path)
        if not p.is_absolute():
            return None, warnings, _error("INVALID_CHART_PATH", f"charts[{i}].path 必须是绝对路径")
        if is_network_path(p):
            return None, warnings, _error("INVALID_CHART_PATH", "禁止网络路径")
        if p.suffix.lower() not in _IMAGE_EXTENSIONS:
            return None, warnings, _error("INVALID_CHART_PATH",
                                           f"charts[{i}].path 后缀必须是 PNG/JPG/JPEG")
        if not p.is_file():
            warnings.append(f"图片未找到: {p.name}")
        validated.append({"path": str(p.resolve()), "title": title})
    return validated, warnings, None


# AI 可能使用中文 key，统一映射到 API 要求的英文 key
_COMPONENT_KEY_MAP = {
    "类型": "type", "器件类型": "type", "type": "type",
    "型号": "model", "器件型号": "model", "model": "model",
    "厂家": "manufacturer", "厂商": "manufacturer", "制造厂商": "manufacturer", "manufacturer": "manufacturer",
    "规格": "specs", "器件规格": "specs", "specs": "specs",
}


def _validate_components(comps: list | None) -> tuple[list | None, dict | None]:
    """校验并规范化 components（中文 key → 英文 key），不修改传入的 comps。

    Returns:
        (normalized_comps, error)。无错误时 error 为 None；
        comps 为 None 或空列表时 normalized_comps 为 None。
    """
    if comps is None:
        return None, None
    if not isinstance(comps, list):
        return None, error_response("INVALID_REPORT_PARAMETERS", "components 必须是数组")
    if not comps:
        return None, None
    if len(comps) > 500:
        return None, error_response("INVALID_REPORT_PARAMETERS", "components 最多 500 条")
    normalized_list: list[dict] = []
    for i, c in enumerate(comps):
        if not isinstance(c, dict):
            return None, error_response("INVALID_REPORT_PARAMETERS", f"components[{i}] 必须是对象")
        # 中文 key → 英文 key 映射
        normalized: dict = {}
        for k, v in c.items():
            target = _COMPONENT_KEY_MAP.get(k.strip(), k)
            normalized[target] = v
        for field in ("type", "model", "manufacturer", "specs"):
            val = normalized.get(field, "")
            if not isinstance(val, str):
                return None, error_response("INVALID_REPORT_PARAMETERS", f"components[{i}].{field} 必须是字符串")
            if not val.strip():
                return None, error_response("INVALID_REPORT_PARAMETERS",
                               f"components[{i}] 缺少必填字段 '{field}'（支持中英文 key）")
            if len(val) > 2000:
                return None, error_response("INVALID_REPORT_PARAMETERS",
                               f"components[{i}].{field} 最长 2000 字符")
        normalized_list.append(normalized)
    return normalized_list, None


def _validate_schematic(path: str) -> tuple[str | None, bool, dict | None]:
    """校验 schematic 图片路径，返回 (规范化路径, 是否缺失, 错误)。"""
    if not path or not isinstance(path, str) or not path.strip():
        return None, False, None
    p = Path(path)
    if not p.is_absolute():
        return None, False, _error("INVALID_REPORT_PARAMETERS", "schematic 必须是绝对路径")
    if is_network_path(p):
        return None, False, _error("INVALID_REPORT_PARAMETERS", "禁止网络路径")
    if p.suffix.lower() not in _IMAGE_EXTENSIONS:
        return None, False, _error("INVALID_REPORT_PARAMETERS",
                                   "schematic 后缀必须是 PNG/JPG/JPEG")
    missing = not p.is_file()
    return str(p.resolve()), missing, None


# ═══════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def generate_simulation_report(
    output_path: str,
    model_name: str,
    description: str = "",
    conclusion: str = "",
    spec_table: list | None = None,
    charts: list | None = None,
    components: list | None = None,
    schematic: str = "",
    overwrite: bool = False,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """生成本地仿真报告（PDF/DOCX），16 步校验后调用本地渲染服务。

    用法："帮我生成这个工程的仿真报告"、"输出 PDF 报告到 C:/report.pdf"

    spec_table：二维数组固定 7 列（指标/数值/单位/规格/规格单位/结论/状态）
    charts：[{"path": "C:/.../gain.png", "title": "增益曲线"}]
    components：type/model/manufacturer/specs 四项全字符串，manufacturer 不知道填 "N/A"
    默认禁止覆盖已有文件（overwrite=false）

    Returns:
        {"success": True, "file_path": "C:/.../report.pdf", "preview_url": "http://...",
         "markdown_link": "[打开PDF报告](http://...)", "message": "..."}
    """

    # ── 校验阶段（1-7：所有校验在 HTTP 调用前完成，避免无效请求）──

    # 1. 输出路径：绝对路径、扩展名 .pdf/.docx、父目录存在、overwrite 检查
    file_type, err = _validate_output(output_path, overwrite)
    if err:
        return err

    # 2. model_name：非空字符串，最长 200 字符
    if not isinstance(model_name, str):
        return error_response("INVALID_REPORT_PARAMETERS", "model_name 必须是字符串")
    mn = model_name.strip()
    if not mn:
        return error_response("INVALID_REPORT_PARAMETERS", "model_name 不能为空")
    if len(mn) > 200:
        return error_response("INVALID_REPORT_PARAMETERS", "model_name 最长 200 字符")

    # 3. description/conclusion：类型检查 + 长度限制
    for field, max_len in _MAX_FIELD_LENGTHS.items():
        val = locals().get(field, "")
        if not isinstance(val, str):
            return error_response("INVALID_REPORT_PARAMETERS", f"{field} 必须是字符串")
        if len(val) > max_len:
            return error_response("INVALID_REPORT_PARAMETERS", f"{field} 最长 {max_len} 字符")
    if not isinstance(overwrite, bool):
        return error_response("INVALID_REPORT_PARAMETERS", "overwrite 必须是布尔值")

    # 4. spec_table：二维数组，固定 7 列，单元格类型检查
    err = _validate_spec_table(spec_table)
    if err:
        return err

    # 5. charts：对象数组 {path, title}，图片路径绝对、后缀合法、最多 50 张
    valid_charts, chart_warnings, chart_error = _validate_charts(charts)
    if chart_error:
        return chart_error

    # 6. components：对象数组 {type, model, manufacturer, specs}，四项全字符串
    #    支持中英文 key 映射（如 "器件类型" → "type"）
    components, err = _validate_components(components)
    if err:
        return err

    # 7. schematic：绝对路径、图片后缀，缺失时记录 warning 不阻塞
    schematic_path, schematic_missing, s_err = _validate_schematic(schematic)
    if s_err:
        return s_err

    # 8. 超时解析：默认 45s，范围 5-120s
    effective_timeout = _REPORT_TIMEOUT
    if timeout_seconds is not None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
            return error_response("INVALID_REPORT_PARAMETERS", "timeout_seconds 必须是整数")
        effective_timeout = max(5, min(timeout_seconds, 120))

    # 9. 构建请求 payload
    expected_output = Path(output_path).expanduser().resolve()
    report_obj: dict = {"model_name": mn}
    if description:
        report_obj["description"] = description
    if conclusion:
        report_obj["conclusion"] = conclusion
    if spec_table:
        report_obj["spec_table"] = spec_table
    if valid_charts:
        report_obj["charts"] = valid_charts
    if components:
        report_obj["components"] = components
    if schematic_path:
        report_obj["schematic"] = schematic_path

    payload = {
        "output_path": str(expected_output),
        "file_type": file_type,
        "overwrite": overwrite,
        "report": report_obj,
    }

    # ── 调用阶段（10-16：HTTP 请求 → 响应校验 → 文件确认）──

    # 10. 调用本地报告渲染服务
    t0 = time.monotonic()
    body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _logger.info("report_render file=%s type=%s model=%s size=%d charts=%d comps=%d specs=%d overwrite=%s",
                 Path(str(expected_output)).name, file_type, mn, len(body),
                 len(valid_charts) if valid_charts else 0,
                 len(components) if components else 0,
                 len(spec_table) if spec_table else 0,
                 overwrite)
    if _logger.isEnabledFor(logging.DEBUG):
        _logger.debug("REPORT_REQ payload: %s", body.decode("utf-8"))
    try:
        with httpx.Client(timeout=effective_timeout) as client:
            resp = client.post(
                _REPORT_URL,
                content=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
    except httpx.ConnectError:
        _logger.error("report_connect_failed url=%s", _REPORT_URL)
        return error_response("REPORT_SERVICE_UNAVAILABLE",
                       "无法连接本地报告渲染服务，请确认服务已启动。", retryable=True)
    except httpx.TimeoutException:
        _logger.error("report_timeout timeout=%d url=%s", effective_timeout, _REPORT_URL)
        return error_response("REPORT_RENDER_TIMEOUT",
                       f"报告渲染超时（{effective_timeout}s）", retryable=True)
    except httpx.RequestError as e:
        return error_response("REPORT_SERVICE_UNAVAILABLE",
                       f"报告渲染请求失败: {e}", retryable=True)

    elapsed = round(time.monotonic() - t0, 1)
    _logger.info("report_render status=%d file_type=%s elapsed=%s",
                 resp.status_code, file_type, elapsed)

    # 11. Map HTTP errors
    if resp.status_code == 400:
        _logger.error("report_validation_failed body=%s", resp.text[:500])
        return error_response("REPORT_VALIDATION_FAILED",
                       f"报告数据校验失败: {_safe_json_text(resp)}")
    if resp.status_code == 409:
        _logger.warning("report_file_busy path=%s", str(expected_output))
        return error_response("OUTPUT_FILE_BUSY",
                       "输出文件无法写入（可能被占用）", retryable=True)
    if resp.status_code != 200:
        _logger.error("report_render_failed status=%d body=%s", resp.status_code, resp.text[:500])
        return error_response("REPORT_RENDER_FAILED",
                       f"报告渲染失败 (HTTP {resp.status_code}): {_safe_json_text(resp)}")

    # 12. Parse response
    try:
        data = resp.json()
    except Exception:
        return error_response("INVALID_REPORT_RESPONSE", "报告服务返回无法解析")

    if not isinstance(data, dict):
        return error_response("INVALID_REPORT_RESPONSE", "报告服务返回格式异常")
    if not data.get("success"):
        return error_response("REPORT_RENDER_FAILED", data.get("error", "报告渲染失败"))

    # 13. Verify returned path matches request
    returned_raw = data.get("file_path")
    if not isinstance(returned_raw, str) or not returned_raw.strip():
        return error_response("INVALID_REPORT_RESPONSE", "报告服务未返回有效 file_path")
    returned_path = Path(returned_raw).expanduser().resolve()
    if returned_path != expected_output:
        return error_response("REPORT_OUTPUT_PATH_MISMATCH",
                       f"报告服务返回的文件路径与请求不一致")

    # 14. Self-verify output file
    if not expected_output.is_file():
        return error_response("REPORT_OUTPUT_NOT_FOUND", "报告服务返回成功，但未找到生成的文件")
    actual_size = expected_output.stat().st_size
    if actual_size <= 0:
        return error_response("REPORT_OUTPUT_EMPTY", "生成的报告文件为空")

    # 15. Build response
    warnings: list[str] = list(chart_warnings)
    if schematic_missing:
        warnings.append("原理图文件未找到，已跳过链路拓扑章节")

    preview_url = register_document_url(str(expected_output),
                                         disposition="inline" if file_type == "pdf" else "attachment")
    md_link = f"[打开{file_type.upper()}报告]({preview_url})"

    result = {
        "success": True,
        "file_path": str(expected_output),
        "file_type": data.get("file_type", file_type),
        "file_size": actual_size,
        "artifacts": [{"type": file_type, "path": str(expected_output),
                       "name": Path(str(expected_output)).name,
                       "generated_by": "generate_simulation_report"}],
        "preview_url": preview_url,
        "markdown_link": md_link,
        "message": f"报告已生成：{md_link}\n\n本地路径：{expected_output}\n\n链接 10 分钟有效，仅本机可访问。",
        "sections": data.get("sections", []),
        "chart_count": data.get("chart_count", 0),
        "component_count": data.get("component_count", 0),
        "spec_row_count": data.get("spec_row_count", 0),
        "created_at": data.get("created_at", ""),
        "warnings": warnings,
        "output_verified": True,
    }
    result.update(build_file_link(str(expected_output), f"打开{file_type.upper()}报告"))
    return result


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _safe_json_text(resp) -> str:
    """从报告服务响应中安全提取错误文本，解析失败时截断返回原文。"""
    try:
        data = resp.json()
        return data.get("error", resp.text[:200])
    except Exception:
        return resp.text[:200]
