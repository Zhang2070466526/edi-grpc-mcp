"""TR 仿真工作流 Resource —— 把 SimulationAgent 的工作流规则注入给上层 Agent。"""

from __future__ import annotations

from servers import mcp
from servers.tr_simulation.client import fetch_workflow


@mcp.resource(
    "edi://integration/workflow",
    name="TR Simulation Workflow",
    title="TR 仿真工作流规则",
    description="调用 SimulationAgent 的 tr_* 工具前必须遵循的工作流规则（会话复用、参数确认、"
                "失败纠错、报告事实保护、原理图同步与回退）。实时从 SimulationAgent 拉取，版本化。",
    mime_type="text/markdown",
)
def resource_tr_workflow() -> str:
    """实时拉取 /api/v1/integration/workflow 返回的 markdown 工作流说明。"""
    return fetch_workflow()
