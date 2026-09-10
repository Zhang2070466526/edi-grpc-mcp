"""SimulationAgent 集成工具包 —— TR 仿真 HTTP 工具封装（17 工具 + 1 Resource + 1 Prompt）。

client.py     HTTP 集成客户端：会话管理、稳定 request_id、调用与轮询
tools.py      17 个 tr_* 工具（@mcp.tool()）
resource.py   TR 仿真工作流 Resource（edi://integration/workflow）
prompts.py    TR 仿真工作流 Prompt（run_tr_simulation）
"""

from servers.tr_simulation.tools import (
    tr_get_workflow_state,
    tr_set_workflow_plan,
    tr_get_simulation_capabilities,
    tr_read_netlist,
    tr_find_paths,
    tr_restore_schematic,
    tr_modify_netlist,
    tr_execute_simulation_plan,
    tr_run_simulation,
    tr_parse_raw,
    tr_read_guide,
    tr_get_project_netlist,
    tr_query_schematic_components,
    tr_sync_project_components,
    tr_prepare_report,
    tr_generate_document,
    tr_query_components,
)
from servers.tr_simulation.resource import resource_tr_workflow  # noqa: F401
from servers.tr_simulation.prompts import prompt_run_tr_simulation  # noqa: F401

__all__ = [
    "tr_get_workflow_state",
    "tr_set_workflow_plan",
    "tr_get_simulation_capabilities",
    "tr_read_netlist",
    "tr_find_paths",
    "tr_restore_schematic",
    "tr_modify_netlist",
    "tr_execute_simulation_plan",
    "tr_run_simulation",
    "tr_parse_raw",
    "tr_read_guide",
    "tr_get_project_netlist",
    "tr_query_schematic_components",
    "tr_sync_project_components",
    "tr_prepare_report",
    "tr_generate_document",
    "tr_query_components",
    "resource_tr_workflow",
    "prompt_run_tr_simulation",
]
