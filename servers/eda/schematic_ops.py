"""EDA 原理图扩展操作工具 — 内置器件 / 清空原理图 / 连线。

list_ideal_components   列出内置器件类型及说明（只读，无需工程）
add_ideal_component     按指定坐标新增内置器件（工厂默认参数）
clear_schematic         清空原理图全部器件和网段（破坏性，需 confirm_clear）
add_wire                连接两个器件的指定引脚
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.config import validate_project_path
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, error_response, require_position
from servers import mcp


@mcp.tool()
def list_ideal_components(timeout_seconds: int = 60) -> dict[str, Any]:
    """列出内置器件类型及其简要说明。

    用法："列出内置器件类型"、"有哪些内置器件可用"

    数据直接读取 ComponentToolBar 使用的 symbolDescriptionMap，不需要打开工程。
    每项包含 component_type 和 description。

    Args:
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，业务字段（component_count/components）在 details 中。
    """
    return call_grpc(
        ecserver_pb2.LIST_IDEAL_COMPONENTS,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def add_ideal_component(
        project_path: str,
        component_type: str,
        position: dict,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按指定坐标新增内置器件，使用工厂默认参数，不自动排布。

    用法："在 (100,200) 处放一个电阻 R"

    严格按指定场景坐标放置，不做网格吸附、自动排布或重叠避让。component_type
    区分大小写，支持范围同 CREATE_SIMULATION_COMPONENT 的工厂注册类型列表。
    不接收 parameters，后续参数设置使用 update_simulation_component。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_type: 器件工厂注册类型名（非空、区分大小写，如 "R"）。
        position: 场景坐标对象 {"x": 100, "y": 200}（x/y 均为有限数值）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details 含 instance_name。
    """
    resolved = validate_project_path(project_path)
    component_type, err = require_nonempty(component_type, label="component_type")
    if err:
        return err
    pos, err = require_position(position)
    if err:
        return err
    return call_grpc(
        ecserver_pb2.ADD_IDEAL_COMPONENT,
        {"project_path": resolved, "component_type": component_type, "position": pos},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def clear_schematic(
        project_path: str,
        confirm_clear: bool = False,
        timeout_seconds: int = 300,
) -> dict[str, Any]:
    """清空工程原理图中的全部器件和网段并保存（破坏性操作）。

    用法："清空这个工程的原理图"

    ⚠️ 此操作会删除原理图全部器件（含控制器、Var、Out）、独立文本以及全部网段、
    连线和连接点，并保存工程，原内容不可恢复。仅需提供 .epp 工程路径；调用即
    表示确认清空，不额外弹出确认窗口。

    Args:
        project_path: .epp 工程文件绝对路径。
        confirm_clear: 必须显式传 True 才会执行（双重确认）。
        timeout_seconds: 最长等待秒数，默认 300。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    resolved = validate_project_path(project_path)
    if not confirm_clear:
        return error_response(
            "CLEAR_CONFIRMATION_REQUIRED",
            "clear_schematic 会清空原理图全部器件和网段并保存；确认后请同时传 confirm_clear=true",
            warning="本操作将清空原理图全部内容且不可恢复",
        )
    return call_grpc(
        ecserver_pb2.CLEAR_SCHEMATIC,
        {"project_path": resolved},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def add_wire(
        project_path: str,
        first_instance_name: str,
        first_pin_index: int,
        second_instance_name: str,
        second_pin_index: int,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """连接指定工程内两个器件的指定引脚，按需创建或合并网段并保存。

    用法："把 R1 的 0 号引脚连到 C1 的 0 号引脚"

    pin_index 是 0 开始的内部引脚编号（不是界面显示的端口名称），必须是
    0~2147483647 的整数。拒绝同一引脚自连接。新增连线及网段合并作为可撤销操作，
    提交后保存工程；保存失败时撤销本次操作。

    Args:
        project_path: .epp 工程文件绝对路径。
        first_instance_name: 第一个器件的实例名（非空字符串）。
        first_pin_index: 第一个器件的引脚编号（0 开始的整数）。
        second_instance_name: 第二个器件的实例名（非空字符串）。
        second_pin_index: 第二个器件的引脚编号（0 开始的整数）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功提示或失败原因在 message 中。
    """
    resolved = validate_project_path(project_path)
    first_instance_name, err = require_nonempty(first_instance_name, label="first_instance_name")
    if err:
        return err
    second_instance_name, err = require_nonempty(second_instance_name, label="second_instance_name")
    if err:
        return err
    for label, v in (("first_pin_index", first_pin_index), ("second_pin_index", second_pin_index)):
        if not isinstance(v, int) or isinstance(v, bool) or not (0 <= v <= 2147483647):
            return error_response("INVALID_PARAMETERS", f"{label} 必须是 0~2147483647 的整数")
    if first_instance_name == second_instance_name and first_pin_index == second_pin_index:
        return error_response("INVALID_PARAMETERS", "不能连接同一器件的同一引脚（自连接）")
    return call_grpc(
        ecserver_pb2.ADD_WIRE,
        {
            "project_path": resolved,
            "first_instance_name": first_instance_name,
            "first_pin_index": first_pin_index,
            "second_instance_name": second_instance_name,
            "second_pin_index": second_pin_index,
        },
        timeout_seconds,
        max_timeout_seconds=300,
    )
