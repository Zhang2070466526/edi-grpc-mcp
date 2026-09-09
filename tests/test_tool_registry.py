"""测试工具注册完整性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_all_tools_registered():
    from start_servers import mcp
    tools = list(mcp._tool_manager._tools.keys())
    required = [
        "list_epp_projects", "create_project", "open_edi_project", "close_edi_project",
        "get_project_summary",
        "simulate_project", "simulate_netlist", "simulate_netlist_with_ads",
        "simulate_anti_burnout",
        "compare_simulation_results",
        "start_simulation_async", "get_simulation_async_status", "get_simulation_async_result",
        "list_eda_tasks",
        "export_project_netlist", "capture_schematic", "get_signal_chain",
        "replace_models_from_csv", "launch_edi", "get_service_status",
        "get_service_logs",
        "list_result_curves", "turbocharts_convert",
        "show_image", "analyze_image", "analyze_variables",
        "generate_simulation_report",
        "open_document",
        "get_simulation_component_schema",
        "list_simulation_components", "create_simulation_component",
        "update_simulation_component", "delete_simulation_component",
        "generate_schematic_from_netlist", "set_component_active_state",
        "replace_port_component", "attach_out_component",
        "replace_schematic_from_file",
        "list_schematic_components", "get_schematic_component_info",
        "get_components_static_params", "batch_query_component",
        "get_model_category_params", "search_public_models", "search_personal_models",
        "list_ideal_components", "create_workspace", "switch_workspace",
        "get_current_workspace",
        "load_performance_component_from_mms", "add_performance_component",
        "add_ideal_component", "clear_schematic", "add_wire",
        "search_schematic_from_public_library", "search_schematic_from_personal_library",
        "use_schematic_from_library_create_project", "use_schematic_from_library_import",
        "export_schematic_components_to_csv",
        "open_hfss_project", "close_hfss_project", "launch_aedt", "get_hfss_project_info",
        "start_hfss_analysis_async", "get_hfss_analysis_status",
        "cst_solve_async", "cst_solve_query", "cst_export_snp",
        "cst_export_farfield", "cst_export_farfield_query",
        "tr_get_workflow_state", "tr_set_workflow_plan", "tr_get_simulation_capabilities",
        "tr_read_netlist", "tr_find_paths", "tr_restore_schematic",
        "tr_modify_netlist", "tr_execute_simulation_plan", "tr_run_simulation",
        "tr_parse_raw", "tr_read_guide", "tr_get_project_netlist",
        "tr_query_schematic_components", "tr_sync_project_components",
        "tr_prepare_report", "tr_generate_document", "tr_query_components",
    ]
    # copy_image_to_workspace is conditional
    from servers.multimodal_vision import OPENCLAW_WORKSPACE_PATH
    if OPENCLAW_WORKSPACE_PATH is not None:
        required.append("copy_image_to_workspace")
    assert len(tools) == len(required), f"expected {len(required)} tools, got {len(tools)} ({sorted(tools)})"
    for name in required:
        assert name in tools, f"missing tool: {name}"


def test_grpc_timeout_limits():
    from servers.eda.grpc_client import call_grpc
    try:
        call_grpc(1, {}, 99999, max_timeout_seconds=300)
        assert False
    except ValueError:
        pass


def test_single_instance_check():
    import socket
    s = socket.socket()
    try:
        result = s.connect_ex(("127.0.0.1", 19998))
        assert result != 0  # should not be listening
    finally:
        s.close()


def test_chat_tool_map_consistency():
    """CHAT_TOOL_MAP 的每个工具都已在 MCP 注册，且与 CHAT_TOOLS_SCHEMA 一致。"""
    from start_servers import mcp
    mcp_tools = set(t.name for t in mcp._tool_manager._tools.values())

    from servers.chat.service import CHAT_TOOL_MAP, CHAT_TOOLS_SCHEMA
    schema_names = set(t["function"]["name"] for t in CHAT_TOOLS_SCHEMA)
    map_names = set(CHAT_TOOL_MAP.keys())

    # MAP ⊂ MCP
    only_in_map = map_names - mcp_tools
    assert not only_in_map, f"Chat MAP 中有未在 MCP 注册的工具: {sorted(only_in_map)}"

    # MAP ↔ SCHEMA 必须一致
    only_in_map_vs_schema = map_names - schema_names
    only_in_schema = schema_names - map_names
    assert not only_in_map_vs_schema, f"MAP 中有 SCHEMA 没有的工具: {sorted(only_in_map_vs_schema)}"
    assert not only_in_schema, f"SCHEMA 中有 MAP 没有的工具: {sorted(only_in_schema)}"


def test_mcp_only_tools_are_expected():
    """MCP 独有的工具应该是已知的非 Chat 工具（同步仿真、ANSYS COM 等）。"""
    from start_servers import mcp
    mcp_tools = set(t.name for t in mcp._tool_manager._tools.values())

    from servers.chat.service import CHAT_TOOL_MAP, _ensure_chat_tools
    _ensure_chat_tools()  # 刷新，确保 CHAT_TOOL_MAP 包含所有已注册工具（含后注册的 CST 等）
    chat_tools = set(CHAT_TOOL_MAP.keys())

    mcp_only = mcp_tools - chat_tools
    expected = {
        "simulate_project",           # 同步阻塞
        "simulate_netlist",           # 需要本地网表文件
        "simulate_netlist_with_ads",  # 需要 ADS 安装
        "open_hfss_project",          # ANSYS COM 依赖
        "close_hfss_project",
        "launch_aedt",
        "get_hfss_project_info",
        "start_hfss_analysis_async",
        "get_hfss_analysis_status",
        "tr_restore_schematic",       # TR 后台工具，同步阻塞
        "tr_execute_simulation_plan",
        "tr_run_simulation",
        "tr_parse_raw",
        "tr_sync_project_components",
        "tr_generate_document",
    }
    unexpected = mcp_only - expected
    assert not unexpected, (
        f"MCP 独有的非预期工具（需要在 Chat 中注册或添加到 expected 列表）: "
        f"{sorted(unexpected)}"
    )


def test_tool_count_dynamic():
    """工具数量应为动态统计，不为零即可。"""
    from start_servers import mcp
    count = len(mcp._tool_manager._tools)
    assert count > 30, f"工具数量异常少: {count}"


def test_destructive_tools_in_map():
    """破坏性工具必须同时在 MCP 和 Chat MAP 中。"""
    from start_servers import mcp
    mcp_tools = set(t.name for t in mcp._tool_manager._tools.values())

    from servers.chat.service import _DESTRUCTIVE_CHAT_TOOLS
    missing = _DESTRUCTIVE_CHAT_TOOLS - mcp_tools
    assert not missing, f"破坏性工具未在 MCP 注册: {sorted(missing)}"


def test_all_tool_params_have_description():
    """每个工具 inputSchema 的每个参数都应有 description（由 schema_descriptions 注入）。

    防止以后新增工具时忘了写 docstring 的 Args: 段，导致参数 description 缺失、
    标准 MCP 客户端「看不懂参数」。
    """
    from start_servers import mcp
    missing: dict[str, list[str]] = {}
    for name, tool in sorted(mcp._tool_manager._tools.items()):
        params = getattr(tool, "parameters", {})
        if not isinstance(params, dict):
            continue
        properties = params.get("properties", {})
        if not isinstance(properties, dict):
            continue
        bare = [
            p for p, f in properties.items()
            if isinstance(f, dict) and not f.get("description")
        ]
        if bare:
            missing[name] = bare
    assert not missing, (
        f"以下工具的参数缺少 inputSchema description（请在 docstring 补 Args: 段）: {missing}"
    )


def test_tool_descriptions_are_concise():
    """工具 description 应只含首行摘要，不含 用法/Args/Returns 整段。

    防止 description 被塞进全文 docstring，导致 LLM 工具选择上下文膨胀、语义搜索噪声大。
    """
    from start_servers import mcp
    bad: dict[str, str] = {}
    for name, tool in sorted(mcp._tool_manager._tools.items()):
        desc = getattr(tool, "description", "") or ""
        if "\n\n" in desc or any(kw in desc for kw in ("用法:", "Args:", "Returns:")):
            bad[name] = desc[:60]
    assert not bad, f"以下工具 description 未截断（仍含多段或用法/Args/Returns）: {bad}"


if __name__ == "__main__":
    test_all_tools_registered()
    test_grpc_timeout_limits()
    test_single_instance_check()
    test_chat_tool_map_consistency()
    test_mcp_only_tools_are_expected()
    test_tool_count_dynamic()
    test_destructive_tools_in_map()
    test_all_tool_params_have_description()
    print("test_tool_registry.py: all passed")
