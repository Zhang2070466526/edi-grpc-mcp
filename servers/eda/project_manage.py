r"""EDA 工程管理工具。

list_epp_projects             扫描文件夹中的所有 .epp 工程文件
create_project                创建新的 .epp 工程（不显示创建向导）
open_edi_project              打开 .epp 工程，等待返回成功或失败
close_edi_project             关闭已打开的工程，可选择是否保存
get_project_summary           工程概览（元数据、原理图路径、元件统计、仿真器件配置、最近结果路径）
analyze_variables             分析变量定义、引用和 Sweep 配置
list_schematic_components     查询原理图全部器件（gRPC，含完整参数与几何）
get_schematic_component_info  按实例名查器件参数与几何（gRPC）
get_components_static_params  查询器件固有参数（重量/尺寸/封装/厂商/成本）
batch_query_component_rf_params  按器件型号列表批量查询模型信息

自然语言使用示例：
  帮我看看 C:/EDI-Workspace 下面有哪些 .epp 工程
  帮我打开 EDA 工程 C:/.../EDI_TEST.epp
  帮我关闭这个工程
  帮我看看这个工程有哪些元件
  帮我查看 TermG1 元件的参数
  帮我获取这个工程的概览信息

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  folder_path      要扫描的文件夹绝对路径（list_epp_projects）
  timeout_seconds  最长等待秒数，默认 60 秒
  need_save        关闭前是否保存工程（close_edi_project），默认 False
"""

from __future__ import annotations

import re
from itertools import islice
from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc, call_project_grpc
from servers.eda.config import SIM_COMPONENT_TYPES, require_project_path
from servers.eda.project_reader import ProjectReader, parse_components
from servers.utils import require_nonempty, error_response
from servers import mcp

# 器件级 active_state → state 名称（EDI 约定：0=NORMAL / 1=DISABLED / 2=SHORTED）
_ACTIVE_STATE_NAMES = {0: "NORMAL", 1: "DISABLED", 2: "SHORTED"}


def _state_name(active_state: int | None) -> str:
    """把器件级 active_state 数值翻成 EDI 的状态名，未知值或缺失返回 UNKNOWN。"""
    if active_state is None:
        return "UNKNOWN"
    return _ACTIVE_STATE_NAMES.get(active_state, "UNKNOWN")


def _find_latest_result(workspace: Path) -> dict[str, Any]:
    """找工程目录下最近的仿真结果 .raw。

    取用顺序：先只看 history/*.raw（EDI 的仿真产物目录）；history 里没有才回退到
    整个工程目录，但排除 model/ 下的模型自带文件（如 ICE 的 .result.raw）与隐藏文件，
    避免把模型文件当成仿真结果。文件大小为 0 时 exists=false（多半是失败的仿真）。
    """
    candidates: list[Path] = []
    history = workspace / "history"
    if history.is_dir():
        try:
            candidates = [p for p in history.glob("*.raw") if p.is_file()]
        except OSError:
            candidates = []
    if not candidates:
        try:
            for p in workspace.rglob("*.raw"):
                if not p.is_file() or p.name.startswith("."):
                    continue
                if any(part.lower() == "model" for part in p.parts):
                    continue
                candidates.append(p)
        except OSError:
            candidates = []

    sized: list[tuple[float, Path, int]] = []
    for p in candidates:
        try:
            st = p.stat()
        except OSError:
            continue
        sized.append((st.st_mtime, p, st.st_size))

    if not sized:
        return {"path": "", "exists": False, "size": 0}

    _, latest, size = max(sized, key=lambda item: item[0])
    try:
        path = latest.resolve().as_posix()
    except OSError:
        path = latest.as_posix()
    return {"path": path, "exists": size > 0, "size": size}


@mcp.tool()
def list_epp_projects(folder_path: str) -> dict[str, Any]:
    """扫描指定文件夹中的 .epp 工程文件，该接口用于扫描工作区外的工程文件，工作区内工程可通过GET_CURRENT_WORKSPACE_SUMMARY获取。

    用法："帮我看看 C:/Projects 下有哪些工程"、"扫描 EDI-Workspace 目录"

    Args:
        folder_path: 要扫描的文件夹绝对路径。

    Returns:
        {"success": True, "folder": "C:/...", "count": 3,
         "projects": [{"name": "demo1", "path": "C:/.../demo1.epp", "size": 0}, ...]}
    """
    if not folder_path or not folder_path.strip():
        return error_response("INVALID_PARAMETERS", "folder_path 不能为空，请提供要扫描的目录")
    root = Path(folder_path).expanduser()
    if not root.is_dir():
        return error_response("FILE_NOT_FOUND", f"文件夹不存在: {folder_path}")

    _MAX_PROJECTS = 1000
    try:
        epp_paths = sorted(islice(root.rglob("*.epp"), _MAX_PROJECTS + 1))
    except PermissionError as exc:
        return error_response("PERMISSION_DENIED", f"扫描目录失败（权限不足）: {exc}")
    truncated = len(epp_paths) > _MAX_PROJECTS
    epp_paths = epp_paths[:_MAX_PROJECTS]
    projects = []
    skipped = 0
    for epp in epp_paths:
        try:
            path = epp.resolve().as_posix()
            size = epp.stat().st_size
        except OSError:
            skipped += 1
            continue
        projects.append({"name": epp.stem, "path": path, "size": size})

    result: dict[str, Any] = {
        "success": True,
        "folder": root.resolve().as_posix(),
        "count": len(projects),
        "projects": projects,
    }
    if skipped:
        result["skipped"] = skipped
    if truncated:
        result["truncated"] = True
        result["message"] = (
            f"检测到超过 {_MAX_PROJECTS} 个工程，已截断到前 {_MAX_PROJECTS} 个，"
            "可缩小扫描范围获取全部"
        )
    return result


@mcp.tool()
def create_project(
        name: str,
        author: str = "",
        path: str = "",
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """创建新的 EDI 工程（不显示创建向导、不自动打开）。建完用 open_edi_project 打开。

    用法："创建一个名为 demo 的工程"、"在 D:/projects 下创建 test 工程"

    重要约束：工程名必须由用户明确提供，缺失时先询问用户，不得自行编造。

    工程文件路径为 <path>/<name>/<name>.epp；目标已存在或名称不符合 Windows 命名
    规则时创建失败、不覆盖已有内容。

    Args:
        name: 工程名（必填，合法的 Windows 文件名）。
        author: 作者（可选，空则默认 bm）。
        path: 工程父目录（可选，空则用工作区 projects 目录）。
        timeout_seconds: 最长等待时间，默认 60。

    Returns:
        gRPC 统一返回结构，成功时 details 含 project_path。
    """
    if not name or not name.strip():
        return error_response("INVALID_PARAMETERS", "工程名不能为空")
    name = name.strip()

    # Windows 文件名非法字符（服务端还会做保留名等更完整校验）
    if any(c in name for c in '<>:"/\\|?*'):
        return error_response("INVALID_PARAMETERS", f"工程名包含非法字符: {name}")

    payload: dict[str, Any] = {"name": name}
    if author.strip():
        payload["author"] = author.strip()
    if path.strip():
        payload["path"] = path.strip()

    return call_grpc(
        ecserver_pb2.CREATE_PROJECT,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def open_edi_project(
        project_path: str,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """打开 .epp 工程（会切换 EDI 当前工程；同目录需有 schematics/main/schematic.ep）。关闭用 close_edi_project。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    return call_project_grpc(ecserver_pb2.OPEN_PROJECT, project_path, timeout_seconds)


@mcp.tool()
def close_edi_project(
        project_path: str,
        need_save: bool = False,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """⚠️ 关闭当前 .epp 工程（未保存改动会丢）。打开用 open_edi_project；只是想看状态用 get_project_summary。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        need_save: 关闭前是否保存工程，默认 False。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    return call_project_grpc(ecserver_pb2.CLOSE_PROJECT, project_path, timeout_seconds,
                             need_save=need_save)


@mcp.tool()
def get_project_summary(
        project_path: str,
        include_component_types: bool = True,
        include_latest_result: bool = True,
) -> dict[str, Any]:
    """获取 .epp 工程概览：元数据、原理图文件路径、元件统计、仿真器件配置、最近结果文件路径。注意：该接口只判断有无仿真结果，禁止解析raw文件。

    用法："帮我看看这个工程的概览"、"查看工程信息"

    字段说明：
        project.name 取自工程内嵌元数据（project/metadata.ep），可能与磁盘目录名不同，
            拼路径/定位目录请用 project.project_dir_name。
        latest_result：未查询时为 null；查询了但工程里没有 .raw 时为
            {"path": "", "exists": false, "size": 0}；文件大小为 0 时 exists=false 并附 warning。
            只认 history/*.raw 优先，其次工程内其他 .raw，但排除 model/ 目录（模型自带文件）与隐藏文件。
        simulation[].parameters 为结构化参数 {"参数名": {"value": ..., "unit": ...}}。
        include_component_types 只控制 components.by_type 的统计，无论 True/False 都要解析原理图；
            False 时 by_type 为 null（不是空对象，避免与"没有器件"混淆），器件总数看 total。
        warnings 为结构化列表，每项 {"code": ..., "message": ..., "path"?: ...}，
            code 取值：METADATA_MISSING（元数据缺失）、NO_SCHEMATIC（未找到原理图）、
            SCHEMATIC_MISSING（某个原理图文件缺失或为空，其器件未统计，带 path）、
            RESULT_EMPTY（最近结果文件 0 字节，带 path）。success 为 True 时也可能带 warning，需转告用户。

    Args:
        project_path: .epp 工程文件绝对路径。
        include_component_types: 是否统计元件类型分布（components.by_type）。
        include_latest_result: 是否包含最近仿真结果信息（False 时 latest_result 为 null）。

    Returns:
        {"success": True,
         "project": {"project_id": "...", "name": "NC35352ME-812",
                     "project_dir_name": "NC35352ME-812", "author": "bm", ...},
         "schematics": {"count": 1, "paths": ["D:/.../schematics/main/schematic.ep"]},
         "components": {"total": 55, "by_type": {"HGC367": 16}},
         "simulation": [{"component_type": "SParameter", "instance_name": "SP1",
                         "parameters": {"Start": {"value": "8", "unit": "GHz"}}}],
         "latest_result": {"path": "D:/.../result.raw", "exists": True, "size": 2048},
         "warnings": []}
    """
    resolved, err = require_project_path(project_path)
    if err:
        return err
    reader = ProjectReader(resolved)

    warnings: list[dict[str, Any]] = []
    # metadata 固定键，缺文件时给空串（避免下游取值 KeyError），并明确告警；
    # 注意：read_metadata() 若新增字段，需在此同步显式透传，否则不会自动带出。
    raw_meta = reader.read_metadata()
    metadata: dict[str, Any] = {
        "project_id": raw_meta.get("project_id", ""),
        "name": raw_meta.get("name", ""),
        "author": raw_meta.get("author", ""),
        "version": raw_meta.get("version", ""),
        "created": raw_meta.get("created", ""),
        # name 取自工程内嵌文件，与磁盘目录名可能不同（如目录 Dual Conversion Link HB / name 20260117）
        "project_dir_name": reader.workspace.name,
    }
    if not raw_meta:
        warnings.append({
            "code": "METADATA_MISSING",
            "message": "未找到工程元数据（project/metadata.ep 缺失或无法解析），project.name 等字段为空串",
        })

    entries = reader.list_schematic_entries()
    schematics_info = {"count": len(entries), "paths": [e["path"] for e in entries]}

    # by_type 用 None 表示"未统计"，与 {}（工程里没有器件）区分开
    components_info: dict[str, Any] = {
        "total": 0,
        "by_type": {} if include_component_types else None,
    }
    simulation_info: list[dict] = []
    # 单遍遍历：每个原理图只读+解析一次，同时统计组件与仿真配置
    for entry in entries:
        raw = reader.read_schematic(entry["name"])
        if not raw:
            # schematics.ep 里登记了但文件缺失/为空：不能静默跳过，否则 count=1 而 total=0
            warnings.append({
                "code": "SCHEMATIC_MISSING",
                "message": "原理图文件缺失或为空，其器件未统计",
                "path": entry["path"],
            })
            continue
        comps = parse_components(raw)
        components_info["total"] += len(comps)
        for comp in comps:
            ct = comp.get("type", "")
            if include_component_types:
                components_info["by_type"][ct] = components_info["by_type"].get(ct, 0) + 1
            if ct in SIM_COMPONENT_TYPES:
                pi = comp.get("paramsinfo", {})
                sim_params: dict[str, dict[str, str]] = {}
                for key, info in pi.items():
                    if key == "BasicParameters" or not isinstance(info, dict):
                        continue
                    sim_params[key] = {
                        "value": str(info.get("value") or ""),
                        "unit": str(info.get("unit") or ""),
                    }
                simulation_info.append({
                    "component_type": ct,
                    "instance_name": comp.get("name", ""),
                    "parameters": sim_params,
                })

    latest_result: dict[str, Any] | None = None
    if include_latest_result:
        latest_result = _find_latest_result(reader.workspace)
        if latest_result["path"] and latest_result["size"] == 0:
            warnings.append({
                "code": "RESULT_EMPTY",
                "message": "最近结果文件大小为 0 字节（可能是失败的仿真），已标记 exists=false",
                "path": latest_result["path"],
            })

    if not entries:
        warnings.append({"code": "NO_SCHEMATIC", "message": "未找到原理图"})

    return {
        "success": True,
        "project": metadata,
        "schematics": schematics_info,
        "components": components_info,
        "simulation": simulation_info,
        "latest_result": latest_result,
        "warnings": warnings,
    }


@mcp.tool()
def analyze_variables(project_path: str) -> dict[str, Any]:
    """分析工程中的变量定义、引用与 Sweep 扫描配置。改完变量想确认谁在引用、扫参配了啥时用。

    识别 Var 元件定义的变量、其他元件中对这些变量的引用、
    以及 Sweep 的扫描配置。
    引用判定为「变量名作为标识符出现在任意参数表达式中」（如 P[1]=polar(dbmtow(Pwr_IN),0)、
    Bits=itob(int(X1))），不限于 Freq[1] 这类字段；引号内字面量（如 Bits="000000"）不计为引用。
    变量的 status 字段无意义（恒为 Disable），生效与否看器件级 active_state/state。

    Args:
        project_path: .epp 工程文件绝对路径。

    Returns:
        {"success": True, "project_path": "...",
         "variables": [{"name": "Var1", "parameter": "Pwr_IN", "initial": "-30",
                        "min": "", "max": "", "tunable": False,
                        "active_state": 0, "state": "NORMAL"}],
         "references": [{"variable": "Pwr_IN", "component": "PORT1",
                         "component_type": "P_nToneG", "parameter": "P[1]"}],
         "sweeps": [{"sweep": "Sweep1", "variable": "Freq", "start": "8", "stop": "12",
                     "step": "0.1", "targets": ["SP1"]}]}
    """
    resolved, err = require_project_path(project_path)
    if err:
        return err
    reader = ProjectReader(resolved)
    variables: list[dict] = []
    references: list[dict] = []
    sweeps: list[dict] = []
    all_comps: dict[str, list[dict]] = {}

    # 第一遍：收集所有原理图的 Var 定义、Sweep 配置，并缓存组件
    for sname in reader.list_schematics() or []:
        raw = reader.read_schematic(sname)
        if not raw:
            continue
        comps = parse_components(raw)
        all_comps[sname] = comps
        for comp in comps:
            ct = comp.get("type", "")
            params = comp.get("paramsinfo", {})

            # Var 元件：变量定义
            if ct == "Var":
                for pkey, pinfo in params.items():
                    if pinfo.get("initial"):
                        variables.append({
                            "name": comp.get("name", ""),
                            "parameter": pkey,
                            "initial": pinfo["initial"],
                            "min": pinfo.get("min", ""),
                            "max": pinfo.get("max", ""),
                            "tunable": pinfo.get("tunable", False),
                            "active_state": comp.get("active_state"),
                            "state": _state_name(comp.get("active_state")),
                        })

            # Sweep 元件：扫描配置
            if ct == "Sweep":
                sweep_var = params.get("SweepVar", {}).get("value", "")
                # 动态收集所有 SimInstanceName[i]（避免硬编码上界漏掉更多目标实例）
                sweep_indices = sorted(
                    int(k[len("SimInstanceName["):-1])
                    for k in params
                    if k.startswith("SimInstanceName[") and k.endswith("]")
                    and k[len("SimInstanceName["):-1].isdigit()
                )
                targets = [
                    params[f"SimInstanceName[{i}]"].get("value", "")
                    for i in sweep_indices
                    if params[f"SimInstanceName[{i}]"].get("value")
                ]
                sweeps.append({
                    "sweep": comp.get("name", ""),
                    "variable": sweep_var,
                    "start": params.get("Start", {}).get("value", ""),
                    "stop": params.get("Stop", {}).get("value", ""),
                    "step": params.get("Step", {}).get("value", ""),
                    "targets": targets,
                })

    # 第二遍：在所有变量收集完后，跨原理图查找引用（避免后定义变量匹配不到前文引用）
    # 判定方式：变量名作为标识符出现在参数表达式里 —— P[1]=polar(dbmtow(Pwr_IN),0)、
    # Bits=itob(int(X1)) 这类字段同样算引用；先剥掉引号内字面量（Bits="000000"）避免误判。
    var_names = {
        value
        for v in variables
        for value in (v["name"], v["parameter"])
        if value
    }
    var_patterns = {
        name: re.compile(rf"(?<![A-Za-z0-9_.]){re.escape(name)}(?![A-Za-z0-9_.])")
        for name in var_names
    }
    string_literal = re.compile(r'"[^"]*"')
    for comps in all_comps.values():
        for comp in comps:
            params = comp.get("paramsinfo", {})
            for pkey, pinfo in params.items():
                expr = string_literal.sub(" ", str(pinfo.get("value", "")))
                if not expr.strip():
                    continue
                for var, pattern in var_patterns.items():
                    if pattern.search(expr):
                        references.append({
                            "variable": var,
                            "component": comp.get("name", ""),
                            "component_type": comp.get("type", ""),
                            "parameter": pkey,
                        })

    return {
        "success": True,
        "project_path": reader.epp_path.resolve().as_posix(),
        "variables": variables,
        "references": references,
        "sweeps": sweeps,
    }


# ═══════════════════════════════════════════════════════════
# gRPC 原理图器件查询（v2）
# ═══════════════════════════════════════════════════════════

@mcp.tool()
def list_schematic_components(
        project_path: str,
        timeout_seconds: int = 60,
        summary_only: bool = False,
        limit: int = 50,
        offset: int = 0,
) -> dict[str, Any]:
    """列出当前原理图上存在的所有器件及器件对应参数，参数包括器件属性和位置几何信息

    列出原理图全部器件：器件属性 + 位置/旋转/镜像/引脚几何（gRPC 实时）。
    例如，电阻R的电阻值和位置坐标、引脚索引和引脚相对坐标、旋转角度、水平还是垂直镜像。
    每项除属性（如电阻阻值、器件类型）外，同步带几何：position（场景坐标）/
    rotation_deg / mirrored_horizontal / mirrored_vertical / pins（含引脚索引与相对坐标）。
    不需要再逐器件调 get_schematic_component_info 补几何。

    用法："列出这个工程原理图上的所有器件"、"查看原理图器件"、"把每个器件的位置和旋转角都列出来"

    定位：gRPC 实时读取当前工程全部器件（含 Var/Sweep 等），属性与几何信息（position /
    rotation_deg / mirrored_horizontal / mirrored_vertical / pins）同步返回。需要分页或
    只取器件标识时用 limit/offset 与 summary_only。

    Args:
        project_path: .epp 工程文件绝对路径。
        timeout_seconds: 单次调用的最长等待秒数，默认 60。
        summary_only: True 时每项只保留标量字段（instance_name/component_type/active_state/state
            以及 rotation_deg/mirrored_*），裁掉 parameters 与 position/pins（dict/list），省上下文；
            需要坐标或引脚时不要用它，改用 limit 分页。
        limit: 只返回前 N 个器件，默认 50；0 = 全部。截断时 details.truncated=true。
        offset: 从第几个器件开始返回，默认 0；配合 limit 翻页（details.total 是总数）。
            例：limit=50, offset=50 取第二页。
    Returns:
        gRPC 统一返回结构（业务字段在 details 中）：
        {"success": True, "status": "SUCCEEDED",
         "details": {"component_count": 5, "total": 5, "offset": 0, "limit": 50,
                    "returned": 5, "truncated": False, "components": [
             {"instance_name": "R1", "component_type": "R",
              "active_state": 0, "state": "NORMAL", "parameters": {...}}, ...]}}
        分页字段：total（器件总数）/ offset / limit / returned（本页条数）/
        truncated（本页之后还有没有）；component_count 保持原含义（总数）。
        每项含属性（parameters 等）与几何（position / rotation_deg / mirrored_* / pins），
        以及 component_uuid（模型库 Component UUID；性能器件非空、内置/仿真器件为空）。
        Var / Sweep / SParameter / HB(XDB) / 形状类(EBOND_ShapeJedec) 等非图形器件本来没有引脚，
        pins 返回空列表 [] 属正常，不代表数据缺失或异常；判断几何是否完整看 position。
    """
    result = call_project_grpc(ecserver_pb2.LIST_SCHEMATIC_COMPONENTS, project_path, timeout_seconds)
    if result.get("success"):
        comps = result.get("details", {}).get("components")
        if isinstance(comps, list):
            total = len(comps)
            if summary_only:
                comps = [{k: v for k, v in c.items() if not isinstance(v, (dict, list))}
                         if isinstance(c, dict) else c for c in comps]
            # 分页（2026-11-17，ENV-1）：先按 offset 跳过，再按 limit 截断。
            # 原先只有 limit ⇒ 只能看前 50 个，第 51 个以后根本取不到（要么 limit=0 全量爆上下文）。
            offset_eff = max(0, int(offset or 0))
            page = comps[offset_eff:]
            if limit and limit > 0 and len(page) > limit:
                page = page[:limit]
            details = result.setdefault("details", {})
            details.update({
                "total": total,
                "offset": offset_eff,
                "limit": limit,
                "returned": len(page),
                "truncated": (offset_eff + len(page)) < total,
            })
            comps = page
            result["details"]["components"] = comps
        result["message"] = "原理图器件查询成功"
    return result


@mcp.tool()
def get_schematic_component_info(
        project_path: str,
        instance_name: str | list[str],
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """按实例名查询器件参数与几何：支持单个名称或名称列表（批量）。

    每项含器件属性（如电阻阻值、器件类型）与几何 position / rotation_deg /
    mirrored_horizontal / mirrored_vertical / pins（引脚索引与相对坐标）。

    用法："查看 R1 这个器件的参数"、"U1 是什么类型的器件"、"这个器件现在的位置和旋转角是多少"

    instance_name 从 list_schematic_components 返回的 instance_name 字段取值。传字符串返回单个
    component；传列表返回 component_count + components，顺序与请求一致，任一实例不存在则整批失败。
    批量没有分页：每器件约 1.2 KB，一次建议不超过 10–20 个；全量请用 list_schematic_components 分页。
    返回含几何：position / rotation_deg / mirrored_horizontal / mirrored_vertical / pins。

    Args:
        project_path: .epp 工程文件绝对路径。
        instance_name: 器件实例名（如 "R1"）或实例名列表，取自 list_schematic_components 返回。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构（业务字段在 details 中）：
        传字符串时：{"success": True, "status": "SUCCEEDED",
         "details": {"instance_name": "R1", "component": {
             "instance_name": "R1", "component_type": "R",
             "active_state": 0, "state": "NORMAL", "parameters": {...},
             "position": {...}, "rotation_deg": 0, "pins": [...]}}}
        传列表时 details 含 component_count + components（元素结构与 component 一致）。
    """
    if isinstance(instance_name, str):
        instance_name, err = require_nonempty(instance_name, error_code="EMPTY_INSTANCE_NAME",
                                              label="instance_name")
        if err:
            return err
    elif isinstance(instance_name, list):
        if not instance_name:
            return error_response("EMPTY_INSTANCE_NAME", "instance_name 不能为空列表")
        cleaned = []
        for name in instance_name:
            if not isinstance(name, str):
                return error_response("INVALID_PARAMETERS", "instance_name 列表元素必须是字符串")
            s = name.strip()
            if not s:
                return error_response("EMPTY_INSTANCE_NAME", "instance_name 列表不能含空字符串")
            cleaned.append(s)
        instance_name = cleaned
    else:
        return error_response("INVALID_PARAMETERS", "instance_name 必须是字符串或字符串列表")
    return call_project_grpc(ecserver_pb2.GET_SCHEMATIC_COMPONENT_INFO, project_path,
                             timeout_seconds, instance_name=instance_name)


@mcp.tool()
def get_components_static_params(
        component_uuids: list[str],
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """查询器件的重量、尺寸、封装、所属厂商、成本等，传入参数 component_uuids 从 list_schematic_components或者get_schematic_component_info 接口返回的 component_uuid 获得。

    用法：查一下这几个器件的重量和尺寸 这几个器件的封装和厂商是什么，该任务在获取选型列表后，通过选型列表的model_uuid去对所选器件的固有属性进行合理性检查

    Args:
        component_uuids: 器件 UUID 数组（从 get_schematic_component_info 返回的 component_uuid 获得）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
        data 与请求 UUID 顺序一一对应，未命中的 UUID 保留为 null。
    """
    if not component_uuids:
        return error_response("INVALID_PARAMETERS", "component_uuids 不能为空数组")
    if not all(isinstance(u, str) and u.strip() for u in component_uuids):
        return error_response("INVALID_PARAMETERS", "component_uuids 必须是非空字符串数组")

    payload = {"original_uuids": [u.strip() for u in component_uuids]}
    return call_grpc(
        ecserver_pb2.GET_COMPONENTS_STATIC_PARAMS,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def batch_query_component_rf_params(
        model_name_list: list[str],
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """按器件型号字符串列表批量查询模型的频率、增益、回波损耗、抑制、P-1、Psat、隔离、插损、衰减等信息，返回对齐到输入顺序的结果。

    用法：批量查这几个型号的模型信息（如 HGC259、NC10424C_812）

    器件型号串数组（如 HGC259、NC10424C_812）。来源二选一：
    ① 选型阶段：search_public_models / search_personal_models 返回的 model_name；
    ② 工程内已有的性能器件：list_schematic_components 返回的 component_type，仅当该器件 component_uuid 非空。
    内置/仿真器件（TermG、P_nToneG、SParameter、Var、Sweep、XDB、Out）的 component_type 是类型名
    不是型号，传入后只会进 missing（整体仍返回 success=true），不要用它们。
    本工具只接受器件型号串，不接受 UUID；按 UUID 查固有参数（重量/尺寸/封装/厂商）用 get_components_static_params。

    下游模型服务只返回命中的型号且不保序。本工具将返回的 data 按输入 model_name_list
    顺序对齐：未命中的型号补 null 占位，并在 missing 中列出未命中的型号。

    该任务不需要打开工程，也不需要 project_path。gRPC 服务将请求转发到
    POST /api/v1/models/manage/batch_query_component/。

    Args:
        model_name_list: 器件型号字符串数组（如 "MAAD_008866"，非空，每个元素非空字符串，顺序和重复项保留）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段在 details 中：
        details.data 与输入 model_name_list 顺序一一对应，未命中项为 null；
        details.missing 为未命中的型号列表。
    """
    if not model_name_list:
        return error_response("INVALID_PARAMETERS", "model_name_list 不能为空数组")
    if not all(isinstance(u, str) and u.strip() for u in model_name_list):
        return error_response("INVALID_PARAMETERS", "model_name_list 必须是非空字符串数组")

    names = [u.strip() for u in model_name_list]
    payload = {"originalid_list": names}
    result = call_grpc(
        ecserver_pb2.BATCH_QUERY_COMPONENT,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )

    # 下游 data 不保序、未命中型号被静默丢弃。这里按 model 字段对齐输入顺序，
    # 未命中补 null、并返回 missing 列表，避免调用方按 index 错位或误判「库中无此件」。
    if result.get("success"):
        details = result.get("details")
        if isinstance(details, dict) and isinstance(details.get("data"), list):
            by_model = {item.get("model"): item for item in details["data"] if isinstance(item, dict)}
            details["data"] = [by_model.get(name) for name in names]
            details["missing"] = [name for name in names if name not in by_model]

    return result
