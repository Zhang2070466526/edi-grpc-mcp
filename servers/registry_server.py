"""MCP 工具注册中心 — 导入即可自动注册所有 @mcp.tool() 工具。

权威工具清单由 scripts/gen_tool_index.py 生成 → docs/TOOL_INDEX.md（勿手改清单）。
权威数量见 /ready 的 tool_count。
"""

from __future__ import annotations

from starlette.responses import PlainTextResponse

from servers import mcp  # noqa: E402 — 全局 MCP 实例
from servers.metrics import get_tool_metrics
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
import servers.multimodal_vision          # noqa: F401 — show_image + analyze + open_document
import servers.report                     # noqa: F401 — generate_simulation_report
import servers.cst                        # noqa: F401 — cst_solve + cst_export_snp
import servers.tr_simulation           # noqa: F401 — 17 个 tr_* 工具 + workflow resource + run_tr_simulation prompt

# Resources & Prompts
import servers.resources_prompts      # noqa: F401 — @mcp.resource() / @mcp.prompt()

# 工具全部注册完成后，把各工具 docstring 的 Args: 段注入 inputSchema 参数 description
# （FastMCP 默认不解析 docstring，此处统一补上，见 servers/schema_descriptions.py）
from servers.schema_descriptions import inject_tool_descriptions, trim_tool_descriptions  # noqa: E402
inject_tool_descriptions(mcp)
trim_tool_descriptions(mcp)

# Web 路由
from servers.chat.routes import ui_page, health_check, chat_endpoint, tool_list, upload_file  # noqa: E402
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
mcp.custom_route("/images/{token}", methods=["GET"])(serve_image)
mcp.custom_route("/documents/{token}", methods=["GET"])(serve_document)
mcp.custom_route("/upload", methods=["POST"])(upload_file)
mcp.custom_route("/metrics", methods=["GET"])(metrics_endpoint)

