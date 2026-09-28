"""CST 仿真求解工具 —— 异步求解 .cst 模型（一次性会话）。

CstSimulator 只负责把「求解」作业投递给 CST 自带解释器的独立进程（见 cst_worker.py），
通过通用 TaskRunner 异步执行，避免长时间阻塞 MCP 请求。
"""

from __future__ import annotations

from typing import Any

from servers import mcp
from servers.cst.cst_api import cst_runner, query_task, run_cst_job
from servers.utils import queue_full_response, require_file, submitted_response


class CstSimulator:
    """CST 仿真器 —— 封装异步求解任务的生命周期。"""

    def solve(self, model_path: str) -> str:
        """求解模型（一次性会话），返回求解后的模型路径。"""
        return run_cst_job("solve", model_path=model_path)

    def submit_solve(self, model_path: str) -> str | None:
        """提交求解任务，返回 task_id（队列满返回 None）。"""
        return cst_runner.submit(self.solve, model_path, metadata={"op": "solve", "model_path": model_path})


_simulator = CstSimulator()


@mcp.tool()
def cst_solve_async(model_path: str) -> dict[str, Any]:
    """异步求解 CST 模型（.cst），立即返回 task_id。

    用法："帮我求解这个 CST 模型"、"跑一下这个天线的仿真"

    求解在后台单 worker 串行执行（CST 一次只能跑一个），不阻塞 MCP 请求。
    通过 cst_solve_query 查询进度和结果。

    注意：CST 段跑在 CST 软件自带解释器（Python 3.6~3.9）上；报错里出现
    "cst package supports only Python 3.6/3.7/3.8/3.9" 是环境约束，不是模型问题。

    Args:
        model_path: .cst 模型文件绝对路径。

    Returns:
        {"success": True, "task_id": "cst-a1b2...", "status": "QUEUED"}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    task_id = _simulator.submit_solve(resolved)
    if task_id is None:
        return queue_full_response("CST_QUEUE_FULL", message="当前已有 CST 求解任务在进行，请稍后重试")
    return submitted_response(task_id, message="求解任务已提交")


@mcp.tool()
def cst_solve_query(task_id: str) -> dict[str, Any]:
    """查询 CST 求解任务：返回进度，完成时附带 model_path。

    一次调用同时拿到进度和结果，无需先查 status 再取 result。

    Args:
        task_id: cst_solve_async 返回的 task_id。
    """
    return query_task(
        task_id, cst_runner.snapshot(task_id),
        not_found_msg="CST 求解任务不存在、已过期或服务已重启",
        running_msg="求解进行中",
        success_key="model_path",
        success_msg="求解完成",
        fail_code="CST_SOLVE_FAILED",
        fail_msg="求解失败",
    )
