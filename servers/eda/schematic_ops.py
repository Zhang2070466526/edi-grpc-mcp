"""EDA 原理图扩展操作工具 — 内置器件 / 清空原理图 / 连线 / 批量器件变换。

list_ideal_components                   列出内置器件类型及说明（只读，无需工程）
add_ideal_component                     按指定坐标新增内置器件（工厂默认参数）
clear_schematic                         清空原理图全部器件和网段（破坏性，需 confirm_clear）
add_wire                                连接两个器件的指定引脚
update_schematic_components_transform   批量设置器件位置/旋转/镜像
"""

from __future__ import annotations

import math
from typing import Any

from proto import ecserver_pb2
from servers.eda.config import require_project_path
from servers.eda.grpc_client import call_grpc
from servers.utils import require_nonempty, error_response, require_position, require_uuid
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
    result = call_grpc(
        ecserver_pb2.LIST_IDEAL_COMPONENTS,
        {},
        timeout_seconds,
        max_timeout_seconds=300,
    )
    if result.get("success"):
        result["message"] = "内置器件查询成功"
    return result


@mcp.tool()
def add_ideal_component(
        project_path: str,
        component_type: str,
        position: dict,
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """按指定坐标新增内置器件，只放置器件，不修改器件属性。

    用法："在 (100,200) 处放一个电阻 R"

    严格按指定场景坐标放置，不做网格吸附、自动排布或重叠避让。component_type 必须是
    list_ideal_components 接口返回数据中存在的 component_type（区分大小写）。
    只放置器件、不修改器件属性，后续参数设置使用 update_simulation_component。

    Args:
        project_path: .epp 工程文件绝对路径。
        component_type: 内置器件类型名，必须来自 list_ideal_components 返回（非空、区分大小写，如 "R"）。
        position: 场景坐标对象 {"x": 100, "y": 200}（x/y 均为有限数值）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details 含 instance_name。
    """
    resolved, err = require_project_path(project_path)
    if err:
        return err
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
    resolved, err = require_project_path(project_path)
    if err:
        return err
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
    """连接指定工程对应原理图中的两个器件的指定引脚，按需创建或合并网段并保存。

    用法："把 R1 的 0 号引脚连到 C1 的 0 号引脚"

    器件引脚可以在 list_schematic_components 和 get_schematic_component_info 获取。
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
    resolved, err = require_project_path(project_path)
    if err:
        return err
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


@mcp.tool()
def update_schematic_components_transform(
        project_path: str,
        components: list[dict],
        timeout_seconds: int = 120,
) -> dict[str, Any]:
    """批量设置原理图器件的位置、旋转角度和镜像状态（对齐/挪位/批量旋转镜像；坐标为场景值，按 12.5 网格吸附）。

    用法："把 R1 移到 (100,200) 并旋转 90 度"、"批量镜像这几个器件"、"把这四个器件对齐到同一水平线"

    按实例 uuid 或 instance_name 定位器件；position / rotation_deg / mirrored_horizontal /
    mirrored_vertical 均为目标绝对状态，省略的字段保持原值。整批一起校验，任一项非法则整批
    不修改；存在实际修改时形成一个撤销操作并保存，保存失败回滚。

    坐标口径（实测）：position 是原理图场景坐标，与 get_schematic_component_info 返回的
    position 同一套，不是毫米也不是截图像素；落位按工程网格吸附——请求 y=120 实际会落到
    125。以返回的 position 为准，别拿请求值当结果，否则会误判成"没生效"。
    取现值用 get_schematic_component_info，或 list_schematic_components 一次带出
    （列表接口同步返回几何）。

    限制与副作用（均实测）：
        - 目标为 InsertText 时整批拒绝：文本框不能用本工具挪。
        - 只改器件几何，不重算传输线/键合线参数：挪了器件后相邻 EBOND 的 Length 仍是原值，
          需要自己用 update_simulation_component 同步，否则仿真口径与实际布局不一致。
        - 同一批次不能重复指定同一器件（uuid 与名称各指定一次也算重复）。
        - 器件不存在、字段非法时整批不修改，不会出现"改了一半"的状态。
        - rotation_deg 归一化到 [0, 360)：450 等效 90，-90 等效 270；两个镜像字段是目标绝对
          状态，重复发同一状态不会来回翻转。

    Args:
        project_path: .epp 工程文件绝对路径。
        components: 非空数组，每项 {uuid?, instance_name?, position?, rotation_deg?, mirrored_horizontal?, mirrored_vertical?}。
            每项至少提供 uuid 或 instance_name，且至少一个变换字段。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        gRPC 统一返回结构，成功时 details 含 project_path / component_count / components（本批器件更新后完整信息，含引脚坐标）。
        返回可直接自证：每项的 position / rotation_deg / mirrored_* 是落位后的实际值，pins 里的
        引脚坐标会随旋转和镜像变化（如镜像后 pin0 从左侧翻到右侧），据此核对方向，不必再查一次。
    """
    resolved, err = require_project_path(project_path)
    if err:
        return err
    if not isinstance(components, list) or not components:
        return error_response("INVALID_PARAMETERS", "components 必须是非空数组")

    cleaned: list[dict] = []
    for i, item in enumerate(components):
        where = f"components[{i}]"
        if not isinstance(item, dict):
            return error_response("INVALID_PARAMETERS", f"{where} 必须是对象")

        out: dict[str, Any] = {}
        # 标识符：uuid（须合法 UUID）或 instance_name（非空字符串），至少一个
        if item.get("uuid") is not None:
            uid, uerr = require_uuid(item["uuid"], label=f"{where}.uuid")
            if uerr:
                return uerr
            out["uuid"] = uid
        if item.get("instance_name") is not None:
            name, nerr = require_nonempty(item["instance_name"], label=f"{where}.instance_name")
            if nerr:
                return nerr
            out["instance_name"] = name
        if "uuid" not in out and "instance_name" not in out:
            return error_response("INVALID_PARAMETERS",
                                  f"{where} 至少要提供 uuid 或 instance_name 之一")

        transform_keys = ("position", "rotation_deg", "mirrored_horizontal", "mirrored_vertical")
        if not any(k in item for k in transform_keys):
            return error_response("INVALID_PARAMETERS",
                                  f"{where} 至少要提供一个变换字段（{'/'.join(transform_keys)}）")

        if "position" in item:
            pos, perr = require_position(item["position"])
            if perr:
                return error_response("INVALID_PARAMETERS", f"{where} {perr['message']}")
            if not (-1e9 <= pos["x"] <= 1e9 and -1e9 <= pos["y"] <= 1e9):
                return error_response("INVALID_PARAMETERS",
                                      f"{where}.position 的 x、y 必须在 [-1e9, 1e9] 范围内")
            out["position"] = pos
        if "rotation_deg" in item:
            r = item["rotation_deg"]
            if not isinstance(r, (int, float)) or isinstance(r, bool) or not math.isfinite(r):
                return error_response("INVALID_PARAMETERS", f"{where}.rotation_deg 必须是有限数值")
            out["rotation_deg"] = r
        for mf in ("mirrored_horizontal", "mirrored_vertical"):
            if mf in item:
                if not isinstance(item[mf], bool):
                    return error_response("INVALID_PARAMETERS", f"{where}.{mf} 必须是布尔值")
                out[mf] = item[mf]
        cleaned.append(out)

    # 实测（2026-11-13）：本事件不需要 payload 顶层的 sub_type / filters。
    #   - EDI 于 15:09 启动的那次实例，缺 sub_type 会 REJECTED「sub_type must be a non-empty string」，
    #     当时只能补上 sub_type/filters 才通；
    #   - EDI 重启后的实例，同一个 payload（只有 project_path + components）直接 SUCCEEDED，
    #     sub_type × filters 的 24 种组合（缺失/空串/null/数组/字符串）全部成功，说明二者均非必填。
    # 结论：不带这两个字段即可，代码保持最小 payload；若某天又出现 sub_type 报错，先看 EDI 进程/版本，
    # 不要以为是器件字段问题（报错措辞会误导）。
    return call_grpc(
        ecserver_pb2.UPDATE_SCHEMATIC_COMPONENTS_TRANSFORM,
        {"project_path": resolved, "components": cleaned},
        timeout_seconds,
        max_timeout_seconds=300,
    )
