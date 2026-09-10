"""MCP Prompt（TR 仿真）— 执行 TR 仿真工作流。

  run_tr_simulation — 工程发现 → 参数确认 → 保存计划 → 仿真 → 报告 → 原理图同步
"""

from __future__ import annotations

from typing import Any

from servers import mcp
from servers.tr_simulation.client import fetch_workflow


@mcp.prompt(
    name="run_tr_simulation",
    title="TR 仿真工作流",
    description="执行 TR 仿真工作流：工程发现 → 参数确认 → 保存计划 → 仿真 → 报告 → 原理图同步。"
                "严格遵循 SimulationAgent 的版本化工作流规则。",
)
def prompt_run_tr_simulation(epp_path: str = "") -> list[dict[str, Any]]:
    """TR 仿真工作流模板，注入版本化工作流规则并给出标准流程引导。

    Args:
        epp_path: EPP 工程绝对路径（可选，缺省时由 Agent 先向用户确认）。
    """
    workflow = fetch_workflow()
    ep = (epp_path or "").strip()

    parts: list[str] = ["请执行 TR 仿真工作流（调用 tr_* 工具），严格遵循下面的版本化工作流规则。"]
    if ep:
        parts.append(f"目标工程：{ep}")
    parts += [
        "",
        "开始前先确认：",
        "1. 用户必须提供存在的 EPP 绝对路径，不得搜索或猜测路径。",
        "2. 调用 `tr_get_simulation_capabilities` 获取可选指标、单位和必需参数。",
        "3. 调用 `tr_find_paths` 获取端口、有向路径和网表快照。",
        "4. 修改网表或仿真前，必须取得用户对所有端口、频段、指标的明确确认。",
        "",
        "── 工作流规则（来自 SimulationAgent，版本化）──",
        "",
        workflow,
    ]
    return [{"role": "user", "content": "\n".join(parts)}]
