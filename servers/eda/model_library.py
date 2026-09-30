"""EDA 模型库 / 原理图库工具 — 模型分类/搜索 + MMS 导入 + 拓扑设计。

模型库领域：get_model_category_params / search_public_models / search_personal_models
为只读查询；add_performance_component 将模型库 Component 放置到原理图。

原理图库领域：design_schematic_topology 按自然语言需求直接设计链路并创建工程。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.config import require_project_path
from servers.eda.grpc_client import call_grpc
from servers.utils import error_response, require_nonempty, require_position, require_uuid
from servers import mcp


@mcp.tool()
def get_model_category_params(timeout_seconds: int = 60,
                              categories_only: bool = True) -> dict[str, Any]:
    """获取模型管理模块的全部子类及对应参数列表。

    用法："看看模型库有哪些分类"、"获取模型分类和参数"

    该任务不需要打开工程，也不需要 project_path。gRPC 服务转发到
    GrpcApiManager::CategoryParams，成功时将数组放入 data 字段。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。
        categories_only: 默认 True——每个子类只保留标量字段（子类id/子类名称/总类id/总类名称），
            裁掉容器字段（参数列表，约 42KB 大头）。需要完整参数列表时显式传 False。

    Returns:
        gRPC 统一返回结构，业务字段 data 在 details 中（失败时 data 为空数组）。
    """
    result = call_grpc(
        ecserver_pb2.GET_MODEL_CATEGORY_PARAMS,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )

    if categories_only and result.get("success"):
        data = result.get("details", {}).get("data")
        if isinstance(data, list):
            trimmed = []
            for item in data:
                if isinstance(item, dict):
                    trimmed.append({k: v for k, v in item.items()
                                    if not isinstance(v, (list, dict))})
                else:
                    trimmed.append(item)
            result.setdefault("details", {})["data"] = trimmed

    if result.get("success"):
        result["message"] = "模型分类参数获取成功"

    return result


def _search_models(
        sub_type: str,
        filters: list | None,
        task_type: int,
        timeout_seconds: int,
        limit: int = 0,
) -> dict[str, Any]:
    """按子类查询模型库（公共/个人复用，仅 task_type 不同）。"""
    sub_type, err = require_nonempty(sub_type, label="sub_type")
    if err:
        return err
    if filters is not None:
        if not isinstance(filters, list):
            return error_response("INVALID_PARAMETERS", "filters 必须是数组")
        for item in filters:
            if not isinstance(item, dict):
                return error_response("INVALID_PARAMETERS", "filters 每个元素必须是对象 {key, min, max}")
            if not isinstance(item.get("key"), str) or not item.get("key"):
                return error_response("INVALID_PARAMETERS", "filters 每个元素需要非空字符串 key")
            if "min" not in item and "max" not in item:
                return error_response("INVALID_PARAMETERS", "filters 每个元素需要 min 或 max 至少一个")
    payload: dict[str, Any] = {
        "sub_type": sub_type,
        "filters": filters if filters is not None else [],
    }
    result = call_grpc(task_type, payload, timeout_seconds, max_timeout_seconds=300)
    if limit and limit > 0 and result.get("success"):
        data = (result.get("details") or {}).get("data")
        # 真实返回里 data 是 {"count": N, "results": [...]}；也可能是纯 list，两种形状都兼容。
        bucket = data.get("results") if isinstance(data, dict) else data
        if isinstance(bucket, list) and len(bucket) > limit:
            bucket[:] = bucket[:limit]  # 原地截断，dict 形状时 count 保留
            result.setdefault("details", {})["returned"] = limit
    return result


@mcp.tool()
def search_public_models(
        sub_type: str,
        filters: list | None = None,
        timeout_seconds: int = 60,
        limit: int = 0,
) -> dict[str, Any]:
    """按子类查询公共模型库。

    用法："查一下子类 61 的公共模型"、"搜索公共模型库"

    sub_type 是模型子类 ID（非空字符串），filters 数组原样转发给模型服务。

    Args:
        sub_type: 模型子类 ID（非空字符串，如 "61"）。
        filters: 过滤条件数组。每个元素为 {"key": "<参数id>", "min": 数值, "max": 数值}，key 取 get_model_category_params 返回的参数 id（如增益的 id 是 "gain"），min/max 二选一或都用（闭区间，单位见参数定义）。
         示例：筛选增益≥20dB → [{"key": "gain", "min": 20}]；
              叠加频段 → [{"key": "gain", "min": 20}, {"key": "min_freq", "min": 27000}]
        timeout_seconds: 最长等待秒数，默认 60。
        limit: 只返回前 N 条结果（0 表示服务端默认，通常 10 条）。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    return _search_models(sub_type, filters, ecserver_pb2.SEARCH_PUBLIC_MODELS, timeout_seconds, limit)


@mcp.tool()
def search_personal_models(
        sub_type: str,
        filters: list | None = None,
        timeout_seconds: int = 60,
        limit: int = 0,
) -> dict[str, Any]:
    """按子类查询个人模型库。

    用法："查一下子类 61 的个人模型"、"搜索我的模型库"

    sub_type 是模型子类 ID（非空字符串），filters 数组原样转发给模型服务。

    Args:
        sub_type: 模型子类 ID（非空字符串，如 "61"）。
        filters: 过滤条件数组。每个元素为 {"key": "<参数id>", "min": 数值, "max": 数值}，key 取 get_model_category_params 返回的参数 id（如增益的 id 是 "gain"），min/max 二选一或都用（闭区间，单位见参数定义）。
         示例：筛选增益≥20dB → [{"key": "gain", "min": 20}]；
              叠加频段 → [{"key": "gain", "min": 20}, {"key": "min_freq", "min": 27000}]
        timeout_seconds: 最长等待秒数，默认 60。
        limit: 只返回前 N 条结果（0 表示服务端默认，通常 10 条）。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    return _search_models(sub_type, filters, ecserver_pb2.SEARCH_PERSONAL_MODELS, timeout_seconds, limit)


@mcp.tool()
def add_performance_component(
        project_path: str,
        component_uuid: str,
        position: dict,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """将工作区模型库中的 Component 放置到指定工程原理图并保存。

    用法："把性能器件 <uuid> 放到 (100,200)"

    component_uuid 为工作区模型库中的 Component UUID，不能假定等于 MMS 的
    original_uuid。坐标会吸附到网格。不自动下载模型、不额外设置参数、不自动
    避让重叠、不返回器件实例名。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_uuid: 工作区模型库中的 Component UUID（非空字符串）。
        position: 场景坐标对象 {"x": 100, "y": 200}（x/y 均为有限数值）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    resolved, err = require_project_path(project_path)
    if err:
        return err
    component_uuid, err = require_uuid(component_uuid, label="component_uuid")
    if err:
        return err
    pos, err = require_position(position)
    if err:
        return err
    return call_grpc(
        ecserver_pb2.ADD_PERFORMANCE_COMPONENT,
        {"project_path": resolved, "component_uuid": component_uuid, "position": pos},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def design_schematic_topology(
        requirement: str,
        timeout_seconds: int = 180,
) -> dict[str, Any]:
    """用户输入指标来设计cst天线模型（中心频率），hfss带通滤波器（中心频率，相对带宽）、低通滤波器（截止频率）负载（中心频率）等电路，然后自动创建并打开新工程。

    用法："设计一个 1GHz 功放链路，含输入匹配和偏置"

    requirement 是自然语言需求描述（非空）。服务端据此到原理图库中取对应的拓扑
    （个人库优先，无候选回退公共库），命中后自动生成工程名并在当前工作区创建、打开新工程。

    Args:
        requirement: 自然语言需求描述（非空字符串）。
        timeout_seconds: 最长等待秒数，默认 180（含取拓扑 + 下载 + 模型库扫描）。

    Returns:
        gRPC 统一返回结构，成功时 details 含 project_name/project_path/schematic_id/
        component_count/net_segment_count/status。
    """
    requirement, err = require_nonempty(requirement, label="requirement")
    if err:
        return err
    return call_grpc(
        ecserver_pb2.DESIGN_SCHEMATIC_TOPOLOGY,
        {"requirement": requirement},
        timeout_seconds,
        max_timeout_seconds=300,
    )
