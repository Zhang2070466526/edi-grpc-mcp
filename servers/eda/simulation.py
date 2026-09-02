r"""EDA 仿真引擎 — 同步/异步仿真、网表仿真、ADS 控制器。

异步仿真：ThreadPoolExecutor(1) 串行执行，最多 8 个排队任务。
任务生命周期：QUEUED → ACCEPTED → RUNNING → SUCCEEDED/FAILED → 2h 后清理。


simulate_project              对 .epp 工程执行仿真，等待结果返回
simulate_netlist              仿真网表，自动返回 RAW 结果和仿真器日志
simulate_netlist_with_ads    基于网表文件调用 ADS 仿真控制器
start_simulation_async        启动异步仿真，立即返回 task_id
get_simulation_async_status   查询状态和当前已接收的 ads_output 日志
get_simulation_async_result   获取结果（运行中返回部分日志，完成返回完整日志）

自然语言使用示例：
  帮我对 EDA 工程 C:\...\EDI_TEST.epp 执行仿真
  帮我对 C:\...\netlist.log 执行 ADS 仿真，超时设为 300 秒
  帮我仿真这个工程，日志来源标记为 my_test
  查看仿真进度
  获取仿真结果

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  netlist_path     网表文件路径（simulate_netlist_with_ads）
  log_source       调用方日志标识，默认 "mcp_client"
  ads_path         ADS 安装路径，为空则自动判断
  timeout_seconds  最长等待秒数，无上限，默认 600（仿真）/ 120（控制器）
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_logger = logging.getLogger("eda.simulation")

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.eda.config import validate_file, validate_project_path
from servers import mcp

# -- 异步仿真任务注册表 --
_sim_tasks: dict[str, dict] = {}
_sim_lock = threading.Lock()

# 单工作线程执行器 — EDA 操作串行化，避免大量线程等待
_SIM_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sim")

# 进程退出时不等待正在执行的仿真（非 daemon 工作线程会阻塞退出，最长可达 timeout_seconds）
atexit.register(_SIM_EXECUTOR.shutdown, wait=False)

# 任务保留 2 小时
_TASK_TTL = 7200

# list_eda_tasks 的合法状态过滤值（模块级常量，避免每次调用重建）
_VALID_TASK_STATUSES = {"QUEUED", "QUEUE_TIMEOUT", "ACCEPTED", "RUNNING", "SUCCEEDED",
                        "FAILED", "TIMEOUT", "STREAM_DISCONNECTED", "REJECTED",
                        "PROTOCOL_MISMATCH", "GRPC_UNAVAILABLE"}


def _prune_tasks_locked() -> None:
    """（需持 _sim_lock）清理过期任务。"""
    now = time.time()
    expired = [
        tid for tid, t in _sim_tasks.items()
        if t.get("finished_at") is not None and now - t["finished_at"] > _TASK_TTL
    ]
    for tid in expired:
        del _sim_tasks[tid]


def _prune_tasks() -> None:
    """清理过期任务（超过 TTL 的已完成/失败任务）。"""
    with _sim_lock:
        _prune_tasks_locked()


def _task_completed(task: dict[str, Any]) -> bool:
    """任务是否已结束（以 finished_at 为唯一依据）。"""
    return task.get("finished_at") is not None


def _task_log_complete(task: dict[str, Any]) -> bool:
    """日志是否已完整接收。"""
    result = task.get("result")
    return bool(isinstance(result, dict) and result.get("log_complete", False))


def _current_ads_output(task: dict) -> str:
    """获取当前日志：优先已完成的完整 result，否则拼接实时 chunk。"""
    if task.get("result") and isinstance(task["result"], dict):
        return task["result"].get("ads_output", "")
    return "".join(task.get("log_chunks", []))


def _task_not_found(task_id: str) -> dict[str, Any]:
    """统一的 TASK_NOT_FOUND 响应（status/result 查询共用）。"""
    return {
        "success": False,
        "completed": True,
        "task_success": None,
        "outcome_known": False,
        "error_code": "TASK_NOT_FOUND",
        "task_id": task_id,
        "status": "UNKNOWN",
        "message": "仿真任务不存在、已经过期或服务已经重启",
        "ads_output": "",
        "log_complete": False,
    }


def _get_task_snapshot(task_id: str, *, prune: bool = False) -> dict[str, Any] | None:
    """获取任务快照，避免并发修改问题。prune=True 时在单锁内先清理过期任务。"""
    with _sim_lock:
        if prune:
            _prune_tasks_locked()
        task = _sim_tasks.get(task_id)
        if task is None:
            return None
        snapshot = dict(task)
        snapshot["log_chunks"] = list(task.get("log_chunks", []))
        result = task.get("result")
        snapshot["result"] = dict(result) if isinstance(result, dict) else result
        return snapshot


# ---------------------------------------------------------------------------
# 事件回调 — 异步任务实时更新
# ---------------------------------------------------------------------------

def _handle_sim_event(task_id: str, update: dict[str, Any]) -> None:
    """gRPC 事件回调：把增量日志/状态实时写入任务注册表。"""
    with _sim_lock:
        task = _sim_tasks.get(task_id)
        if task is None:
            return

        status = update.get("status", "")
        chunk = update.get("ads_output_chunk", "")
        details = update.get("details", {})

        if chunk:
            task["log_chunks"].append(chunk)

        if update.get("message"):
            task["message"] = update["message"]

        if details.get("project_path"):
            task["project_path"] = details["project_path"]

        if details.get("result_path"):
            task["result_path"] = details["result_path"]

        if status == "ACCEPTED":
            if task["status"] == "QUEUED":
                task["status"] = "ACCEPTED"

        elif status == "RESULT_STATUS_RUNNING":
            task["status"] = "RUNNING"
            if task["started_at"] is None:
                task["started_at"] = time.time()


# ---------------------------------------------------------------------------
# 后台执行
# ---------------------------------------------------------------------------

def _run_sim_task(
    task_id: str,
    client_uuid: str,
    project_path: str,
    log_source: str,
    timeout_seconds: int,
) -> None:
    """后台线程执行体：调用 call_grpc 跑仿真，把最终结果写回任务注册表。"""
    try:
        result = call_grpc(
            ecserver_pb2.SIMULATE_PROJECT,
            {"project_path": project_path, "log_source": log_source},
            timeout_seconds,
            max_timeout_seconds=3600,
            task_id=task_id,
            client_uuid=client_uuid,
            on_event=lambda update: _handle_sim_event(task_id, update),
        )

        with _sim_lock:
            task = _sim_tasks.get(task_id)
            if task is None:
                return
            task["status"] = result["status"]
            task["message"] = result["message"]
            task["result_path"] = result.get("result_path", "")
            task["result"] = result
            task["error"] = None if result.get("success") else result.get("message")
            task["finished_at"] = time.time()
            result["completed"] = True
            # task_success 已由 gRPC 层 _terminal_result 计算，此处不重复推导
            # 最终 result 已含完整拼接日志，清 chunk 避免双份
            task["log_chunks"] = []
        _logger.info("task=%s status=%s outcome_known=%s task_success=%s",
                     task_id[:12], result["status"], result.get("outcome_known"), result.get("task_success"))

    except Exception as exc:
        _logger.exception("task=%s mcp_exception", task_id[:12])
        with _sim_lock:
            task = _sim_tasks.get(task_id)
            if task is None:
                return
            partial_output = "".join(task.get("log_chunks", []))
            task["status"] = "FAILED"
            task["message"] = str(exc)
            task["error"] = str(exc)
            task["finished_at"] = time.time()
            task["result"] = {
                "success": False,
                "completed": True,
                "outcome_known": False,
                "task_success": None,
                "failure_source": "mcp",
                "task_id": task_id,
                "client_uuid": client_uuid,
                "status": "FAILED",
                "message": str(exc),
                "project_path": project_path,
                "result_path": task.get("result_path", ""),
                "ads_output": partial_output,
                "log_complete": False,
            }
            task["log_chunks"] = []


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------

@mcp.tool()
def start_simulation_async(
    project_path: str,
    log_source: str = "mcp_client",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """启动异步仿真，立即返回 task_id 供get_simulation_async_status后续查询进度。

    用法："帮我跑一下这个工程的仿真"、"启动仿真，超时设 10 分钟"

    任务生命周期：QUEUED → ACCEPTED → RUNNING → SUCCEEDED/FAILED → 2h 后自动清理
    最多 8 个排队任务，超限返回 SIMULATION_QUEUE_FULL
    通过 get_simulation_async_status(task_id) 查进度，get_simulation_async_result 取结果

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        log_source: 调用方日志标识。
        timeout_seconds: 最长等待秒数，默认 600。

    Returns:
        {"success": True, "task_id": "abc123...", "client_uuid": "...", "status": "QUEUED"}
    """
    resolved_path = validate_project_path(project_path)

    # 与 call_grpc 的范围校验保持一致，避免非法超时值进入后台线程后静默失败
    if timeout_seconds < 1 or timeout_seconds > 3600:
        return {"success": False,
                "error_code": "INVALID_PARAMETERS",
                "message": "timeout_seconds 必须在 1-3600 之间"}

    task_id = str(uuid.uuid4())
    client_uuid = str(uuid.uuid4())

    _prune_tasks()

    with _sim_lock:
        # Queue limit: max 8 concurrent tasks (atomic check+insert)
        pending = sum(1 for t in _sim_tasks.values()
                      if t.get("status") in ("QUEUED", "ACCEPTED", "RUNNING"))
        if pending >= 8:
            return {"success": False,
                    "error_code": "SIMULATION_QUEUE_FULL",
                    "message": f"当前已有 {pending} 个仿真任务在进行或排队，请稍后重试"}

        _sim_tasks[task_id] = {
            "task_id": task_id,
            "client_uuid": client_uuid,
            "operation": "simulate_project",
            "project_path": resolved_path,
            "result_path": "",
            "status": "QUEUED",
            "message": "等待执行",
            "log_chunks": [],
            "result": None,
            "error": None,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }

    _SIM_EXECUTOR.submit(
        _run_sim_task,
        task_id,
        client_uuid,
        resolved_path,
        log_source,
        timeout_seconds,
    )

    _logger.info("task=%s status=QUEUED project=%s timeout=%ds",
                 task_id[:12], resolved_path, timeout_seconds)
    return {
        "success": True,
        "task_id": task_id,
        "client_uuid": client_uuid,
        "status": "QUEUED",
        "message": "仿真任务已创建",
    }


@mcp.tool()
def get_simulation_async_status(task_id: str) -> dict[str, Any]:
    """查询异步仿真进度和实时日志。运行中即可调用，不阻塞。

    用法："查一下 task_id 为 xxx 的仿真进度"、"仿真跑完了吗"

    Returns:
        {"success": True, "completed": False, "task_success": null,
         "outcome_known": False, "status": "RUNNING", "ads_output": "...", "log_complete": False}
        completed=True 且 outcome_known=False 表示 MCP 已退出但 EDI 结果未知（如 TIMEOUT）
    """
    task = _get_task_snapshot(task_id, prune=True)

    if task is None:
        return _task_not_found(task_id)

    completed = _task_completed(task)

    grpc_outcome_known = bool(
        completed and task.get("result") is not None
        and isinstance(task["result"], dict)
        and task["result"].get("outcome_known", False)
    )
    grpc_task_success = task["result"].get("task_success") if grpc_outcome_known else None

    return {
        "success": True,
        "completed": completed,
        "task_success": grpc_task_success,
        "outcome_known": grpc_outcome_known,
        "task_id": task_id,
        "client_uuid": task["client_uuid"],
        "status": task["status"],
        "message": task["message"],
        "project_path": task["project_path"],
        "result_path": task["result_path"],
        "ads_output": _current_ads_output(task),
        "log_complete": _task_log_complete(task),
        "created_at": task["created_at"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
    }


@mcp.tool()
def get_simulation_async_result(task_id: str) -> dict[str, Any]:
    """获取异步仿真最终结果。运行中返回当前状态和已接收的部分日志，完成后返完整的仿真结果和日志(ads_output)。

    用法："仿真结果出来了吗"、"把 task_id xxx 的完整日志给我"

    Returns:
        完成时：{"success": True, "completed": True, "outcome_known": True,
                  "status": "SUCCEEDED", "result_path": ".../result.raw", "ads_output": "..."}
        运行中：{"success": True, "completed": False, "outcome_known": False,
                  "status": "RUNNING", "ads_output": "部分日志..."}
        超时/断连：{"success": True, "outcome_known": False, "task_success": null}
    """
    task = _get_task_snapshot(task_id, prune=True)

    if task is None:
        return _task_not_found(task_id)

    completed = _task_completed(task)

    # 已完成 — 返回完整 result；success 统一为「查询成功」语义（与 status 端点一致），
    # 仿真任务成败看 task_success / status，避免两端点 success 语义相反
    if task["result"] is not None:
        result = dict(task["result"])
        result["success"] = True
        return result

    # 运行中 — 返回当前状态和部分日志
    return {
        "success": True,
        "completed": completed,
        "task_success": None,
        "outcome_known": False,
        "task_id": task_id,
        "client_uuid": task["client_uuid"],
        "status": task["status"],
        "message": task["message"],
        "project_path": task["project_path"],
        "result_path": task["result_path"],
        "ads_output": _current_ads_output(task),
        "log_complete": False,
    }


@mcp.tool()
def simulate_project(
    project_path: str,
    log_source: str = "mcp_client",
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """同步执行 EDA 工程仿真，阻塞等待完成。长仿真建议用 start_simulation_async。

    用法："跑一下这个工程的仿真"（同步等待）

    注意：同步阻塞，HTTP 请求可能等待数分钟，交互场景建议异步版本。

    FetchEvent 长连接期间实时收集 ads_output 增量日志，
    成功或失败均返回完整日志。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        log_source: 调用方日志标识。
        timeout_seconds: 最长等待时间，默认 600 秒。

    Returns:
        {"success": True, "completed": True, "outcome_known": True,
         "status": "SUCCEEDED", "result_path": ".../result.raw", "ads_output": "..."}
    """
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.SIMULATE_PROJECT,
        {"project_path": resolved_path, "log_source": log_source},
        timeout_seconds,
        max_timeout_seconds=3600,
    )


@mcp.tool()
def simulate_netlist(
    netlist_path: str,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """仿真指定 netlist.log 文件，不需打开 .epp 工程。返回 RAW 结果+日志。

    用法："帮我跑一下这个网表文件"、"仿真 C:/test/netlist.log"
    服务端：网表→临时目录→ADS仿真→RAW归档到原网表同级 history/result.raw→清理临时目录

    Returns:
        {"success": True, "status": "SUCCEEDED", "result_path": ".../history/result.raw",
         "ads_output": "ADS simulator output..."}
    """
    resolved_netlist = validate_file(netlist_path)
    return call_grpc(
        ecserver_pb2.SIMULATE_NETLIST,
        {"netlist_path": resolved_netlist},
        timeout_seconds,
        max_timeout_seconds=3600,
    )


@mcp.tool()
def simulate_netlist_with_ads(
    netlist_path: str,
    ads_path: str = "",
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """调用 ADS 仿真控制器。

    Args:
        netlist_path: 网表文件路径。
        ads_path: ADS 安装路径，为空则自动判断。
        timeout_seconds: 最长等待时间，默认 120 秒。
    """
    resolved_netlist = validate_file(netlist_path)
    return call_grpc(
        ecserver_pb2.CALL_SIMULATION_CONTROLLER,
        {"netlist_path": resolved_netlist, "ads_path": ads_path},
        timeout_seconds,
        max_timeout_seconds=3600,
    )


@mcp.tool()
def list_eda_tasks(status: str = "") -> dict[str, Any]:
    """列出当前所有异步仿真任务，可选按状态过滤。不阻塞。

    用法："现在有哪些仿真在跑"、"列出所有失败的任务"

    Args:
        status: 按状态过滤，为空返回全部。
               可选：QUEUED / ACCEPTED / RUNNING / SUCCEEDED / FAILED /
               TIMEOUT / STREAM_DISCONNECTED / REJECTED。

    Returns:
        {"success": True, "total": 3, "tasks": [
            {"task_id": "abc", "status": "RUNNING", "project_path": "...", ...}]}
    """
    status_filter = status.strip().upper()
    if status_filter and status_filter not in _VALID_TASK_STATUSES:
        return {"success": False,
                "error_code": "INVALID_STATUS",
                "message": f"无效状态: {status}，可选: {sorted(_VALID_TASK_STATUSES)}"}
    _prune_tasks()
    snapshot_ids: list[str] = []
    with _sim_lock:
        snapshot_ids = list(_sim_tasks.keys())
    tasks: list[dict] = []
    for tid in snapshot_ids:
        t = _get_task_snapshot(tid)
        if t is None:
            continue
        st = t.get("status", "UNKNOWN")
        if status_filter and st.upper() != status_filter:
            continue
        tasks.append({
            "task_id": t["task_id"],
            "status": st,
            "operation": t.get("operation", ""),
            "project_path": t.get("project_path", ""),
            "message": t.get("message", ""),
            "created_at": t.get("created_at"),
            "started_at": t.get("started_at"),
            "finished_at": t.get("finished_at"),
            "error": t.get("error"),
        })
    return {
        "success": True,
        "total": len(tasks),
        "tasks": tasks,
    }
