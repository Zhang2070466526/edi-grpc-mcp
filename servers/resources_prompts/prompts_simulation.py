"""MCP Prompts（仿真类）— 仿真执行分析。

  run_and_review_simulation — 异步仿真+日志分析，含轮询限制
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
        analyze_log: str = "true",
) -> list[dict[str, Any]]:
    """仿真执行与分析工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        execution_mode: 已废弃（仿真仅支持异步），保留参数兼容旧调用。
        analyze_log: 是否分析 ads_output 日志，**必须传字符串**（MCP 协议规定 prompts/get 的
            arguments 是 string→string，传 bool 会在协议层被 -32602 拒掉，进不到本函数）。
            真值写 "true"/"yes"/"1"/"on"，假值写 "false"/"no"/"0"/"off"，大小写不敏感。
    """
    analyze = str(analyze_log).strip().lower() in ("true", "yes", "1", "on")
    steps: list[str] = [
        "1. 调用 `get_project_summary` 确认工程中有仿真器件。",
        "2. 调用 `start_simulation_async` 启动仿真，获取 task_id。",
        "3. 启动后最多立即查询一次 `get_simulation_async_status`。如果仍在运行，返回 task_id 告知用户稍后查询。不要紧密轮询（间隔不少于 10 秒），单次对话最多自动查询 3 次。",
        "4. 完成后调用 `get_simulation_async_result` 获取完整结果和日志。",
    ]

    if analyze:
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
                    f"分析日志：{'是' if analyze else '否'}\n\n"
                    + "\n".join(steps)
            ),
        },
    ]