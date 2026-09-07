"""EDA 模型库 / 原理图库工具 — 模型分类/搜索 + MMS 导入 + 原理图库搜索/使用。

模型库领域：get_model_category_params / search_public_models / search_personal_models
为只读查询；load_performance_component_from_mms 从 MMS 导入性能模型；
add_performance_component 将模型库 Component 放置到原理图。

原理图库领域：search_schematic_from_public_library / search_schematic_from_personal_library
搜索在线原理图库；use_schematic_from_library_create_project /
use_schematic_from_library_import 用库内容创建 / 导入工程。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.config import validate_project_path
from servers.eda.grpc_client import call_grpc
from servers.utils import error_response, require_nonempty, require_position, require_uuid
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


def _search_models(
        sub_type: str,
        filters: list | None,
        task_type: int,
        timeout_seconds: int,
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
    return call_grpc(task_type, payload, timeout_seconds, max_timeout_seconds=300)


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
        filters: 过滤条件数组。每个元素为 {"key": "<参数id>", "min": 数值, "max": 数值}，key 取 get_model_category_params 返回的参数 id（如增益的 id 是 "gain"），min/max 二选一或都用（闭区间，单位见参数定义）。
         示例：筛选增益≥20dB → [{"key": "gain", "min": 20}]；
              叠加频段 → [{"key": "gain", "min": 20}, {"key": "min_freq", "min": 27000}]
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    return _search_models(sub_type, filters, ecserver_pb2.SEARCH_PUBLIC_MODELS, timeout_seconds)


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
        filters: 过滤条件数组。每个元素为 {"key": "<参数id>", "min": 数值, "max": 数值}，key 取 get_model_category_params 返回的参数 id（如增益的 id 是 "gain"），min/max 二选一或都用（闭区间，单位见参数定义）。
         示例：筛选增益≥20dB → [{"key": "gain", "min": 20}]；
              叠加频段 → [{"key": "gain", "min": 20}, {"key": "min_freq", "min": 27000}]
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（code/message/data）在 details 中。
    """
    return _search_models(sub_type, filters, ecserver_pb2.SEARCH_PERSONAL_MODELS, timeout_seconds)


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
    original_uuid, err = require_uuid(original_uuid, label="original_uuid")
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


def _search_schematic(search_name: str, task_type: int, timeout_seconds: int) -> dict[str, Any]:
    """按拓扑描述查询原理图库（公共/个人复用，仅 task_type 不同）。"""
    return call_grpc(
        task_type,
        {"search_name": search_name or ""},
        timeout_seconds,
        max_timeout_seconds=60,
    )


@mcp.tool()
def search_schematic_from_public_library(
        search_name: str,
        timeout_seconds: int = 30,
) -> dict[str, Any]:
    """按拓扑描述查询公共原理图库。

    用法："搜索公共原理图库"、"查一下有没有功放相关的原理图"

    search_name 是必需的拓扑描述关键词，服务端固定转换为 topology_description
    过滤条件，不接收自定义 filters。不需要 project_path、工作区或已打开工程。
    使用本工具前应提示用户提供对应的拓扑描述。

    Args:
        search_name: 拓扑描述关键词（必需，如 "功放"）。
        timeout_seconds: 最长等待秒数，默认 30（服务端查询超时 10 秒）。

    Returns:
        gRPC 统一返回结构，成功时 details 含 count 和 results
        （每项 id/schematic_id/name/properties）。
    """
    return _search_schematic(search_name, ecserver_pb2.SEARCH_SCHEMATIC_FROM_PUBLIC_LIBRARY, timeout_seconds)


@mcp.tool()
def search_schematic_from_personal_library(
        search_name: str,
        timeout_seconds: int = 30,
) -> dict[str, Any]:
    """按拓扑描述查询个人原理图库。

    用法："搜索我的原理图库"、"查一下个人库里的原理图"

    search_name 是必需的拓扑描述关键词，使用前应提示用户提供对应拓扑描述。
    参数与返回结构同 search_schematic_from_public_library，仅查询范围为个人库。

    Args:
        search_name: 拓扑描述关键词（必需，如 "功放"）。
        timeout_seconds: 最长等待秒数，默认 30。
    """
    return _search_schematic(search_name, ecserver_pb2.SEARCH_SCHEMATIC_FROM_PERSONAL_LIBRARY, timeout_seconds)


@mcp.tool()
def use_schematic_from_library_create_project(
        file_uuid: str,
        timeout_seconds: int = 180,
) -> dict[str, Any]:
    """从在线原理图库下载内容，在当前工作区创建并打开新工程。

    用法："用原理图库里的这个原理图创建一个新工程"

    file_uuid 是在线原理图文件的合法 UUID（取自搜索结果的 id）。服务端异步下载
    ZIP、校验包内恰好一个 .epp 及 schematics/main/schematic.ep、安装随包模型/符号/
    仿真依赖；缺少 Component 时从 MMS 下载并等待模型库扫描。下载超时 120 秒，
    模型库扫描超时 60 秒。同名工程目录存在则失败、不覆盖。

    Args:
        file_uuid: 在线原理图文件的 UUID。
        timeout_seconds: 最长等待秒数，默认 180。

    Returns:
        gRPC 统一返回结构，成功时 details 含 project_path/schematic_uuid/
        component_count/net_segment_count。
    """
    file_uuid, err = require_uuid(file_uuid, label="file_uuid")
    if err:
        return err
    return call_grpc(
        ecserver_pb2.USE_SCHEMATIC_FROM_LIBRARY_CREATE_PROJECT,
        {"file_uuid": file_uuid},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def use_schematic_from_library_import(
        file_uuid: str,
        project_path: str,
        timeout_seconds: int = 180,
) -> dict[str, Any]:
    """从在线原理图库下载内容，替换指定工程的当前原理图并保存。

    用法："用原理图库里的这个原理图替换当前工程"

    ⚠️ 此操作会替换指定工程的原理图，请确认 project_path。与 create 方式不同，
    目标是客户端指定的已有 .epp 工程（已打开复用窗口，否则打开）。下载/扫描
    超时与依赖安装规则同 use_schematic_from_library_create_project。

    Args:
        file_uuid: 在线原理图文件的 UUID。
        project_path: .epp 工程文件绝对路径。
        timeout_seconds: 最长等待秒数，默认 180。
    """
    file_uuid, err = require_uuid(file_uuid, label="file_uuid")
    if err:
        return err
    resolved = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.USE_SCHEMATIC_FROM_LIBRARY_IMPORT,
        {"file_uuid": file_uuid, "project_path": resolved},
        timeout_seconds,
        max_timeout_seconds=300,
    )
