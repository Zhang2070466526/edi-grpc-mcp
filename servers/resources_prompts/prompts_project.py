"""MCP Prompts（工程类）— 工程检查、信号链分析。

  inspect_edi_project   — 只读检查：概览→变量→器件→仿真配置
  analyze_signal_chain  — 信号链分析：追踪链路→逐级说明
"""

from __future__ import annotations

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