"""HFSS 任务管理工具 —— 查看 / 取消 HFSS 异步任务队列。"""

from __future__ import annotations

import time
from typing import Any

from servers import mcp
from servers.ansys.run_analysis import hfss_runner


def _fmt_task(t: dict[str, Any]) -> dict[str, Any]:
    """把任务快照格式化成列表项。"""
    meta = t.get("metadata") or {}
    now = time.time()
    started = t.get("started_at") or t.get("created_at")
    elapsed = round(max(0.0, (t.get("finished_at") or now) - started), 1)
    return {
        "task_id": t["task_id"],
        "status": t["status"],
        "op": meta.get("operation", ""),
        "project_path": meta.get("project_path", ""),
        "design_name": meta.get("design_name", ""),
        "setup_name": meta.get("setup_name", ""),
        "started_at": t.get("started_at"),
        "elapsed_seconds": elapsed,
    }


@mcp.tool()
def hfss_task_list() -> dict[str, Any]:
    """列出当前所有 HFSS 异步任务及状态，无需参数直接调。查单个任务进度用 get_hfss_analysis_status。

    用法："现在有哪些 HFSS 仿真任务在跑"

    Args:
        （无参数）

    Returns:
        {"success": true, "task_list": [{"task_id", "status", "op", "project_path",
         "design_name", "setup_name", "started_at", "elapsed_seconds"}]}
    """
    return {"success": True, "task_list": [_fmt_task(t) for t in hfss_runner.list()]}


@mcp.tool()
def hfss_task_cancel(task_id: str) -> dict[str, Any]:
    """⚠️ 取消一个 HFSS 异步任务（尽力终止运行中的仿真）。

    用法："把那个 HFSS 求解任务停掉"

    注意：运行中的 HFSS 求解由 AEDT COM 的 Analyze() 阻塞执行，无法真正打断 ——
    取消只把任务标记为 CANCELLED（与 FAILED 区分），后台求解仍会跑完但结果不再记录。

    Args:
        task_id: hfss_task_list 或 start_hfss_analysis_async 返回的 task_id。

    Returns:
        {"success": true/false, "cancelled": true/false, "reason": "..."}
    """
    cancelled, reason = hfss_runner.cancel(task_id)
    return {"success": cancelled, "cancelled": cancelled, "reason": reason}
