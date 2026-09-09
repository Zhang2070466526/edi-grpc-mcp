"""MCP Prompts（器件类）— 器件配置、器件选型。

  configure_simulation_component — Schema→参数映射→确认→创建/更新
  select_component                — 器件选型：分类→搜索→对比→推荐→替换
"""

from __future__ import annotations

from typing import Any

from servers import mcp


@mcp.prompt(
    name="configure_simulation_component",
    title="配置仿真器件",
    description="按用户需求配置 SP/HB/XDB 仿真器件参数。创建或修改前先查询 Schema 和现有配置。",
)
def prompt_configure_simulation_component(
        project_path: str,
        action: str,
        component_type: str,
        instance_name: str = "",
        requirements: str = "",
) -> list[dict[str, Any]]:
    """配置仿真器件的工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        action: create（创建）或 update（更新）。
        component_type: SParameter / HarmonicBalance / XDB。
        instance_name: update 时需要提供实例名。
        requirements: 用户对参数的自然语言描述。
    """
    act = action.lower().strip()
    if act not in ("create", "update"):
        return [{"role": "user", "content": (
            f"错误：action 必须是 'create' 或 'update'，收到 '{action}'。"
        )}]

    ct = component_type.strip()
    if not ct:
        return [{"role": "user", "content": "错误：component_type 不能为空。"}]

    if act == "update" and not instance_name.strip():
        return [{"role": "user", "content": (
            "错误：action=update 时必须提供 instance_name。"
        )}]

    steps: list[str] = [
        "1. 调用 `get_simulation_component_schema` 查询器件支持的参数、类型和单位。",
        "   （如果客户端支持 Resource，也可读取 edi://reference/simulation-components）",
    ]

    if act == "update":
        inst = instance_name.strip() or "（请提供实例名）"
        steps += [
            f"3. 调用 `list_simulation_components` 查找 {inst} 的当前参数。",
            f"4. 把用户需求「{requirements or '修改参数'}」映射为合法的参数名、值和单位。",
            f"5. 在执行前向用户展示：目标器件 {inst}（{ct}）、参数名称、新的值、单位。",
            f"6. 用户确认后调用 `update_simulation_component`，传入 instance_name=\"{inst}\" 和 component_type=\"{ct}\"。",
        ]
    else:
        steps += [
            f"3. 把用户需求「{requirements or '使用默认参数'}」映射为合法的参数名、值和单位。",
            f"4. 在执行前向用户展示：器件类型 {ct}、参数名称、值、单位。",
            f"5. 用户确认后调用 `create_simulation_component`，传入 component_type=\"{ct}\"。",
        ]

    steps += [
        "",
        "重要约束：",
        "- 参数名必须与 `get_simulation_component_schema` 返回的一致。",
        "- 不要编造参数名、单位或 wire 字段。",
        "- 无单位参数不要传 unit 字段。",
        "- 每次 create 都会创建新实例，EDI 自动分配实例名。",
        "- TIMEOUT 或 STREAM_DISCONNECTED 后禁止自动重试。",
    ]

    return [
        {
            "role": "user",
            "content": "\n".join(steps),
        },
    ]


@mcp.prompt(
    name="select_component",
    title="器件选型",
    description="按需求从公共/个人模型库选型：分类→搜索→过滤→对比→推荐→生成替换清单。",
)
def prompt_select_component(sub_type_id: str, requirement: str) -> list[dict[str, Any]]:
    """器件选型工作流模板。

    Args:
        sub_type_id: 模型子类 ID（如 "61"）。
        requirement: 选型需求描述（频率/增益/NF 等）。
    """
    return [{"role": "user", "content": (
        f"请按需求「{requirement}」从模型库选型（子类 {sub_type_id}）。\n\n"
        "步骤：\n"
        "1. 调用 `get_model_category_params(categories_only=true)` 确认子类 ID（精简返回，避免大结果）。\n"
        "2. 若需求含参数约束（增益/频率/NF 等）：调 `get_model_category_params()` 取该子类参数 id（如 gain/min_freq），\n"
        "   构造成 filters（例 [{\"key\":\"gain\",\"min\":20}]）；无约束则 filters 传空。\n"
        "3. 依次调用 `search_public_models` 与 `search_personal_models`（sub_type 相同、带 filters；EDA gRPC 串行执行，不要并行）。\n"
        "4. 对候选调 `get_components_static_params`（把搜索结果里的 `model_uuid` 值作为 `original_uuids` 数组参数传入；\n"
        "   二者值相同仅参数名不同，选型结果里没有 `alternative_model_id` 字段）查厂商/尺寸/封装。\n"
        "5. 输出对比表：型号 | 厂商 | 关键参数 | 尺寸 | 来源(公共/个人)。\n"
        "6. 给出推荐及理由，不编造参数。\n"
        "7. 若用户确认要替换到工程：先调 `list_simulation_components(summary_only=true)` "
        "拿工程现有器件的三列（不编造，以实际返回为准）：\n"
        "   `component_type→original_model_type`、`instance_name→original_model_name`、`model_id→original_model_id`。\n"
        "8. 生成 CSV（列 original_model_type/name/id + alternative_model_type/name/id），"
        "调用 `replace_models_from_csv` 应用到工程。\n"
        "   未确认前只输出推荐，不执行替换。"
    )}]