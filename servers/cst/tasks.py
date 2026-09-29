"""CST 任务管理工具 —— 查看 / 取消 CST 异步任务队列。"""

from __future__ import annotations

import time
from typing import Any

from servers import mcp
from servers.cst.cst_api import cst_cancel_active, cst_runner


def _fmt_task(t: dict[str, Any]) -> dict[str, Any]:
    meta = t.get("metadata") or {}
    now = time.time()
    started = t.get("started_at") or t.get("created_at")
    elapsed = round(max(0.0, (t.get("finished_at") or now) - started), 1)
    return {
        "task_id": t["task_id"],
        "status": t["status"],
        "op": meta.get("op", ""),
        "model_path": meta.get("model_path", ""),
        "started_at": t.get("started_at"),
        "elapsed_seconds": elapsed,
    }


@mcp.tool()
def cst_task_list() -> dict[str, Any]:
    """列出当前所有 CST 异步任务（求解 / 导出）及状态。

    用法："现在有哪些 CST 任务在跑"

    Args:
        （无参数）

    Returns:
        {"success": True, "task_list": [{task_id, status, op, model_path,
         started_at, elapsed_seconds}]}
    """
    return {"success": True, "task_list": [_fmt_task(t) for t in cst_runner.list()]}


@mcp.tool()
def cst_task_cancel(task_id: str) -> dict[str, Any]:
    """取消一个 CST 异步任务（排队中的直接取消，运行中的尝试终止其子进程）。

    用法："把那个求解任务停掉"

    注意：运行中的任务只能尽力终止 python worker，真正占资源的 CST 引擎进程未必
    能立即停下（见 tasklist 判断）。取消后任务状态为 CANCELLED（与 FAILED 区分），
    取消原因写在 error 字段。

    Args:
        task_id: cst_task_list 或各 cst_*_async 返回的 task_id。

    Returns:
        {"success": True/False, "cancelled": True/False, "reason": "..."}
    """
    cancelled, reason = cst_runner.cancel(task_id, kill=cst_cancel_active)
    return {"success": cancelled, "cancelled": cancelled, "reason": reason}
