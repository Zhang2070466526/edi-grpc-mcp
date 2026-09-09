"""MCP 工具注册中心 — 导入即可自动注册所有 @mcp.tool() 工具。

═══════════════════════════════════════════════════════════
  已注册工具（数量由实际模块加载决定，配置工作区后 +1）：

  工程管理：
    list_epp_projects             扫描文件夹中的 .epp 工程
    create_project                创建新的 .epp 工程
    open_edi_project              打开 .epp 工程
    close_edi_project             关闭 .epp 工程
    get_project_summary           工程概览
    analyze_variables             分析变量定义和引用关系
    list_schematic_components     查询原理图全部器件（gRPC）
    get_schematic_component_info  按实例名查询器件完整信息
    get_components_static_params   查询器件固有参数（重量/尺寸/封装/厂商/成本）

  仿真：
    simulate_project              执行工程仿真（同步）
    start_simulation_async        启动异步仿真
    get_simulation_async_status   查询异步仿真状态
    get_simulation_async_result   获取异步仿真结果
    list_eda_tasks                列出异步仿真任务
    simulate_netlist              仿真网表，返回 RAW 结果
    simulate_netlist_with_ads     调用 ADS 仿真控制器
    simulate_anti_burnout         抗烧毁仿真与风险评估
    list_simulation_components    查询仿真器件
    get_simulation_component_schema  查询器件参数 schema
    create_simulation_component   新增仿真器件
    update_simulation_component   更新仿真器件参数
    delete_simulation_component   按实例名删除器件
    set_component_active_state    设置器件状态（NORMAL/DISABLED/SHORTED）
    generate_schematic_from_netlist  从网表生成原理图
    replace_port_component          替换端口器件类型
    attach_out_component            为器件引脚挂载 Out 器件
    replace_schematic_from_file        从 .ep 文件整体替换原理图

  分析 / 导出：
    export_project_netlist        查看/导出工程网表
    capture_schematic             截取原理图为图片
    export_schematic_components_to_csv  导出器件为 CSV（供模型替换）
    get_signal_chain              追踪信号链路（节点接力算法）

  模型库 / 原理图库：
    replace_models_from_csv       按 CSV 批量替换模型
    get_model_category_params     获取模型分类及参数列表
    search_public_models          查询公共模型库
    search_personal_models        查询个人模型库
    load_performance_component_from_mms  从 MMS 导入性能模型
    add_performance_component     放置模型库性能器件
    search_schematic_from_public_library      查询公共原理图库
    search_schematic_from_personal_library    查询个人原理图库
    use_schematic_from_library_create_project 用原理图库内容创建新工程
    use_schematic_from_library_import         用原理图库内容替换工程原理图

  启动：
    launch_edi                    启动 EDI 客户端
    get_service_status            返回 gRPC 通道状态、队列信息
    get_service_logs              读取 EDI 服务端日志并分析异常

  工作区：
    create_workspace              创建工作区（不自动切换）
    switch_workspace              设置下次启动使用的工作区
    get_current_workspace         查询当前实际加载的工作区目录

  原理图扩展：
    list_ideal_components         列出内置器件类型
    add_ideal_component           按指定坐标新增内置器件
    clear_schematic               清空原理图（破坏性，需确认）
    add_wire                      连接两个器件的指定引脚

  ANSYS：
    open_hfss_project             打开 .aedt HFSS 项目
    close_hfss_project            关闭 HFSS 项目
    launch_aedt                   启动 AEDT
    get_hfss_project_info         获取 HFSS 项目信息
    start_hfss_analysis_async     异步启动 HFSS 仿真
    get_hfss_analysis_status      查询 HFSS 仿真状态

  CST：
    cst_solve_async               异步求解 .cst 模型
    cst_solve_query               查询求解任务（进度+结果）
    cst_export_snp                导出 S 参数 (.sNp)
    cst_export_farfield           导出远场方向图（自动判断求解）
    cst_export_farfield_query     查询远场导出任务（进度+结果）

  图片：
    show_image                    读取本地图片，返回 MCP ImageContent
    analyze_image                 调用视觉模型分析图片内容
    copy_image_to_workspace       已隐藏（不使用 OpenClaw）

  文档：
    open_document                 打开本地文档（link 链接 / local 系统打开）

  报告：
    generate_simulation_report    生成本地仿真报告（PDF/DOCX）

  图表：
    list_result_curves            解析 RAW 返回可用曲线
    compare_simulation_results    多 RAW 结果对比叠图
    turbocharts_convert           ADS RAW → 曲线图 + CSV
═══════════════════════════════════════════════════════════
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
import servers.eda.workspace_ops         # noqa: F401
import servers.eda.schematic_ops         # noqa: F401
import servers.eda.edi_launcher          # noqa: F401
import servers.turbocharts.compare_results  # noqa: F401
import servers.turbocharts.convert_raw   # noqa: F401
import servers.ansys.project_manage       # noqa: F401
import servers.ansys.run_analysis         # noqa: F401
import servers.multimodal_vision          # noqa: F401 — show_image + copy + analyze + open_document
import servers.report                     # noqa: F401 — generate_simulation_report
import servers.cst                        # noqa: F401 — cst_solve + cst_export_snp

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

