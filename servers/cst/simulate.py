"""CST 仿真求解工具 —— 异步求解 .cst 模型（一次性会话）。

CstSimulator 类封装「可写副本 → 连接 → run_solver → 保存 → 关闭」的一次性会话流程，
通过通用 TaskRunner 异步执行，避免长时间阻塞 MCP 请求。
"""

from __future__ import annotations

import os
from typing import Any

from servers import mcp
from servers.utils import validate_file, submitted_response, queue_full_response
from servers.cst.cst_api import CstApi, cst_runner, query_task, to_writable_copy


class CstSimulator:
    """CST 仿真器 —— 封装异步求解任务的生命周期。"""

    def __init__(self) -> None:
        self._api = CstApi()

    def solve(self, model_path: str) -> str:
        """求解模型（一次性会话），返回求解后的模型路径。"""
        model_path = to_writable_copy(os.path.abspath(str(model_path)))
        de, prj = self._api.open_session(model_path)
        try:
            if prj.modeler.run_solver() is False:
                raise RuntimeError("run_solver returned False")
            prj.save()
        finally:
            self._api.close_session(de, prj)
        return model_path

    def submit_solve(self, model_path: str) -> str | None:
        """提交求解任务，返回 task_id（队列满返回 None）。"""
        return cst_runner.submit(self.solve, model_path)


_simulator = CstSimulator()


@mcp.tool()
def cst_solve_async(model_path: str) -> dict[str, Any]:
    """异步求解 CST 模型（.cst），立即返回 task_id。

    用法："帮我求解这个 CST 模型"、"跑一下这个天线的仿真"

    求解在后台单 worker 串行执行（CST 一次只能跑一个），不阻塞 MCP 请求。
    通过 cst_solve_query 查询进度和结果。

    Args:
        model_path: .cst 模型文件绝对路径。

    Returns:
        {"success": True, "task_id": "cst-a1b2...", "status": "QUEUED"}
    """
    resolved = validate_file(model_path, (".cst",))
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
