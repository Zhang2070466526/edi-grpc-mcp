"""MCP 工具注册中心 — 导入即可自动注册所有 @mcp.tool() 工具。

权威工具清单由 scripts/gen_tool_index.py 生成 → docs/TOOL_INDEX.md（勿手改清单）。
权威数量见 /ready 的 tool_count。
"""

from __future__ import annotations

import sys

from starlette.responses import PlainTextResponse

from servers import mcp  # noqa: E402 — 全局 MCP 实例
from servers.metrics import get_tool_metrics
from servers.settings import get_settings
from servers.utils import server_uptime_seconds

# 导入工具模块即可触发 @mcp.tool() 装饰器注册
import servers.eda.project_manage       # noqa: F401
import servers.eda.simulation            # noqa: F401
import servers.eda.simulation_components # noqa: F401
import servers.eda.design_export         # noqa: F401
import servers.eda.signal_chain          # noqa: F401
import servers.eda.model_replace         # noqa: F401
import servers.eda.model_library          # noqa: F401
import servers.eda.soft_ip                # noqa: F401
import servers.eda.workspace_ops         # noqa: F401
import servers.eda.schematic_ops         # noqa: F401
import servers.eda.edi_launcher          # noqa: F401
import servers.turbocharts.compare_results  # noqa: F401
import servers.turbocharts.convert_raw   # noqa: F401
import servers.turbocharts.resource      # noqa: F401 — edi://reference/turbocharts-guide（RAW 转图说明）
import servers.ansys.project_manage       # noqa: F401
import servers.ansys.run_analysis         # noqa: F401
import servers.laspcem                    # noqa: F401 — launch/open/solve/export_snp/report/get_setup
import servers.multimodal_vision          # noqa: F401 — show_image + analyze + open_document
import servers.mcp_arg_guard            # noqa: F401 — 缺参/参数名拼错回统一信封（包装 ToolManager.call_tool）
import servers.cst                        # noqa: F401 — cst_solve + cst_export_snp
import servers.microwave                # noqa: F401 — calculate_transline + design_wilkinson + filter_order_* + wilkinson_marchand_order
import servers.rf_analysis                        # noqa: F401 — analyze_mixer_spur + calculate_rf_dynamic_range
import servers.simulation_tool           # noqa: F401 — export_simulation_report + run_tr_simulation + run_mixer_simulation + draw_chart
import servers.describe_tool             # noqa: F401 — describe_tool（工具自描述：完整参数 + 可抄示例）

# Resources & Prompts
import servers.resources_prompts      # noqa: F401 — @mcp.resource() / @mcp.prompt()

# 工具全部注册完成后，把各工具 docstring 的 Args: 段注入 inputSchema 参数 description
# （FastMCP 默认不解析 docstring，此处统一补上，见 servers/schema_descriptions.py）
from servers.schema_descriptions import (
    inject_tool_descriptions,
    trim_tool_descriptions,
    inline_tool_schemas,
)  # noqa: E402
inject_tool_descriptions(mcp)
trim_tool_descriptions(mcp)
# 嵌套模型（如 run_tr_simulation.links 的 TrLink）会被 pydantic 生成成 $ref + $defs；
# 扁平成自包含 schema，客户端/模型不必跟 $ref 就能看到元素字段。
inline_tool_schemas(mcp)


# ── 内部工具过滤（对智能体彻底不可见，代码保留、可逆）──────────────────────────
# 这里列的是「依赖 EDI gRPC(127.0.0.1:50055)」的工具，即 grpc接口调用.md 里
# ExternalCall 任务类型的 MCP 包装。开关 MCP_HIDE_INTERNAL_TOOLS=1 时下架，
# 智能体在 tools/list、describe_tool（含相似名建议）、Chat、按名调用都感知不到。
# 名单去掉名字（或开关回 0）重启即恢复，工具函数本体不删除。
_INTERNAL_TOOLS: frozenset[str] = frozenset({
    # 工程管理
    "create_project", "open_edi_project", "close_edi_project",
    "list_schematic_components", "get_schematic_component_info",
    "get_components_static_params", "batch_query_component_rf_params",
    # 仿真
    "simulate_netlist", "simulate_anti_burnout",
    "start_simulation_async",
    # 仿真器件
    "update_simulation_component",
    "replace_port_component", "delete_component_from_schematic",
    "set_component_active_state",
    "replace_schematic_from_file", "attach_out_component",
    "update_schematic_components_transform",
    # 网表/截图/CSV
    "export_project_netlist", "capture_schematic", "export_schematic_components_to_csv",
    # 信号链（本地网表优先，gRPC 兜底）
    "get_signal_chain",
    # 模型替换 / 模型库 / 软 IP
    "replace_models_from_csv", "get_model_category_params",
    "search_public_models", "search_personal_models",
    "add_performance_component", "design_schematic_topology", "design_chip",
    # 工作区 / 原理图扩展
    "create_workspace", "switch_workspace", "get_current_workspace_path",
    "get_current_workspace_summary",
    "list_ideal_components", "add_ideal_component", "clear_schematic", "add_wire",
    # 独立 exe 工具（由 EDI 安装目录提供）：TR/混频需提权 + ADS，网表寻路纯本地但同属 EDI 组件
    "run_tr_simulation", "run_mixer_simulation", "find_netlist_paths",
    # 其它 exe 工具 + EDI 启动/日志诊断
    "draw_chart", "export_simulation_report", "turbocharts_convert",
    "get_service_logs", "launch_edi",
    # gRPC 通道状态 + 异步仿真查询（start_simulation_async 的查进度/取结果配套）
    "get_service_status", "get_simulation_async_status", "get_simulation_async_result",
    "list_eda_tasks",
    # RAW 曲线查询/对比（turbocharts 相关）
    "list_result_curves", "compare_simulation_results",
})


# 与 gRPC 通信相关的 prompt（引导调用上面这些 gRPC 工具的流程模板）
_INTERNAL_PROMPTS: frozenset[str] = frozenset({
    "configure_simulation_component", "select_component",
    "analyze_signal_chain", "run_and_review_simulation", "assess_anti_burnout",
    "create_simulation_report", "troubleshoot_edi_error",
})

# 与 gRPC 通信相关的 resource（错误码 / 仿真器件参数 / 操作规则 / 服务状态 / 工程目录）
# 以及配套已下架工具的指南（TR 仿真 / RAW 转图）
_INTERNAL_RESOURCES: frozenset[str] = frozenset({
    "edi://reference/error-codes",
    "edi://reference/simulation-components",
    "edi://reference/operation-guide",
    "edi://service/status",
    "edi://service/overview",
    "edi://projects",
    "edi://reference/tr-simulation-guide",
    "edi://reference/turbocharts-guide",
})

#: 无依赖的本地纯计算工具（微波计算 / 射频链路计算）。mode=2 时与内部工具一起保留。
_CALC_TOOLS: frozenset[str] = frozenset({
    "calculate_transline", "design_wilkinson",
    "filter_order_lowpass", "filter_order_highpass",
    "filter_order_bandpass", "filter_order_bandstop",
    "wilkinson_marchand_order",
    "analyze_mixer_spur", "calculate_rf_dynamic_range",
})

#: mode=3 下架：软 IP（design_chip）+ LASPCEM 电磁仿真工具。
_MODE3_TOOLS: frozenset[str] = frozenset({
    "design_chip",
    "launch_laspcem", "open_laspcem_project", "import_aedt_as_sem",
    "run_laspcem_solve_async", "laspcem_solve_query",
    "laspcem_task_cancel", "laspcem_task_list",
    "export_laspcem_snp", "build_laspcem_report", "get_laspcem_setup",
    "laspcem_cleanup_orphans",
})

#: exe 打包版（sys.frozen）**无条件**下架的工具：analyze_image 会上传第三方图片，交付包不暴露。
_EXE_HIDDEN_TOOLS: frozenset[str] = frozenset({
    "analyze_image",
})


def _hide_exe_hidden_tools() -> None:
    """exe 打包版（PyInstaller frozen）无条件下架 _EXE_HIDDEN_TOOLS，与 mode 无关。"""
    if getattr(sys, "frozen", False):
        for name in _EXE_HIDDEN_TOOLS:
            mcp._tool_manager._tools.pop(name, None)


def _hide_internal_tools() -> None:
    """按开关下架工具/prompt/resource（从注册表 pop，代码保留、可逆）。

    mode = get_settings().mcp_hide_internal_tools：
      0 = 全量暴露（不动）；
      1 = 下架内部工具（_INTERNAL_TOOLS 黑名单 + 配套 prompt/resource）；
      2 = 只保留「内部工具 + 无依赖计算工具」，其余（ANSYS/CST/LASPCEM/图片/文档等）全下架；
      3 = 下架软 IP（design_chip）+ LASPCEM 电磁仿真工具。
    """
    _hide_exe_hidden_tools()
    mode = get_settings().mcp_hide_internal_tools
    if mode == 0:
        return

    if mode == 1:
        for name in _INTERNAL_TOOLS:
            mcp._tool_manager._tools.pop(name, None)
        for name in _INTERNAL_PROMPTS:
            mcp._prompt_manager._prompts.pop(name, None)
        for name in _INTERNAL_RESOURCES:
            mcp._resource_manager._resources.pop(name, None)
        return

    if mode == 2:  # 白名单：只保留内部工具 + 无依赖计算工具（+ describe_tool 元工具）
        keep_tools = _INTERNAL_TOOLS | _CALC_TOOLS | {"describe_tool"}
        for name in list(mcp._tool_manager._tools):
            if name not in keep_tools:
                mcp._tool_manager._tools.pop(name, None)
        for name in list(mcp._prompt_manager._prompts):
            if name not in _INTERNAL_PROMPTS:
                mcp._prompt_manager._prompts.pop(name, None)
        for name in list(mcp._resource_manager._resources):
            if name not in _INTERNAL_RESOURCES:
                mcp._resource_manager._resources.pop(name, None)
        return

    if mode == 3:  # 下架软 IP（design_chip）+ LASPCEM 电磁仿真工具
        for name in _MODE3_TOOLS:
            mcp._tool_manager._tools.pop(name, None)
        return

    # 其它未知值：保守按全量暴露处理（int 字段，正常到不了这里）


_hide_internal_tools()


# ── 工具 annotations：给客户端的「只读 / 破坏性」安全标签 ────────────────────
# 为什么要有它（2026-11-17 实测）：MCP 客户端（Claude Desktop / Cursor / VS Code）
# 靠 annotations 决定「调用前是否弹确认」「列表里是否标红」，它们**不解析** description
# 里的 ⚠️ 文字。当时 101 个工具的 annotations 全为空 ⇒ clear_schematic 和
# get_service_status 在客户端眼里完全一样，唯一的拦截点没了。
# 分类口径：readOnlyHint = 不修改工程、不写文件、不启停进程；
#           destructiveHint = 会删除/覆盖已有数据、或不可逆改变工程状态。
# 名单显式列出（不用名字前缀猜），新增工具时请同步这里。
_READONLY_TOOLS: frozenset[str] = frozenset({
    "analyze_image", "analyze_mixer_spur", "batch_query_component_rf_params",
    "calculate_rf_dynamic_range", "calculate_transline",
    "cst_export_farfield_query", "cst_export_snp_query", "cst_get_model_info",
    "cst_list_results", "cst_solve_query", "cst_task_list", "describe_tool",
    "design_wilkinson", "fetch_artifact",
    "filter_order_bandpass", "filter_order_bandstop", "filter_order_highpass",
    "filter_order_lowpass", "get_components_static_params", "get_current_workspace_path",
    "get_current_workspace_summary",
    "get_hfss_analysis_status", "get_hfss_project_info", "get_laspcem_setup",
    "get_model_category_params", "get_project_summary",
    "get_schematic_component_info", "get_service_logs", "get_service_status",
    "get_signal_chain", "get_simulation_async_result", "get_simulation_async_status",
    "get_simulation_component_schema", "laspcem_solve_query", "laspcem_task_list",
    "list_eda_tasks", "list_epp_projects", "list_ideal_components",
    "list_result_curves", "list_schematic_components",
    "open_document", "search_personal_models",
    "search_public_models", "show_image", "wilkinson_marchand_order",
})

#: 会删除/覆盖已有数据、或不可逆改变工程状态的工具 ⇒ destructiveHint=True。
#: 比「描述里带 ⚠️ 的 9 个」多两个：close_laspcem_project（未保存改动会丢）、
#: laspcem_task_cancel（终止运行中的求解）—— 语义破坏但当初没打 ⚠️。
_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "clear_schematic", "close_edi_project", "close_hfss_project",
    "quit_aedt",
    "close_laspcem_project", "cst_task_cancel", "delete_component_from_schematic",
    "laspcem_task_cancel", "replace_models_from_csv", "replace_port_component",
    "replace_schematic_from_file", "switch_workspace",
})

#: 破坏性工具的中文短名（客户端列表里显示这个，不显示英文工具名）。其余工具 title=name。
_TOOL_TITLES: dict[str, str] = {
    "clear_schematic": "清空原理图",
    "close_edi_project": "关闭 EDI 工程",
    "close_hfss_project": "关闭 AEDT 工程",
    "quit_aedt": "退出 AEDT 程序",
    "close_laspcem_project": "关闭 LASPCEM 工程",
    "cst_task_cancel": "取消 CST 任务",
    "delete_component_from_schematic": "删除器件",
    "laspcem_task_cancel": "取消 LASPCEM 任务",
    "replace_models_from_csv": "批量替换模型",
    "replace_port_component": "替换端口器件",
    "replace_schematic_from_file": "整图替换原理图",
    "switch_workspace": "切换工作区",
}


def _apply_tool_annotations() -> None:
    """给每个工具补 annotations（在 _hide_internal_tools 之后调用，只处理留下的工具）。

    idempotentHint 只在「重复执行结果一致」时才置 True：只读查询、清空/删除/关闭这类
    幂等破坏性操作给 True；新建/添加/跑仿真这类重复执行会产生新对象或新任务的一律 False
    （客户端据此决定超时后能否安全自动重试，不能乱承诺）。
    """
    from mcp.types import ToolAnnotations

    for name, tool in mcp._tool_manager._tools.items():
        readonly = name in _READONLY_TOOLS
        destructive = (not readonly) and (name in _DESTRUCTIVE_TOOLS)
        tool.annotations = ToolAnnotations(
            title=_TOOL_TITLES.get(name, name),
            readOnlyHint=readonly,
            destructiveHint=destructive,
            idempotentHint=readonly or destructive,
            openWorldHint=False,  # 全部是本机 EDA 工具，不访问外部世界
        )


_apply_tool_annotations()

# Web 路由
from servers.chat.routes import (  # noqa: E402
    ui_page, health_check, chat_endpoint, tool_list, upload_file,
    tool_browser_page, tool_browser_api,
)
from servers.multimodal_vision import serve_image  # noqa: E402
from servers.multimodal_vision import serve_document  # noqa: E402


async def metrics_endpoint(request):
    """GET /metrics — 输出 Prometheus 格式的运行时指标。"""
    from servers.eda.simulation import sim_task_count

    metrics = get_tool_metrics()
    lines = []

    # 工具调用总次数 / 失败次数 / 总耗时
    lines.append("# HELP edi_tool_calls_total 工具调用总次数")
    lines.append("# TYPE edi_tool_calls_total counter")
    for tool in sorted(metrics):
        lines.append(f'edi_tool_calls_total{{tool="{tool}"}} {metrics[tool]["count"]}')

    lines.append("# HELP edi_tool_errors_total 工具调用失败次数")
    lines.append("# TYPE edi_tool_errors_total counter")
    for tool in sorted(metrics):
        lines.append(f'edi_tool_errors_total{{tool="{tool}"}} {metrics[tool]["errors"]}')

    lines.append("# HELP edi_tool_duration_ms_sum 工具调用总耗时(毫秒)")
    lines.append("# TYPE edi_tool_duration_ms_sum counter")
    for tool in sorted(metrics):
        lines.append(f'edi_tool_duration_ms_sum{{tool="{tool}"}} {metrics[tool]["total_ms"]:.0f}')

    # 当前异步仿真任务数
    lines.append("# HELP edi_sim_tasks 当前异步仿真任务数")
    lines.append("# TYPE edi_sim_tasks gauge")
    lines.append(f"edi_sim_tasks {sim_task_count()}")

    # 服务运行时长
    lines.append("# HELP edi_uptime_seconds 服务运行时长(秒)")
    lines.append("# TYPE edi_uptime_seconds gauge")
    lines.append(f"edi_uptime_seconds {server_uptime_seconds():.0f}")

    return PlainTextResponse("\n".join(lines) + "\n")


mcp.custom_route("/", methods=["GET"])(ui_page)
mcp.custom_route("/ui", methods=["GET"])(ui_page)
mcp.custom_route("/health", methods=["GET"])(health_check)
mcp.custom_route("/chat", methods=["POST"])(chat_endpoint)
mcp.custom_route("/tools/list", methods=["GET"])(tool_list)
mcp.custom_route("/tools", methods=["GET"])(tool_browser_page)
mcp.custom_route("/tools/grouped", methods=["GET"])(tool_browser_api)
mcp.custom_route("/images/{token}", methods=["GET"])(serve_image)
mcp.custom_route("/documents/{token}", methods=["GET"])(serve_document)
mcp.custom_route("/upload", methods=["POST"])(upload_file)
mcp.custom_route("/metrics", methods=["GET"])(metrics_endpoint)

