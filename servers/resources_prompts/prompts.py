"""MCP Prompts — 可复用工作流模板，用户通过 prompts/get 主动选择。

8 个 Prompt：
  inspect_edi_project       — 只读检查：概览→变量→器件→仿真配置
  run_and_review_simulation — 异步仿真+日志分析，含轮询限制
  configure_simulation_component — Schema→参数映射→确认→创建/更新
  create_simulation_report  — 查询工程→生成曲线→渲染 PDF/DOCX
  troubleshoot_edi_error    — 错误诊断：读 error-codes → 查服务状态 → 建议动作
  assess_anti_burnout       — 抗烧毁评估：仿真→按裕量排序→结论
  select_component          — 器件选型：分类→搜索→对比→推荐→替换
  analyze_signal_chain      — 信号链分析：追踪链路→逐级说明
"""

from __future__ import annotations

from pathlib import Path as _Path
from typing import Any

from servers import mcp


@mcp.prompt(
    name="inspect_edi_project",
    title="检查 EDI 工程",
    description="查看工程基本信息、器件统计、变量配置和仿真设置。不修改工程，不启动仿真。",
)
def prompt_inspect_edi_project(
        project_path: str,
        detail_level: str = "standard",
) -> list[dict[str, Any]]:
    """检查 EDI 工程的工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        detail_level: summary（概览）/ standard（标准）/ full（完整）。
    """
    detail = detail_level.lower().strip() or "standard"
    if detail not in ("summary", "standard", "full"):
        detail = "standard"

    depth = {
        "summary": "只输出基本信息，不展开器件列表",
        "standard": "包含器件统计和仿真配置",
        "full": "包含完整器件列表和参数",
    }

    return [
        {
            "role": "user",
            "content": (
                f"请检查 EDI 工程：{project_path}\n\n"
                f"检查深度：{detail}（{depth.get(detail, '')}）\n\n"
                "步骤：\n"
                "1. 调用 `get_project_summary` 获取工程概览。\n"
                "2. 调用 `analyze_variables` 查看变量定义和 Sweep 配置。\n"
                "3. standard/full 时调用 `list_simulation_components` 查看器件分布和仿真器件配置。\n"
                "4. 汇总输出：原理图数量、器件统计、变量与 Sweep、仿真配置、已有的问题。\n\n"
                "注意：只读操作，不修改工程，不启动仿真。"
            ),
        },
    ]


@mcp.prompt(
    name="run_and_review_simulation",
    title="执行并检查仿真",
    description="对工程执行仿真并分析结果。默认异步执行，支持日志分析。",
)
def prompt_run_and_review_simulation(
        project_path: str,
        execution_mode: str = "async",
        analyze_log: bool = True,
) -> list[dict[str, Any]]:
    """仿真执行与分析工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        execution_mode: async（异步，推荐）或 sync（同步）。
        analyze_log: 是否分析 ads_output 日志。
    """
    mode = execution_mode.lower().strip() or "async"
    if mode not in ("async", "sync"):
        mode = "async"

    steps: list[str] = [
        "1. 调用 `get_project_summary` 确认工程中有仿真器件。",
    ]
    if mode == "async":
        steps += [
            "2. 调用 `start_simulation_async` 启动仿真，获取 task_id。",
            "3. 启动后最多立即查询一次 `get_simulation_async_status`。如果仍在运行，返回 task_id 告知用户稍后查询。不要紧密轮询（间隔不少于 10 秒），单次对话最多自动查询 3 次。",
            "4. 完成后调用 `get_simulation_async_result` 获取完整结果和日志。",
        ]
    else:
        steps += [
            "2. 调用 `simulate_project` 等待仿真完成。",
        ]

    if analyze_log:
        steps += [
            "5. 分析 `ads_output` 日志：是否成功、有无 error/warning、result_path 是否生成、log_complete 是否为 true。",
            "6. 输出结论：成功/失败/未知，以及日志关键行。",
        ]
    else:
        steps += [
            "5. 输出结论：成功/失败/未知，result_path 路径。",
        ]

    steps += [
        "",
        "重要约束：",
        "- TIMEOUT 或 STREAM_DISCONNECTED 时：明确告知用户任务结果未知，禁止自动重试。",
        "- 不要为了查询日志而启动新的仿真任务。",
        "- 如用户需要图表，再调用 `turbocharts_convert` 或 `compare_simulation_results`。",
    ]

    return [
        {
            "role": "user",
            "content": (
                    f"请对工程 {project_path} 执行仿真并分析结果。\n"
                    f"执行方式：{mode}\n"
                    f"分析日志：{'是' if analyze_log else '否'}\n\n"
                    + "\n".join(steps)
            ),
        },
    ]


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
    name="create_simulation_report",
    title="生成仿真报告",
    description="协调多个工具完成仿真报告：查询工程→确认结果→生成曲线→整理数据→渲染 PDF/DOCX。",
)
def prompt_create_simulation_report(
    project_path: str,
    output_path: str,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """生成仿真报告的完整工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        output_path: 输出文件绝对路径（.pdf 或 .docx）。
        overwrite: 输出文件已存在时是否覆盖。
    """
    ext = _Path(output_path).suffix.lower() if output_path else ".pdf"
    file_type = ext.lstrip(".")

    steps = [
        f"1. 确认输出路径：`{output_path or '（请提供）'}`（{file_type.upper()} 格式）。",
        "2. 调用 `get_project_summary` 获取工程基本信息作为报告封面和简介素材。",
        "3. 查询已有仿真结果（`get_simulation_async_result` 或 `list_eda_tasks`），不自动重新仿真。",
        "   没有结果时询问用户是否执行仿真。",
        "4. 调用 `list_result_curves` 获取 RAW 中实际可用的曲线名。",
        "5. 根据用户需求选择关键曲线，调用 `turbocharts_convert` 生成曲线图片。",
        "6. 如需拓扑图，调用 `capture_schematic` 截取原理图。",
        "7. 整理电参数表（spec_table）：只填入有真实测量值的指标，无要求值时结果列填'未判定'。",
        "8. 整理器件选型表（components）：type/model/manufacturer/specs 四项均为字符串，不得猜测。",
        "9. 收集 description（产品简介）和 conclusion（结论文字）。",
        f"10. 调用 `generate_simulation_report` 渲染报告：output_path=\"{output_path or '（请提供）'}\"，overwrite={'true' if overwrite else 'false'}",
        "",
        "重要约束：",
        "- 不要自动启动新的仿真，除非用户明确要求。",
        "- log_complete=false 时不能断言日志完整。",
        "- TIMEOUT/STREAM_DISCONNECTED 时禁止自动重试，也不写成功结论。",
        "- 器件厂家和规格不得根据型号名称猜测。",
        "- 只有同时具备测量值和判定要求时才能写'合格/不合格'。",
    ]

    return [{"role": "user", "content": "\n".join(steps)}]


@mcp.prompt(
    name="troubleshoot_edi_error",
    title="诊断 EDI 调用错误",
    description="根据 gRPC 返回的 status/error_code 查错误码词典、检查服务状态，给出排查建议。",
)
def prompt_troubleshoot_edi_error(
    status: str,
    error_code: str = "",
) -> list[dict[str, Any]]:
    """诊断 EDI 工具调用失败的工作流模板。

    Args:
        status: gRPC 返回的 status（如 TIMEOUT/STREAM_DISCONNECTED/GRPC_UNAVAILABLE）。
        error_code: 工具的 error_code（如 FILE_NOT_FOUND/INVALID_PARAMETERS）。
    """
    steps = [
        f"诊断目标：status={status}" + (f", error_code={error_code}" if error_code else ""),
        "",
        "步骤：",
        "1. 读取 `edi://reference/error-codes` 资源，查找该状态码的含义和建议动作。",
        "2. 调用 `get_service_status` 检查 gRPC 通道是否健康、是否有任务在排队。",
    ]

    if status in ("TIMEOUT", "STREAM_DISCONNECTED"):
        steps += [
            "3. 不要自动重试。告知用户：EDI 任务结果未知（outcome_known=false）。",
            "4. 建议：确认 EDI 是否仍在运行，查看 ads_output 尾部有无报错。",
        ]
    elif status == "GRPC_UNAVAILABLE":
        steps += [
            "3. 确认 EDI 是否已启动、gRPC 地址端口是否正确。",
            "4. 若 EDI 已掉线，请手动启动 EDI 软件后重试。",
        ]
    elif status in ("QUEUE_TIMEOUT",):
        steps += [
            "3. 调用 `list_eda_tasks` 查看是否有长任务卡住执行槽位。",
            "4. 如无任务运行，稍后重试即可。",
        ]
    elif status == "REJECTED":
        steps += [
            "3. 查看返回的 message 了解拒绝原因（通常是参数错误或权限问题）。",
            "4. 修正参数后重试，不要用相同参数反复调用。",
        ]
    else:
        steps += [
            "3. 根据 error_code 判断是 MCP 层校验错误还是 EDI 业务错误。",
            "4. MCP 层问题（FILE_NOT_FOUND/INVALID_PARAMETERS 等）修正参数重试。",
        ]

    return [{"role": "user", "content": "\n".join(steps)}]


@mcp.prompt(
    name="assess_anti_burnout",
    title="评估器件抗烧毁风险",
    description="对工程执行抗烧毁仿真并按功率裕量排序输出结论。",
)
def prompt_assess_anti_burnout(project_path: str) -> list[dict[str, Any]]:
    """抗烧毁评估工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
    """
    return [{"role": "user", "content": (
        f"请对工程 {project_path} 执行抗烧毁评估。\n\n"
        "步骤：\n"
        "1. 调用 `simulate_anti_burnout` 执行评估（timeout 默认 600s）。\n"
        "2. 若返回『抗烧毁仿真网表处理失败』：提示用户检查 PORT1 功率是否异常（>40dBm 会导致仿真溢出），"
        "或链路是否为多通道合路（该场景服务端可能不支持）。\n"
        "3. 成功时解析 results：按 (simulated_input_power - max_input_power) 裕量升序排列，"
        "标注最接近限值的器件。\n"
        "4. 汇总表格：器件 | 仿真输入功率 | 最大允许 | 裕量 | 判定。\n\n"
        "重要：max_input_power 单位可能是 dBm 或 W，比较前统一换算成 dBm。"
    )}]


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
        "1. 调用 `get_model_category_params(categories_only=true)` 确认子类（精简返回，避免大结果）。\n"
        "2. 依次调用 `search_public_models` 与 `search_personal_models`（sub_type 相同；"
        "   EDA gRPC 串行执行，不要并行调用）。\n"
        "3. 从需求提取过滤条件（频率/增益/NF 等），在结果中筛选。\n"
        "4. 对候选调 `get_components_static_params`（把搜索结果里的 `model_uuid` 值作为 `original_uuid` 参数传入；\n"
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


@mcp.prompt(
    name="analyze_signal_chain",
    title="信号链分析",
    description="追踪工程信号链路并解释各级器件作用与功率流。",
)
def prompt_analyze_signal_chain(project_path: str, start: str = "PORT1") -> list[dict[str, Any]]:
    """信号链分析工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        start: 起始器件实例名（默认 PORT1）。
    """
    return [{"role": "user", "content": (
        f"请分析工程 {project_path} 从 {start} 开始的信号链路。\n\n"
        "步骤：\n"
        "0. 若工程近期跑过 simulate_anti_burnout：先调 `export_project_netlist` 刷新干净网表"
        "（抗烧毁流程会把 PowerPin 虚拟节点写进本地 netlist.log，污染追踪源）。\n"
        "1. 调用 `get_signal_chain`（project_path, start_component=start）获取链路。\n"
        "2. 说明每级器件作用（LNA/衰减器/移相器/功放/功分器）。\n"
        "3. 若结果含 warning（PowerPin 污染），提示用户先重新导出网表再追踪。\n"
        "4. 有仿真结果时结合 result.raw 说明各级实际功率。\n"
        "5. 输出：文本链路图 + 各级说明表。"
    )}]
