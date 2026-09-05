"""EDA 模型库工具 — 模型分类/查询/搜索 + MMS 导入 + 性能器件放置。

五个工具均围绕模型库领域：前三个为只读查询（不需要 project_path），
load_performance_component_from_mms 从 MMS 导入性能模型到本地模型库，
add_performance_component 将工作区模型库中的 Component 放置到原理图。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.config import validate_project_path
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, require_position
from servers import mcp


@mcp.tool()
def get_model_category_params(timeout_seconds: int = 60,
                              categories_only: bool = False) -> dict[str, Any]:
    """获取模型管理模块的全部子类及对应参数列表。

    用法："看看模型库有哪些分类"、"获取模型分类和参数"

    该任务不需要打开工程，也不需要 project_path。gRPC 服务转发到
    GrpcApiManager::CategoryParams，成功时将数组放入 data 字段。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。
        categories_only: 为 True 时每个子类只保留标量字段（子类id/子类名称/总类id/总类名称），
            裁掉容器字段（参数列表，158KB 大头）。已按真实 data 结构核对命中。

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

    return result


@mcp.tool()
def search_public_models(
        sub_type: str,
        filters: list | None = None,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """按子类查询公共模型库。

    用法："查一下子类 61 的公共模型"、"搜索公共模型库"

    sub_type 是模型子类 ID（非空字符串），filters 数组原样转发给模型服务。

    Args:
        sub_type: 模型子类 ID（非空字符串，如 "61"）。
        filters: 过滤条件数组（原样转发，可选）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    sub_type, err = require_nonempty(sub_type, label="sub_type")
    if err:
        return err
    payload: dict[str, Any] = {
        "sub_type": sub_type,
        "filters": filters if filters is not None else [],
    }
    return call_grpc(
        ecserver_pb2.SEARCH_PUBLIC_MODELS,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def search_personal_models(
        sub_type: str,
        filters: list | None = None,
        timeout_seconds: int = 60,
) -> dict[str, Any]:
    """按子类查询个人模型库。

    用法："查一下子类 61 的个人模型"、"搜索我的模型库"

    sub_type 是模型子类 ID（非空字符串），filters 数组原样转发给模型服务。

    Args:
        sub_type: 模型子类 ID（非空字符串，如 "61"）。
        filters: 过滤条件数组（原样转发，可选）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    sub_type, err = require_nonempty(sub_type, label="sub_type")
    if err:
        return err
    payload: dict[str, Any] = {
        "sub_type": sub_type,
        "filters": filters if filters is not None else [],
    }
    return call_grpc(
        ecserver_pb2.SEARCH_PERSONAL_MODELS,
        payload,
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def load_performance_component_from_mms(
        original_uuid: str,
        timeout_seconds: int = 90,
) -> dict[str, Any]:
    """从 MMS 下载性能模型并导入当前工作区本地模型库。

    用法："从 MMS 导入这个性能模型 <uuid>"

    使用程序当前已加载的工作区，无需 .epp、project_path 或工作区路径，不要求打开
    工程。下载、解压并校验 library.ep 后合并至本地模型库，随后复制仿真文件并
    发起模型库重新扫描（下载超时 60 秒）。

    注意：成功只表示导入函数返回成功、已发起扫描，不代表异步扫描完成；导入后
    需等待模型库扫描完成再调用 add_performance_component，否则可能查不到 Component。

    Args:
        original_uuid: MMS 原始模型 UUID（非空字符串，格式按 Uuid::isValid 校验）。
        timeout_seconds: 最长等待秒数，默认 90。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    original_uuid, err = require_nonempty(original_uuid, label="original_uuid")
    if err:
        return err
    return call_grpc(
        ecserver_pb2.LOAD_PERFORMANCE_COMPONENT_FROM_MMS,
        {"original_uuid": original_uuid},
        timeout_seconds,
        max_timeout_seconds=300,
    )


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

    注意：load_performance_component_from_mms 导入后需等待模型库扫描完成再调用
    本工具，否则可能暂时查不到 Component。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_uuid: 工作区模型库中的 Component UUID（非空字符串）。
        position: 场景坐标对象 {"x": 100, "y": 200}（x/y 均为有限数值）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    resolved = validate_project_path(project_path)
    component_uuid, err = require_nonempty(component_uuid, label="component_uuid")
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
