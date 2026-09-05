r"""仿真器件管理工具 — 工具 API v3，gRPC 协议 v2。

get_simulation_component_schema  查询器件支持的参数、类型、单位和权限
list_simulation_components       查询工程中的仿真器件及其参数
create_simulation_component      新增仿真器件（每次创建新实例）
update_simulation_component      按实例名更新参数（自动识别类型）
delete_simulation_component      按实例名删除器件（EDI 直接执行）
set_component_active_state       确定性设置 NORMAL/DISABLED/SHORTED
generate_schematic_from_netlist  从网表追加或重建 main 原理图

协议枚举：
  CREATE_SIMULATION_COMPONENT = 11
  DELETE_SIMULATION_COMPONENT = 12
  GENERATE_SCHEMATIC_FROM_NETLIST = 13
  SET_COMPONENT_ACTIVE_STATE = 14
  UPDATE_SIMULATION_COMPONENT = 15
"""

from __future__ import annotations

import json as _json
import logging
import math
import re as _re
from functools import lru_cache
from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.config import (
    validate_project_path,
    SIM_COMPONENT_TYPES,
)
from servers.eda.project_reader import ProjectReader, parse_components
from servers.eda.grpc_client import call_grpc, call_project_grpc
from servers.utils import is_network_path, require_nonempty, error_response
from servers import mcp

_logger = logging.getLogger("sim_components")

_COMPONENT_TYPES = SIM_COMPONENT_TYPES
_ACTIVE_STATES = {"NORMAL", "DISABLED", "SHORTED"}


# ═══════════════════════════════════════════════════════════
# Catalog
# ═══════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def _load_catalog() -> dict:
    """加载仿真器件参数目录 JSON（缓存单例），失败返回空 dict。"""
    try:
        here = Path(__file__).parent
        path = here / "simulation_component_catalog.json"
        return _json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, _json.JSONDecodeError) as exc:
        _logger.error("Failed to load component catalog: %s", exc)
        return {}


def _catalog_component(component_type: str) -> dict:
    """从目录中取出指定器件类型的定义，不存在返回空 dict。"""
    cat = _load_catalog()
    return cat.get("components", {}).get(component_type, {})


# ═══════════════════════════════════════════════════════════
# 参数解析
# ═══════════════════════════════════════════════════════════

def _resolve_parameter_schema(
        component_type: str,
        parameter_name: str,
) -> tuple[dict | None, str]:
    """Resolve a public parameter name to its schema and wire name.

    Search order:
      1. Fixed parameters (exact match).
      2. Dynamic patterns (Freq[{index}], Order[{index}]).

    Returns (parameter_schema, wire_name).  schema is None on failure.
    """
    comp = _catalog_component(component_type)
    if not comp:
        return None, ""

    # 1. Fixed parameters
    fixed = comp.get("parameters", {})
    if parameter_name in fixed:
        return fixed[parameter_name], fixed[parameter_name].get("wire_name", parameter_name)

    # 2. Dynamic patterns
    patterns = comp.get("parameter_patterns", [])
    for pat in patterns:
        public_pattern = pat.get("public_pattern", "")
        # 把 {index} 替换成捕获组
        regex = _re.escape(public_pattern).replace(r"\{index\}", r"(\d+)")
        m = _re.fullmatch(regex, parameter_name)
        if m:
            index = int(m.group(1))
            idx_min = pat.get("index_min", 1)
            idx_max = pat.get("index_max", 32)
            if index < idx_min or index > idx_max:
                return None, ""
            wire_pattern = pat.get("wire_pattern", public_pattern)
            wire_name = wire_pattern.replace("{index}", str(index))
            return pat, wire_name

    return None, ""


def _prepare_parameters(
        component_type: str,
        parameters: dict,
        operation: str,
        *,
        allow_empty: bool,
) -> tuple[dict | None, dict | None]:
    """Validate and convert public parameters to wire parameters.

    Returns (wire_parameters, error).  One is always None.

    Checks (in order):
      1. parameters must be a dict
      2. Empty check based on operation
      3. Component type exists in catalog
      4. Each parameter name resolves via _resolve_parameter_schema
      5. create_allowed / update_allowed based on operation
      6. Each value must be a dict with only "value" / "unit"
      7. "value" must be present, not null/array/object
      8. Value type validation (number / integer / boolean / enum)
      9. Unit validation (required, allowed values)

    Args:
        component_type: "SParameter", "HarmonicBalance", or "XDB".
        parameters: Public parameter dict.
        operation: "create" or "update".
        allow_empty: If True, empty parameters dict is OK.
    """
    if not isinstance(parameters, dict):
        return None, _param_error("INVALID_PARAMETERS",
                                  "parameters 必须是对象", component_type)

    cat = _load_catalog()
    if not cat:
        return None, _param_error("COMPONENT_SCHEMA_UNAVAILABLE",
                                  "仿真控件参数目录加载失败", component_type)

    comp = _catalog_component(component_type)
    if not comp:
        return None, _param_error("UNSUPPORTED_COMPONENT_TYPE",
                                  f"不支持的控件类型: {component_type}",
                                  component_type,
                                  supported_types=sorted(_COMPONENT_TYPES))

    # Empty check
    if not parameters:
        if allow_empty:
            return {}, None
        return None, _param_error("INVALID_PARAMETERS",
                                  "parameters 必须是非空对象", component_type)

    # Parameter schema
    schemas: dict[str, tuple[dict, str]] = {}
    for pname in parameters:
        schema, wire = _resolve_parameter_schema(component_type, pname)
        if schema is None:
            return None, _param_error("UNSUPPORTED_PARAMETER",
                                      f"{component_type} 不支持参数: {pname}",
                                      component_type, parameter=pname,
                                      supported_parameters=_supported_param_names(component_type))
        schemas[pname] = (schema, wire)

    # Permission check
    for pname, (schema, wire) in schemas.items():
        if operation == "create" and not schema.get("create_allowed", False):
            return None, _param_error("CREATE_PARAMETER_NOT_ALLOWED",
                                      f"{component_type}.{pname} 不允许在创建时设置",
                                      component_type, parameter=pname)
        if operation == "update" and not schema.get("update_allowed", False):
            return None, _param_error("UPDATE_PARAMETER_NOT_ALLOWED",
                                      f"{component_type}.{pname} 不允许更新",
                                      component_type, parameter=pname)

    # Value validation
    wire_params: dict = {}
    for pname, (schema, wire_name) in schemas.items():
        pval = parameters[pname]

        # Must be dict
        if not isinstance(pval, dict):
            return None, _param_error("INVALID_PARAMETER_VALUE",
                                      f"参数 {pname} 的值必须是对象 {{\"value\": ...}}",
                                      component_type, parameter=pname)

        # Only "value" and "unit" allowed
        extra = set(pval.keys()) - {"value", "unit"}
        if extra:
            return None, _param_error("INVALID_PARAMETER_VALUE",
                                      f"参数 {pname} 包含多余字段: {', '.join(sorted(extra))}",
                                      component_type, parameter=pname)

        # "value" required
        if "value" not in pval:
            return None, _param_error("MISSING_VALUE",
                                      f"参数 {pname} 缺少 value",
                                      component_type, parameter=pname)

        raw_value = pval["value"]

        # value must not be null, array, or object
        if raw_value is None:
            return None, _param_error("INVALID_VALUE",
                                      f"参数 {pname} 的 value 不能为 null",
                                      component_type, parameter=pname)
        if isinstance(raw_value, (list, dict)):
            return None, _param_error("INVALID_VALUE",
                                      f"参数 {pname} 的 value 必须是标量",
                                      component_type, parameter=pname)

        # Type validation
        vt = schema.get("value_type", "string")
        if vt in ("number", "integer"):
            try:
                num = float(str(raw_value))
            except (ValueError, TypeError):
                return None, _param_error("INVALID_VALUE",
                                          f"参数 {pname} 的值必须是 {vt}",
                                          component_type, parameter=pname)
            if not math.isfinite(num):
                return None, _param_error("INVALID_VALUE",
                                          f"参数 {pname} 的值不能是 NaN 或 Infinity",
                                          component_type, parameter=pname)
            if vt == "integer" and not num.is_integer():
                return None, _param_error("INVALID_VALUE",
                                          f"参数 {pname} 的值必须是整数",
                                          component_type, parameter=pname)

        # Enum check
        ev = schema.get("enum_values", [])
        if ev and str(raw_value) not in ev:
            return None, _param_error("INVALID_ENUM_VALUE",
                                      f"参数 {pname} 的值必须是 {'/'.join(ev)}",
                                      component_type, parameter=pname,
                                      allowed_values=ev)

        # Unit validation
        unit_required = schema.get("unit_required", False)
        if unit_required and "unit" not in pval:
            return None, _param_error("MISSING_UNIT",
                                      f"参数 {pname} 需要 unit",
                                      component_type, parameter=pname)
        if not unit_required and "unit" in pval:
            return None, _param_error("UNSUPPORTED_UNIT",
                                      f"参数 {pname} 不需要 unit",
                                      component_type, parameter=pname)

        if "unit" in pval:
            unit_val = pval["unit"]
            if not isinstance(unit_val, str) or not unit_val.strip():
                return None, _param_error("UNSUPPORTED_UNIT",
                                          f"参数 {pname} 的 unit 必须是有效字符串",
                                          component_type, parameter=pname)
            allowed = schema.get("units", [])
            if allowed and unit_val not in allowed:
                return None, _param_error("UNSUPPORTED_UNIT",
                                          f"参数 {pname} 不支持单位 {unit_val}",
                                          component_type, parameter=pname,
                                          allowed_units=allowed)

        # Detect duplicate (alias conflict): Freq → Freq[1] + Freq[1] → Freq[1]
        if wire_name in wire_params:
            # Find the conflicting public name
            conflicting = next(
                (n for n, (s, w) in schemas.items()
                 if w == wire_name and n != pname),
                pname,
            )
            return None, _param_error("DUPLICATE_PARAMETER_ALIAS",
                                      f"参数 {pname} 和 {conflicting} 映射到同一个底层参数 {wire_name}",
                                      component_type, parameter=pname,
                                      conflicting_parameter=conflicting,
                                      wire_name=wire_name)

        wire_params[wire_name] = pval

    return wire_params, None


def _supported_param_names(component_type: str) -> list[str]:
    """Build a sorted list of supported parameter names for error messages."""
    comp = _catalog_component(component_type)
    names = sorted(comp.get("parameters", {}).keys())
    # Add abbreviated pattern hints
    for pat in comp.get("parameter_patterns", []):
        pp = pat.get("public_pattern", "")
        idx_min = pat.get("index_min", 1)
        idx_max = pat.get("index_max", 32)
        names.append(pp.replace("{index}", f"{{{idx_min}..{idx_max}}}"))
    return names


# ═══════════════════════════════════════════════════════════
# Wire ↔ public name conversion
# ═══════════════════════════════════════════════════════════

def _to_wire_parameters(component_type: str, parameters: dict) -> dict:
    """Convert public parameter names to wire names."""
    result: dict = {}
    for pname, pval in parameters.items():
        schema, wire_name = _resolve_parameter_schema(component_type, pname)
        if wire_name:
            result[wire_name] = pval
        else:
            result[pname] = pval
    return result


def _from_wire_parameters(component_type: str, parameters: dict) -> dict:
    """Convert wire parameter names back to public names."""
    comp = _catalog_component(component_type)
    fixed = comp.get("parameters", {})

    # Build reverse map: wire_name → public_name
    reverse: dict[str, str] = {}
    for pub_name, defn in fixed.items():
        reverse[defn.get("wire_name", pub_name)] = pub_name

    # Also map dynamic patterns (don't override fixed mappings)
    for pat in comp.get("parameter_patterns", []):
        public_pat = pat.get("public_pattern", "")
        wire_pat = pat.get("wire_pattern", public_pat)
        for wire_key in parameters:
            if wire_key in reverse:
                continue  # already mapped by fixed parameter
            wire_regex = _re.escape(wire_pat).replace(r"\{index\}", r"(\d+)")
            m = _re.fullmatch(wire_regex, wire_key)
            if m:
                reverse[wire_key] = public_pat.replace("{index}", m.group(1))

    result: dict = {}
    for wire_name, pval in parameters.items():
        pub_name = reverse.get(wire_name, wire_name)
        result[pub_name] = pval

    return result


# ═══════════════════════════════════════════════════════════
# 实例查找（供 update / delete 复用）
# ═══════════════════════════════════════════════════════════

def _find_component_by_instance(
        project_path: str,
        instance_name: str,
) -> tuple[dict | None, dict | None]:
    """Locate a component by its instance name in saved project files.

    Returns (component, error).  One is always None.
    """
    target = instance_name.strip()
    if not target:
        return None, error_response("EMPTY_INSTANCE_NAME", "instance_name 不能为空")

    try:
        reader = ProjectReader(project_path)
    except FileNotFoundError:
        return None, error_response("PROJECT_NOT_FOUND", "工程文件不存在")
    except ValueError:
        return None, error_response("INVALID_PROJECT_PATH", "project_path 必须是 .epp 文件")

    matches: list[dict] = []
    for sname in reader.list_schematics() or []:
        raw = reader.read_schematic(sname)
        if not raw:
            continue
        for comp in parse_components(raw):
            if comp.get("name") != target:
                continue
            matches.append({**comp, "schematic": sname})

    if not matches:
        return None, error_response("COMPONENT_NOT_FOUND",
                                f"在已保存工程中未找到器件实例 {target}",
                                hint="请确认 EDI 中已保存工程，或使用 list_simulation_components 查看当前器件")

    if len(matches) > 1:
        return None, error_response("AMBIGUOUS_INSTANCE_NAME",
                                f"发现多个名为 {target} 的器件",
                                details={
                                    "matches": [
                                        {"schematic": m["schematic"],
                                         "component_type": m.get("type", ""),
                                         "component_id": m.get("component_id", "")}
                                        for m in matches
                                    ]
                                }
                                )

    return matches[0], None


# ═══════════════════════════════════════════════════════════
# 参数格式化（供 list_simulation_components）
# ═══════════════════════════════════════════════════════════

def _format_component_parameters(
        component_type: str,
        paramsinfo: dict,
) -> dict:
    """Build public-facing parameter dict from raw paramsinfo.

    paramsinfo is the already-parsed dict from parse_paramsinfo().
    Does NOT call parse_paramsinfo again.

    保留完整元数据字段（value/unit/default_unit/tunable/visible/initial/max/min/status），
    unit 无值时省略该键。
    """
    parameters: dict = {}
    for wire_name, metadata in paramsinfo.items():
        if not isinstance(metadata, dict):
            continue

        entry: dict = {
            "value": metadata.get("value", ""),
            "default_unit": metadata.get("default_unit", ""),
            "tunable": metadata.get("tunable", False),
            "visible": metadata.get("visible", True),
            "initial": metadata.get("initial", ""),
            "max": metadata.get("max", ""),
            "min": metadata.get("min", ""),
            "status": metadata.get("status", ""),
        }
        if metadata.get("unit"):
            entry["unit"] = metadata["unit"]

        parameters[wire_name] = entry

    return _from_wire_parameters(component_type, parameters)


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _param_error(
        code: str,
        message: str,
        component_type: str = "",
        parameter: str = "",
        **extra,
) -> dict:
    """构建器件参数校验错误响应（等价于 error_response + 参数 details）。"""
    kwargs = dict(extra)
    if component_type:
        kwargs["component_type"] = component_type
    if parameter:
        kwargs["parameter"] = parameter
    return error_response(code, message, **kwargs)


def _find_sim_components(project_path: str, component_type: str = "", schematic_name: str = "",
                         include_hidden: bool = False, reader: ProjectReader | None = None) -> list[dict]:
    """Local lookup of all components from saved project files.

    schematic_name 为空时遍历所有原理图，指定时只读取该原理图。
    include_hidden=False 时过滤 visible=false 的隐藏参数。
    reader 为 None 时内部创建；否则复用（避免调用方二次实例化）。
    """
    if reader is None:
        reader = ProjectReader(project_path)
    schematics = reader.list_schematics() or []
    if schematic_name:
        schematics = [s for s in schematics if s == schematic_name]
    results: list[dict] = []
    for sname in schematics:
        raw = reader.read_schematic(sname)
        if not raw:
            continue
        for comp in parse_components(raw):
            ct = comp.get("type", "")
            if component_type and ct != component_type:
                continue
            params = comp.get("paramsinfo", {})
            # 默认过滤 visible=false 的隐藏参数，include_hidden=True 时放开
            if not include_hidden:
                params = {
                    k: v for k, v in params.items()
                    if not isinstance(v, dict) or v.get("visible", True)
                }
            # SP/HB/XDB: format with wire→public name mapping
            # Other types (Var, Sweep, P_nToneG, etc.): raw paramsinfo
            if ct in _COMPONENT_TYPES:
                formatted = _format_component_parameters(ct, params)
            else:
                formatted = params
            results.append({
                "component_type": ct,
                "instance_name": comp.get("name", ""),
                "component_id": comp.get("component_id", ""),
                "model_id": comp.get("model_id", ""),
                "pin_count": comp.get("pin_count", 0),
                "parameter_count": comp.get("parameter_count", 0),
                "schematic": sname,
                "parameters": formatted,
            })
    return results


# ═══════════════════════════════════════════════════════════
# 1. get_simulation_component_schema
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def get_simulation_component_schema(
        component_type: str,
        parameter_name: str = "",
) -> dict[str, Any]:
    """查询已建模器件类型的参数 Schema 和权限。

    仅支持 SParameter / HarmonicBalance / XDB 的参数目录。
    其他类型可通过 list_simulation_components 读取但无本地 Schema。

    Args:
        component_type: "SParameter" / "HarmonicBalance" / "XDB"。
        parameter_name: 指定参数名时只返回该参数，为空返回全部。
    """
    catalog = _load_catalog()
    if not catalog:
        return error_response("COMPONENT_SCHEMA_UNAVAILABLE", "参数目录加载失败")

    comp = _catalog_component(component_type)
    if not comp:
        return error_response("UNSUPPORTED_COMPONENT_TYPE", f"不支持的控件类型: {component_type}",
                          supported_component_types=sorted(_COMPONENT_TYPES))

    params = comp.get("parameters", {})
    if parameter_name:
        schema, _ = _resolve_parameter_schema(component_type, parameter_name)
        if schema is None:
            return error_response("UNSUPPORTED_PARAMETER", f"{component_type} 不支持参数 {parameter_name}",
                              supported_parameters=_supported_param_names(component_type))
        params = {parameter_name: schema}

    return {
        "success": True,
        "component_type": component_type,
        "schema_version": catalog.get("schema_version", ""),
        "protocol_version": catalog.get("protocol_version", ""),
        "parameters": params,
        "parameter_patterns": comp.get("parameter_patterns", []),
        "example": comp.get("example", {}),
    }


# ═══════════════════════════════════════════════════════════
# 2. list_simulation_components
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def list_simulation_components(
        project_path: str,
        component_type: str = "",
        name_contains: str = "",
        schematic_name: str = "",
        offset: int = 0,
        limit: int = 100,
        include_hidden: bool = False,
        summary_only: bool = False,
) -> dict[str, Any]:
    """列出已保存工程中的全部器件及参数（含 wire→public 参数名映射）。

    可列出所有器件（SP/HB/XDB/Var/Sweep/P_nToneG/TermG 等）。
    已知类型做 wire→public 映射；其他类型返回原始 paramsinfo。
    读取磁盘文件，EDI 未保存的修改不会反映到结果中。
    字段包含：component_type/instance_name/component_id/model_id/pin_count/parameter_count/schematic/parameters。

    本工具同时覆盖原 list_project_components 的能力：支持按原理图过滤和分页。
    修改器件后建议先保存工程再调用本工具确认。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_type: 按类型过滤（可选）。
        name_contains: 按实例名模糊匹配（可选）。
        schematic_name: 只读指定原理图（可选，默认遍历全部原理图）。
        offset: 分页偏移（默认 0）。
        limit: 每页数量（默认 100，最大 500）。
        include_hidden: 是否包含 Visible=false 的隐藏参数（默认 False）。
        summary_only: 为 True 时每个器件只保留 component_type/instance_name/model_id/pin_count，
            裁掉 parameters 等大字段（用于选型/清单等只需器件标识的场景）；需要完整参数时不传此参数或用
            get_schematic_component_info 单点查询。
    """
    try:
        reader = ProjectReader(project_path)
    except FileNotFoundError:
        return error_response("PROJECT_NOT_FOUND", "工程文件不存在")
    except ValueError:
        return error_response("INVALID_PROJECT_PATH", "project_path 必须是 .epp 文件")
    components = _find_sim_components(project_path, component_type, schematic_name=schematic_name,
                                      include_hidden=include_hidden, reader=reader)
    if name_contains:
        components = [c for c in components
                      if name_contains.lower() in c["instance_name"].lower()]

    type_counts: dict[str, int] = {}
    for c in components:
        type_counts[c["component_type"]] = type_counts.get(c["component_type"], 0) + 1

    total = len(components)
    offset = max(0, offset)
    limit = max(1, min(limit, 500))
    paged = components[offset:offset + limit]

    if summary_only:
        paged = [{
            "component_type": c["component_type"],
            "instance_name": c["instance_name"],
            "model_id": c.get("model_id", ""),
            "pin_count": c.get("pin_count", 0),
        } for c in paged]

    return {
        "success": True,
        "project_path": str(reader.epp_path.resolve()),
        "total": total,
        "count": total,
        "offset": offset,
        "limit": limit,
        "component_type_counts": type_counts,
        "components": paged,
    }


# ═══════════════════════════════════════════════════════════
# 3. create_simulation_component
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def create_simulation_component(
        project_path: str,
        component_type: str,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """使用 EDI 器件工厂默认参数创建器件。

    不传自定义参数，创建后根据返回的 instance_name 调用 update 设参。
    component_type 是否支持由 EDI 服务决定，MCP 不做类型限制。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_type: EDI 器件工厂类型名。
        timeout_seconds: 最长等待秒数。
    """
    ct, err = require_nonempty(component_type, label="component_type")
    if err:
        return err
    return call_project_grpc(ecserver_pb2.CREATE_SIMULATION_COMPONENT, project_path, timeout_seconds, component_type=ct)


# ═══════════════════════════════════════════════════════════
# 4. update_simulation_component
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def update_simulation_component(
        project_path: str,
        instance_name: str,
        parameters: dict,
        component_type: str = "",
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按实例名更新器件参数。

    SParameter/HarmonicBalance/XDB：执行 MCP 参数校验和 wire 转换。
    其他器件类型：基本格式检查后透传给 EDI 校验。
    建议先调用 list_simulation_components 确认实例名和类型。

    示例：
      {"project_path": "...", "instance_name": "HB1",
       "component_type": "HarmonicBalance",
       "parameters": {"Freq": {"value": "2", "unit": "GHz"}, "Order": {"value": "7"}}}

    Args:
        project_path: .epp 工程文件绝对路径。
        instance_name: 器件实例名（如 "HB1"、"SP2"）。
        parameters: 要更新的参数（非空对象）。
        component_type: 器件类型（可选），用于参数校验。
        timeout_seconds: 最长等待秒数。
    """
    resolved = validate_project_path(project_path)

    if not isinstance(parameters, dict) or not parameters:
        return error_response("INVALID_PARAMETERS", "parameters 必须是非空对象")

    # Three-way type inference: explicit > disk > error
    explicit_type = component_type.strip() if component_type else ""
    component, find_err = _find_component_by_instance(resolved, instance_name)
    # 空实例名 / 同名歧义必须立即报错，不能静默吞掉（COMPONENT_NOT_FOUND 可忽略，以支持显式 type + 未保存新器件）
    if find_err and find_err.get("error_code") in ("EMPTY_INSTANCE_NAME", "AMBIGUOUS_INSTANCE_NAME"):
        return find_err
    actual_type = component.get("type", "") if component else ""

    if actual_type:
        # Instance found on disk — validate consistency with explicit type
        if explicit_type and explicit_type != actual_type:
            return error_response("COMPONENT_TYPE_MISMATCH",
                              f"{instance_name} 的实际类型为 {actual_type}，但请求提供了 {explicit_type}")
        ct = actual_type

    elif explicit_type:
        # Not on disk but explicit type given — e.g. newly created, not yet saved
        ct = explicit_type

    else:
        return error_response("COMPONENT_TYPE_REQUIRED",
                          f"无法从已保存工程识别 {instance_name} 的类型；请先保存工程，或显式提供 component_type")

    # Catalog types (SP/HB/XDB): do wire-conversion via _prepare_parameters
    # Other types (Sweep, P_nToneG, Var, etc.): send as-is, EDI validates
    if ct in _COMPONENT_TYPES:
        wire_params, prepare_error = _prepare_parameters(
            ct, parameters, operation="update", allow_empty=False,
        )
        if prepare_error:
            return prepare_error
    else:
        # Basic validation — EDI handles business rules for non-catalog types
        for k, v in parameters.items():
            if not isinstance(v, dict) or "value" not in v:
                return error_response("INVALID_PARAMETERS", f"参数 {k} 必须是 {{\"value\": ...}} 格式")
        wire_params = parameters

    return call_grpc(
        ecserver_pb2.UPDATE_SIMULATION_COMPONENT,
        {"project_path": resolved,
         "instance_name": instance_name.strip(),
         "parameters": wire_params},
        timeout_seconds,
        max_timeout_seconds=300,
    )


# ═══════════════════════════════════════════════════════════
# 5. replace_port_component
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def replace_port_component(
        project_path: str,
        target_instance_name: str,
        replacement_component_type: str,
        parameters: dict | None = None,
        timeout_seconds: int = 300,
) -> dict[str, Any]:
    """将原理图中的端口器件替换为另一种类型。

    目前仅支持 TermG ↔ P_nToneG 之间的替换。
    服务端保留位置、状态和外部连线，生成新实例名。

    Args:
        project_path: .epp 工程文件绝对路径。
        target_instance_name: 要替换的端口实例名（如 "TermG1"）。
        replacement_component_type: 目标器件类型（TermG / P_nToneG）。
        parameters: 可选参数字典。
        timeout_seconds: 最长等待秒数（默认 300）。
    """
    resolved = validate_project_path(project_path)

    if not target_instance_name.strip():
        return error_response("EMPTY_INSTANCE_NAME", "target_instance_name 不能为空")

    rct = replacement_component_type.strip()
    if rct not in ("TermG", "P_nToneG"):
        return error_response("UNSUPPORTED_COMPONENT_TYPE", "replacement_component_type 仅支持 TermG / P_nToneG")

    params = {} if parameters is None else parameters
    if not isinstance(params, dict):
        return error_response("INVALID_PARAMETERS", "parameters 必须是对象")

    return call_grpc(
        ecserver_pb2.REPLACE_PORT_COMPONENT,
        {"project_path": resolved,
         "target_instance_name": target_instance_name.strip(),
         "replacement_component_type": rct,
         "parameters": params},
        timeout_seconds,
        max_timeout_seconds=600,
    )


# ═══════════════════════════════════════════════════════════
# 6. delete_simulation_component
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def delete_simulation_component(
        project_path: str,
        instance_name: str,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按实例名删除原理图器件及其连接线。

    这是通用删除工具，不限于仿真器件。删除直接由 EDI 按实例名执行，
    不做本地磁盘预检查，因此即使 EDI 中有未保存的更改也能正常工作。

    建议调用前先用 list_simulation_components 确认目标实例名。

    Args:
        project_path: .epp 工程文件绝对路径。
        instance_name: 要删除的器件实例名（如 "R1"、"SP2"）。
        timeout_seconds: 最长等待秒数。
    """
    instance_name, err = require_nonempty(instance_name, error_code="EMPTY_INSTANCE_NAME",
                                          label="instance_name")
    if err:
        return err
    return call_project_grpc(ecserver_pb2.DELETE_SIMULATION_COMPONENT, project_path,
                             timeout_seconds, instance_name=instance_name)


# ═══════════════════════════════════════════════════════════
# 6. set_component_active_state
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def set_component_active_state(
        project_path: str,
        instance_name: str,
        state: str,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """确定性设置器件状态为 NORMAL、DISABLED 或 SHORTED。

    这不是状态切换，而是直接设置目标状态，因此重复调用具有幂等性：
    DISABLED → 再设置 DISABLED → 仍然是 DISABLED。

    Args:
        project_path: .epp 工程文件绝对路径。
        instance_name: 器件实例名。
        state: 目标状态，只接受 "NORMAL" / "DISABLED" / "SHORTED"（大小写不敏感）。
        timeout_seconds: 最长等待秒数。
    """
    normalized = state.strip().upper()
    if normalized not in _ACTIVE_STATES:
        return error_response("INVALID_ACTIVE_STATE", f"无效状态: {state}，仅支持 NORMAL / DISABLED / SHORTED",
                          allowed_states=sorted(_ACTIVE_STATES))

    instance_name, err = require_nonempty(instance_name, error_code="EMPTY_INSTANCE_NAME",
                                          label="instance_name")
    if err:
        return err
    return call_project_grpc(ecserver_pb2.SET_COMPONENT_ACTIVE_STATE, project_path,
                             timeout_seconds, instance_name=instance_name, state=normalized)


# ═══════════════════════════════════════════════════════════
# 7. generate_schematic_from_netlist
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def generate_schematic_from_netlist(
        project_path: str,
        netlist_path: str,
        clear_before_import: bool = False,
        confirm_clear: bool = False,
        timeout_seconds: int = 300,
) -> dict[str, Any]:
    """将网表文件导入工程生成 main 原理图。

    默认追加到现有原理图。清空模式需要双重确认。

    Args:
        project_path: .epp 工程文件绝对路径。
        netlist_path: 网表文件路径（必须已存在）。
        clear_before_import: 是否在导入前清空 main 原理图（默认 False）。
        confirm_clear: 确认清空操作。clear_before_import=true 时必须同时为 true。
        timeout_seconds: 最长等待秒数，默认 300。
    """
    resolved = validate_project_path(project_path)

    netlist = Path(netlist_path).expanduser().resolve()
    if not netlist.is_file():
        return error_response("FILE_NOT_FOUND", f"网表文件不存在: {netlist}")

    # Clear-before-import safety gate
    if clear_before_import and not confirm_clear:
        # Count existing components in main schematic only (same as what gRPC clears)
        existing_count = 0
        try:
            reader = ProjectReader(project_path)
            raw = reader.read_schematic("main")
            if raw:
                existing_count = len(parse_components(raw))
        except Exception:
            pass

        return error_response("CLEAR_CONFIRMATION_REQUIRED",
                          "clear_before_import=true 会清空 main 原理图的全部器件；确认后请同时传 confirm_clear=true",
                          existing_component_count=existing_count,
                          warning="本操作将清空 main 原理图")

    return call_grpc(
        ecserver_pb2.GENERATE_SCHEMATIC_FROM_NETLIST,
        {"project_path": resolved,
         "netlist_path": str(netlist),
         "clear_before_import": clear_before_import},
        timeout_seconds,
        max_timeout_seconds=600,
    )


# ═══════════════════════════════════════════════════════════
# 8.5 replace_schematic_from_file
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def replace_schematic_from_file(
        project_path: str,
        schematic_path: str,
        timeout_seconds: int = 300,
) -> dict[str, Any]:
    """从 .ep 文件整体替换原理图（对应 gRPC LOAD_SCHEMATIC_FROM_FILE）。

    用指定的 schematic.ep 文件（完整 edi-schematic S-Expression）整体替换工程唯一原理图，
    不追加、不合并。成功后立即保存，失败恢复原原理图状态。

    ⚠️ 此操作会整体替换当前原理图，原内容丢失，请谨慎使用。

    Args:
        project_path: .epp 工程文件绝对路径。
        schematic_path: .ep 原理图文件路径（本地文件、必须已存在、内容为完整 S-Expression 且不能为空；
                        建议传绝对路径，Windows 下路径用 / 或 \\ 分隔）。
        timeout_seconds: 最长等待秒数，默认 300。
    """
    resolved = validate_project_path(project_path)

    # 必填校验：不能为空
    if not schematic_path or not schematic_path.strip():
        return error_response("INVALID_PATH", "schematic_path 不能为空")

    sch_path = Path(schematic_path).expanduser().resolve()

    # 仅允许本地文件，拒绝网络路径（UNC）
    if is_network_path(sch_path):
        return error_response("INVALID_PATH", f"禁止访问网络路径: {sch_path}")

    if not sch_path.is_file():
        return error_response("FILE_NOT_FOUND", f"原理图文件不存在: {sch_path}")
    if sch_path.suffix.lower() != ".ep":
        return error_response("INVALID_PATH", "schematic_path 必须是 .ep 文件")

    return call_grpc(
        ecserver_pb2.LOAD_SCHEMATIC_FROM_FILE,
        {"project_path": resolved,
         "schematic_path": str(sch_path)},
        timeout_seconds,
        max_timeout_seconds=600,
    )


# ═══════════════════════════════════════════════════════════
# 8. attach_out_component
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def attach_out_component(
        project_path: str,
        target_instance_name: str,
        pin_index: int | None = None,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """为目标器件引脚挂载一个 Out 器件，并自动连线。

    Out 器件使用默认参数创建，实例名按 Out1/Out2/... 自动分配。
    服务端自动判断引脚朝向、计算放置位置、检测重叠并旋转对齐。

    Args:
        project_path: .epp 工程文件绝对路径。
        target_instance_name: 要挂载 Out 的目标器件实例名（如 "U1"）。
        pin_index: 0 开始的目标引脚编号。单引脚器件可省略。
        timeout_seconds: 最长等待秒数（默认 120）。
    """
    target_instance_name, err = require_nonempty(target_instance_name,
                                                 error_code="EMPTY_INSTANCE_NAME",
                                                 label="target_instance_name")
    if err:
        return err

    if pin_index is not None and (isinstance(pin_index, bool) or not isinstance(pin_index, int) or pin_index < 0):
        return error_response("INVALID_PARAMETERS", "pin_index 必须是非负整数")

    extras = {"target_instance_name": target_instance_name}
    if pin_index is not None:
        extras["pin_index"] = pin_index
    return call_project_grpc(ecserver_pb2.ATTACH_OUT_COMPONENT, project_path,
                             timeout_seconds, **extras)
