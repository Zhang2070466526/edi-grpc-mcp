"""MCP Prompts（仿真类）— 仿真执行分析、抗烧毁评估。

  run_and_review_simulation — 异步仿真+日志分析，含轮询限制
  assess_anti_burnout       — 抗烧毁评估：仿真→按裕量排序→结论
"""

from __future__ import annotations

from typing import Any

from servers import mcp


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