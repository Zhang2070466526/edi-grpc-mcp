"""EDA 模型库查询工具 — 获取模型分类参数 + 查询公共/个人模型库。

三个工具均为只读查询，不需要 project_path、不要求打开工程，返回模型服务的
裁剪响应（code/message/data）。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty
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
