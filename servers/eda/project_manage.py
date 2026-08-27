r"""EDA 工程管理工具。

list_epp_projects             扫描文件夹中的所有 .epp 工程文件
open_edi_project              打开 .epp 工程，等待返回成功或失败
close_edi_project             关闭已打开的工程，可选择是否保存
get_project_summary           工程概览（元数据、原理图、仿真配置）

自然语言使用示例：
  帮我看看 C:/Users/JGL/EDI-Workspace 下面有哪些 .epp 工程
  帮我打开 EDA 工程 C:/.../EDI_TEST.epp
  帮我关闭这个工程
  帮我看看这个工程有哪些元件
  帮我查看 TermG1 元件的参数
  帮我获取这个工程的概览信息

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  folder_path      要扫描的文件夹绝对路径（list_epp_projects）
  schematic_name   原理图名称，默认 main
  timeout_seconds  最长等待秒数，默认 60 秒
  need_save        关闭前是否保存工程（close_edi_project），默认 False
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.eda.config import ProjectReader, parse_components, validate_project_path, SIM_COMPONENT_TYPES
from servers import mcp


@mcp.tool()
def list_epp_projects(folder_path: str) -> dict[str, Any]:
    """扫描指定文件夹中的 .epp 工程文件。

    用法："帮我看看 C:/Projects 下有哪些工程"、"扫描 EDI-Workspace 目录"

    Args:
        folder_path: 要扫描的文件夹绝对路径。

    Returns:
        {"success": True, "folder": "C:/...", "count": 3,
         "projects": [{"name": "demo1", "path": "C:/.../demo1.epp", "size": 0}, ...]}
    """
    if not folder_path or not folder_path.strip():
        raise ValueError("folder_path 不能为空，请提供要扫描的目录")
    root = Path(folder_path).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")

    _MAX_PROJECTS = 1000
    all_epp = sorted(root.rglob("*.epp"))
    total = len(all_epp)
    projects = []
    for epp in all_epp[:_MAX_PROJECTS]:
        projects.append({
            "name": epp.stem,
            "path": str(epp.resolve()),
            "size": epp.stat().st_size,
        })

    result: dict[str, Any] = {
        "success": True,
        "folder": str(root.resolve()),
        "count": len(projects),
        "projects": projects,
    }
    if total > _MAX_PROJECTS:
        result["truncated"] = True
        result["total"] = total
        result["message"] = f"共检测到 {total} 个工程，已截断到前 {_MAX_PROJECTS} 个，可缩小扫描范围获取全部"
    return result


@mcp.tool()
def open_edi_project(
        project_path: str,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """
    打开一个.epp 工程，例如C:\\Users\\JGL\\EDI-Workspace\\projects\\1\\1.epp

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.OPEN_PROJECT,
        {"project_path": resolved_path},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def close_edi_project(
        project_path: str,
        need_save: bool = False,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """关闭一个.epp 工程。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        need_save: 关闭前是否保存工程，默认 False。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.CLOSE_PROJECT,
        {"project_path": resolved_path, "need_save": need_save},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def get_project_summary(
    project_path: str,
    include_component_types: bool = True,
    include_latest_result: bool = True,
) -> dict[str, Any]:
    """获取 .epp 工程完整概览：元数据、原理图、元件统计、仿真配置、最近结果。

    用法："帮我看看这个工程的概览"、"查看工程信息"

    Args:
        project_path: .epp 工程文件绝对路径。
        include_component_types: 是否统计元件类型分布。
        include_latest_result: 是否包含最近仿真结果信息。

    Returns:
        {"success": True, "project": {...}, "schematics": {"count": 1, "names": ["main"]},
         "components": {"total": 15, "by_type": {"ResG": 5}},
         "simulation": [{"component_type": "SParameter", "Freq": "1 GHz", ...}],
         "latest_result": {"path": ".../result.raw", "exists": True, "size": 2048}}
    """
    reader = ProjectReader(project_path)
    metadata = reader.read_metadata()

    schematics = reader.list_schematics()
    schematics_info = {"count": len(schematics), "names": schematics}

    components_info: dict[str, Any] = {"total": 0, "by_type": {}}
    simulation_info: list[dict] = []
    # 单遍遍历：每个原理图只读+解析一次，同时统计组件与仿真配置
    for sname in schematics:
        raw = reader.read_schematic(sname)
        if not raw:
            continue
        comps = parse_components(raw)
        components_info["total"] += len(comps)
        for comp in comps:
            ct = comp.get("type", "")
            if include_component_types:
                components_info["by_type"][ct] = components_info["by_type"].get(ct, 0) + 1
            if ct in SIM_COMPONENT_TYPES:
                pi = comp.get("paramsinfo", {})
                entry: dict[str, Any] = {"component_type": ct, "instance_name": comp.get("name", "")}
                for key, info in pi.items():
                    if key == "BasicParameters" or not isinstance(info, dict):
                        continue
                    entry[key] = f"{info.get('value', '')} {info.get('unit', '')}".strip()
                simulation_info.append(entry)

    latest_result: dict[str, Any] = {}
    if include_latest_result:
        result_paths = list(reader.workspace.rglob("*.raw"))
        if result_paths:
            rp = sorted(
                result_paths, key=lambda x: x.stat().st_mtime, reverse=True
            )[0]
            latest_result = {
                "path": str(rp.resolve()),
                "exists": True,
                "size": rp.stat().st_size,
            }

    warnings: list[str] = []
    if not schematics:
        warnings.append("未找到原理图")

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
    """分析工程中的变量定义和引用关系。

    识别 Var 元件定义的变量、其他元件中对这些变量的引用、
    以及 Sweep 的扫描配置。

    Args:
        project_path: .epp 工程文件绝对路径。
    """
    reader = ProjectReader(project_path)
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
                            "status": pinfo.get("status", ""),
                        })

            # Sweep 元件：扫描配置
            if ct == "Sweep":
                sweep_var = params.get("SweepVar", {}).get("value", "")
                targets = [
                    params.get(f"SimInstanceName[{i}]", {}).get("value", "")
                    for i in range(1, 10)
                    if params.get(f"SimInstanceName[{i}]", {}).get("value")
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
    var_names = {
        value
        for v in variables
        for value in (v["name"], v["parameter"])
        if value
    }
    for comps in all_comps.values():
        for comp in comps:
            params = comp.get("paramsinfo", {})
            for pkey, pinfo in params.items():
                val = pinfo.get("value", "")
                if val and val in var_names:
                    references.append({
                        "variable": val,
                        "component": comp.get("name", ""),
                        "component_type": comp.get("type", ""),
                        "parameter": pkey,
                    })

    return {
        "success": True,
        "project_path": str(reader.epp_path.resolve()),
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
) -> dict[str, Any]:
    """通过 gRPC 查询原理图全部器件（含完整参数），比本地文件读取更实时。

    用法："列出这个工程原理图上的所有器件"、"查看原理图器件"

    Returns:
        gRPC 统一返回结构（业务字段在 details 中）：
        {"success": True, "status": "SUCCEEDED",
         "details": {"component_count": 5, "components": [
             {"instance_name": "R1", "component_type": "R",
              "active_state": 0, "state": "NORMAL", "parameters": {...}}, ...]}}
    """
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.LIST_SCHEMATIC_COMPONENTS,
        {"project_path": resolved_path},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def get_schematic_component_info(
    project_path: str,
    instance_name: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """通过 gRPC 按实例名查询单个器件的完整信息。

    用法："查看 R1 这个器件的参数"、"U1 是什么类型的器件"

    Returns:
        gRPC 统一返回结构（业务字段在 details 中）：
        {"success": True, "status": "SUCCEEDED",
         "details": {"instance_name": "R1", "component": {
             "instance_name": "R1", "component_type": "R",
             "active_state": 0, "state": "NORMAL", "parameters": {...}}}}
    """
    if not instance_name or not instance_name.strip():
        return {"success": False, "error_code": "EMPTY_INSTANCE_NAME",
                "message": "instance_name 不能为空"}
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.GET_SCHEMATIC_COMPONENT_INFO,
        {"project_path": resolved_path, "instance_name": instance_name.strip()},
        timeout_seconds,
        max_timeout_seconds=300,
    )
