"""CST 结果导出工具 —— 导出 S 参数（无会话）与远场方向图（需会话，自动判断求解）。

两件事都交给 CST 自带解释器的独立进程执行（CST 官方 cst 库只支持 Python 3.6~3.9，
本服务跑 3.12 → 同进程 import 必失败，详见 servers/cst/cst_worker.py 头部说明）。
本模块只负责：参数校验 → 投递作业 → 把结果包成统一信封。
"""

from __future__ import annotations

import os
from typing import Any

from servers import mcp
from servers.cst.cst_api import (
    cst_busy_response,
    cst_cancel_active,
    cst_runner,
    query_task,
    run_cst_job,
)
from servers.task_runner import QueueBusyError
from servers.utils import (
    error_response,
    queue_full_response,
    require_file,
    submitted_response,
)


class CstResultExporter:
    """CST 结果导出器 —— S 参数（无会话）与远场方向图（需会话，自动判断求解）。"""

    def export_snp(self, model_path: str, output_dir: str, port_count: int | None) -> str:
        """读取求解后的 .cst，导出 S 参数为 Touchstone .sNp，返回文件路径。"""
        return run_cst_job(
            "export_snp",
            model_path=os.path.abspath(str(model_path)),
            output_dir=output_dir,
            port_count=port_count,
        )

    def export_farfield(self, model_path: str, output_dir: str) -> list[str]:
        """导出远场方向图，自动判断是否已求解，返回 txt 路径列表。

        流程：无会话检测结果树 → 无远场结果则先 run_solver + save → execute_vba 导出。
        """
        return run_cst_job(
            "export_farfield",
            model_path=os.path.abspath(str(model_path)),
            output_dir=output_dir,
        )


_result_exporter = CstResultExporter()


@mcp.tool()
def cst_export_snp(
    model_path: str,
    output_dir: str = "",
    port_count: int | None = None,
) -> dict[str, Any]:
    """导出 CST 模型的 S 参数为 Touchstone .sNp 文件（需先求解）。

    用法："把这个 CST 模型的 S 参数导出来"

    直接读取求解后的结果文件，不需要打开会话。导出后可通过文件路径
    查看或进一步处理。

    注意：归档（未解包）的 .cst 读不了结果树，需先用 cst_open_project 解包。

    本工具走同步串行队列：CST 正忙（有求解/导出在跑或排队）时立即返回 CST_BUSY，
    不排队；需要排队请用 cst_export_snp_async。

    Args:
        model_path: .cst 模型文件绝对路径（需已求解，存在 S 参数结果）。
        output_dir: 导出目录（空则默认导出到模型同级目录）。
        port_count: 端口数（None 则自动从结果推断）。

    Returns:
        {"success": True, "snp_path": "C:/.../xxx.s2p", "message": "S 参数已导出"}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    # 走 cst_runner 串行执行：与求解/远场导出的会话互斥，避免无会话读结果时
    # 与正在求解的会话并发（读到半写结果或 CST 单实例冲突）
    try:
        snp_path = cst_runner.run_sync(
            _result_exporter.export_snp, resolved, output_dir, port_count,
            metadata={"op": "export_snp", "model_path": resolved}, timeout=900,
            require_idle=True, kill=cst_cancel_active,
        )
    except QueueBusyError:
        return cst_busy_response("S 参数导出（要排队请改用 cst_export_snp_async）")
    except Exception as exc:
        return error_response("CST_EXPORT_FAILED", str(exc))
    return {"success": True, "snp_path": snp_path, "message": "S 参数已导出"}


@mcp.tool()
def cst_export_farfield(model_path: str, output_dir: str = "") -> dict[str, Any]:
    """导出 CST 模型的远场方向图为 ASCII .txt（自动判断是否已求解）。

    用法："把这个模型的远场方向图导出来"

    先无会话检测模型是否已求解：已有远场结果则直接导出；否则先 run_solver
    求解再导出。求解可能耗时数分钟，任务在后台串行执行，通过
    cst_export_farfield_query 查询进度和结果。

    注意：查询返回 farfield_txts=[] 表示模型里没有远场结果（多为未配 farfield
    监视器），不是导出失败；归档模型需先用 cst_open_project 解包。

    Args:
        model_path: .cst 模型文件绝对路径（模型需配置 farfield 监视器）。
        output_dir: 导出目录（空则默认模型同级目录）。

    Returns:
        {"success": True, "task_id": "cst-a1b2...", "status": "QUEUED"}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    if not output_dir:
        output_dir = os.path.dirname(os.path.abspath(resolved))
    task_id = cst_runner.submit(
        _result_exporter.export_farfield, resolved, output_dir,
        metadata={"op": "export_farfield", "model_path": resolved},
    )
    if task_id is None:
        return queue_full_response("CST_QUEUE_FULL", message="当前已有 CST 任务在进行，请稍后重试")
    return submitted_response(task_id, message="远场导出任务已提交")


@mcp.tool()
def cst_export_farfield_query(task_id: str) -> dict[str, Any]:
    """查询远场导出任务：返回进度，完成时附带 farfield_txts。

    一次调用同时拿到进度和结果，无需先查 status 再取 result。

    Args:
        task_id: cst_export_farfield 返回的 task_id。
    """
    result = query_task(
        task_id, cst_runner.snapshot(task_id),
        not_found_msg="CST 远场导出任务不存在、已过期或服务已重启",
        running_msg="导出进行中",
        success_key="farfield_txts",
        success_msg="远场方向图已导出",
        fail_code="CST_EXPORT_FARFIELD_FAILED",
        fail_msg="导出失败",
    )
    # 空列表不是「已导出」：模型里没有 farfield 结果（未配监视器）。如实措辞，
    # 否则 success=true + farfield_txts=[] 会被读成「导出成功了但没文件」。
    if result.get("completed") and result.get("farfield_txts") == []:
        result["message"] = "模型里没有远场结果（需在 CST 里配置 farfield 监视器并求解），未导出任何文件"
    return result


@mcp.tool()
def cst_export_snp_async(
    model_path: str,
    output_dir: str = "",
    port_count: int | None = None,
) -> dict[str, Any]:
    """异步导出 CST 模型的 S 参数为 Touchstone .sNp 文件（需先求解），立即返回 task_id。

    用法："把这个 CST 模型的 S 参数导出来（后台跑）"

    与 cst_export_snp 等价，但走异步队列：适合大模型 / 慢盘，避免同步调用超过
    客户端 300 s 读取上限被掐断。通过 cst_export_snp_query 查询进度和结果。

    Args:
        model_path: .cst 模型文件绝对路径（需已求解，存在 S 参数结果）。
        output_dir: 导出目录（空则默认导出到模型同级目录）。
        port_count: 端口数（None 则自动从结果推断）。

    Returns:
        {"success": True, "task_id": "cst-a1b2...", "status": "QUEUED"}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    task_id = cst_runner.submit(
        _result_exporter.export_snp, resolved, output_dir, port_count,
        metadata={"op": "export_snp", "model_path": resolved},
    )
    if task_id is None:
        return queue_full_response("CST_QUEUE_FULL", message="当前已有 CST 任务在进行，请稍后重试")
    return submitted_response(task_id, message="S 参数导出任务已提交")


@mcp.tool()
def cst_export_snp_query(task_id: str) -> dict[str, Any]:
    """查询 S 参数导出任务：返回进度，完成时附带 snp_path。

    一次调用同时拿到进度和结果，无需先查 status 再取 result。

    Args:
        task_id: cst_export_snp_async 返回的 task_id。
    """
    return query_task(
        task_id, cst_runner.snapshot(task_id),
        not_found_msg="CST S 参数导出任务不存在、已过期或服务已重启",
        running_msg="导出进行中",
        success_key="snp_path",
        success_msg="S 参数已导出",
        fail_code="CST_EXPORT_SNP_FAILED",
        fail_msg="导出失败",
    )
