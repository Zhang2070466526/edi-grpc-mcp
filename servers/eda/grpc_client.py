"""EDA gRPC 通信层 — 封装 FetchEvent + PerformAction 异步调用流程。

内部模块，不直接暴露为 MCP 工具。
调用 call_grpc(task_type, payload, timeout_seconds) 提交任务并等待结果。

调用顺序：FetchEvent（建立订阅）→ PerformAction（提交任务），
与文档要求一致。两次调用使用相同的 client_uuid。

支持：
  - 增量 ads_output 日志收集（原样追加，不 strip）
  - 事件回调（on_event），供异步任务实时更新
  - 外部传入 task_id / client_uuid，贯穿整个调用链
  - 严格事件筛选（client_uuid + task_id + event_type）
  - 超时/断连保留已收日志，log_complete=False
  - 统一 deadline 控制总超时
  - finally 确保事件流释放
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

import grpc

from proto import ecserver_pb2, ecserver_pb2_grpc
from servers.eda.config import EDA_GRPC_SERVER, validate_project_path

_logger = logging.getLogger("eda.grpc_client")

# EDA 操作全局锁 — 确保同一时间只进行一项 gRPC 状态操作
_EDA_LOCK = threading.RLock()
_queue_busy = False  # EDA gRPC 执行槽是否被占用（跨线程标志，由 _EDA_LOCK 串行化写）

# gRPC 通道配置：默认接收上限 4MB，长仿真日志可能会超过，导致
# RESOURCE_EXHAUSTED 被误判为 STREAM_DISCONNECTED，结果丢失。
# keepalive 防止防火墙/NAT 空闲超时掐断 FetchEvent 长连接流。
_CHANNEL_OPTIONS = [
    ("grpc.max_receive_message_length", 256 * 1024 * 1024),  # 4MB -> 256MB
    ("grpc.max_send_message_length", 64 * 1024 * 1024),
    ("grpc.keepalive_time_ms", 300_000),  # 300s
    ("grpc.keepalive_timeout_ms", 10_000),  # ping 发出后 10s 无响应才判定超时
    ("grpc.keepalive_permit_without_calls", 1),
]

# 模块级 channel 缓存：_EDA_LOCK 已串行化所有调用，复用同一 channel
# 避免每次调用重新 TCP 握手，高频操作下减少延迟叠加
_channel_cache: dict[str, grpc.Channel] = {}
_channel_lock = threading.Lock()


def is_queue_busy() -> bool:
    """EDA 执行槽是否被占用（只读诊断接口）。"""
    return _queue_busy


def get_cached_channel(target: str) -> grpc.Channel | None:
    """线程安全地读取缓存的 channel（不触发重建）。"""
    with _channel_lock:
        return _channel_cache.get(target)


def _get_channel(target: str) -> grpc.Channel:
    """获取缓存的 gRPC channel，不健康时关闭并重建。

    由 _channel_lock 串行化；复用模块级 _channel_cache，避免每次调用重新 TCP 握手。
    """
    with _channel_lock:
        ch = _channel_cache.get(target)
        if ch is not None:
            # 健康检查：channel 可能因 EDI 重启而断开
            try:
                grpc.channel_ready_future(ch).result(timeout=2)
            except (grpc.FutureTimeoutError, grpc.RpcError):
                _logger.warning("grpc channel unhealthy, reconnecting %s", target)
                try:
                    ch.close()
                except Exception:
                    pass  # close 失败也不影响后续重建
                ch = None
        if ch is None:
            ch = grpc.insecure_channel(target, options=_CHANNEL_OPTIONS)
            _channel_cache[target] = ch
        return ch


class _ChannelHandle:
    """上下文管理器包装：退出时不关闭缓存的 channel。"""

    def __init__(self, ch: grpc.Channel):
        """包装一个 channel，供 with 语句使用。"""
        self._ch = ch

    def __enter__(self) -> grpc.Channel:
        """返回被包装的 channel。"""
        return self._ch

    def __exit__(self, *args: object) -> None:
        """退出时不关闭 channel（缓存复用）。"""
        pass  # 缓存复用，不关闭


# 回调类型
GrpcEventCallback = Callable[[dict[str, Any]], None]

# PerformAction 提交阶段最长等待（秒），主要时间留给 FetchEvent
_PERFORM_ACTION_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _parse_payload_json(payload_json: str) -> tuple[dict[str, Any], str | None]:
    """解析 payload_json，返回 (dict, error_or_none)。"""
    if not payload_json:
        return {}, None
    try:
        value = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        return {}, f"payload_json 解析失败: {exc}"
    if not isinstance(value, dict):
        return {}, "payload_json 不是 JSON 对象"
    return value, None


def _emit_event(callback: GrpcEventCallback | None, update: dict[str, Any]) -> None:
    """安全地调用事件回调；回调异常只记日志，不影响主流程。"""
    if callback is None:
        return
    try:
        callback(update)
    except Exception:
        _logger.exception("gRPC 事件回调失败 task_id=%s", update.get("task_id"))


# ---------------------------------------------------------------------------
# 终端结果构建
# ---------------------------------------------------------------------------

def _terminal_result(
        success: bool,
        status: str,
        message: str,
        client_uuid: str,
        task_id: str,
        task_type_name: str,
        project_path: str = "",
        result_path: str = "",
        ads_output: str = "",
        log_complete: bool = False,
        latest_details: dict[str, Any] | None = None,
        *,
        outcome_known: bool = False,
) -> dict[str, Any]:
    """构建终端结果字典。

    outcome_known=True 表示已收到 EDI 的最终事件（SUCCEEDED/FAILED），
    此时 task_success 有意义；False 表示 EDI 任务结果未知（超时/断连等）。
    """
    latest_details = latest_details or {}
    return {
        "success": success,
        "completed": True,
        "outcome_known": outcome_known,
        "task_success": success if outcome_known else None,
        "client_uuid": client_uuid,
        "task_id": task_id,
        "task_type": task_type_name,
        "status": status,
        "message": message,
        "project_path": project_path,
        "result_path": result_path,
        "ads_output": ads_output,
        "log_complete": log_complete,
        "details": latest_details,
    }


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def call_grpc(
        task_type: int,
        payload: dict[str, Any],
        timeout_seconds: int,
        max_timeout_seconds: int = 3600,
        *,
        task_id: str | None = None,
        client_uuid: str | None = None,
        on_event: GrpcEventCallback | None = None,
) -> dict[str, Any]:
    """所有 gRPC 工具的统一入口。全局 RLock 串行化，先 FetchEvent 后 PerformAction。

    流程：获取锁 → 建立流式订阅（FetchEvent）→ 提交任务（PerformAction）
    → 三重回显校验（client_uuid/task_id/event_type）→ 消费事件流 → 终态返回

    异常分类：DEADLINE_EXCEEDED→TIMEOUT, RESOURCE_EXHAUSTED→PAYLOAD_TOO_LARGE,
    已受理后断开→STREAM_DISCONNECTED, 未受理→GRPC_UNAVAILABLE

    Args:
        task_type: EventType 枚举值。
        payload: 任务参数字典。
        timeout_seconds: 总超时秒数（需 > 0）。
        max_timeout_seconds: 最大允许秒数，默认 3600。
        task_id: 外部预生成的任务 ID（None 时内部生成）。
        client_uuid: 外部预生成的客户端 ID（None 时内部生成）。
        on_event: 事件回调，接收每个 FetchEvent 事件的增量信息。

    Returns:
        {"success": bool, "completed": True, "outcome_known": bool, "task_success": bool|null,
         "status": "SUCCEEDED/FAILED/TIMEOUT/...", "ads_output": "...", "log_complete": bool}
    """
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds 必须大于 0")
    if timeout_seconds > max_timeout_seconds:
        raise ValueError(f"timeout_seconds 不能超过 {max_timeout_seconds} 秒")

    actual_task_id = task_id or str(uuid.uuid4())
    actual_client_uuid = client_uuid or str(uuid.uuid4())

    # 全局串行锁：EDI 同一时间只能执行一个 gRPC 操作
    # 超时包含排队时间——client 调用时设置的 timeout_seconds 是"从请求到响应"的总时长
    deadline = time.monotonic() + timeout_seconds

    acquired = _EDA_LOCK.acquire(timeout=timeout_seconds)
    if not acquired:
        return _terminal_result(  # 排队超时，未获取锁
            success=False, status="QUEUE_TIMEOUT",
            message=f"等待 EDA 执行槽位超时（{timeout_seconds:.0f}s）",
            client_uuid=actual_client_uuid, task_id=actual_task_id,
            task_type_name=ecserver_pb2.EventType.Name(task_type),
            project_path=payload.get("project_path", ""),
            result_path="", ads_output="", log_complete=False,
            latest_details={},
        )
    global _queue_busy
    _queue_busy = True
    try:
        if time.monotonic() >= deadline:
            # 排队耗时耗尽预算，未开始执行（几乎不可达：acquire 成功意味着未超时）
            return _terminal_result(
                success=False, status="QUEUE_TIMEOUT",
                message=f"等待 EDA 执行槽位超时（{timeout_seconds:.0f}s）",
                client_uuid=actual_client_uuid, task_id=actual_task_id,
                task_type_name=ecserver_pb2.EventType.Name(task_type),
                project_path=payload.get("project_path", ""),
                result_path="", ads_output="", log_complete=False,
                latest_details={},
            )
        return _call_grpc_unlocked(
            task_type, payload, deadline,
            task_id=actual_task_id,
            client_uuid=actual_client_uuid,
            on_event=on_event,
        )
    finally:
        _queue_busy = False
        _EDA_LOCK.release()


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------

def _call_grpc_unlocked(
        task_type: int,
        payload: dict[str, Any],
        deadline: float,
        *,
        task_id: str,
        client_uuid: str,
        on_event: GrpcEventCallback | None,
) -> dict[str, Any]:
    """在已持有 _EDA_LOCK 的前提下执行完整 gRPC 调用（无锁实现）。

    顺序：FetchEvent 订阅 → PerformAction 提交 → 三重回显校验 → 消费事件流 → 终态返回。
    调用方负责获取/释放 _EDA_LOCK，并传入 deadline 控制总超时。
    """
    task_type_name = ecserver_pb2.EventType.Name(task_type)

    request = ecserver_pb2.Request(
        client_uuid=client_uuid,
        task_id=task_id,
        type=task_type,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )

    started_at = time.monotonic()
    ads_output_chunks: list[str] = []
    _given_timeout = max(0.0, deadline - started_at)
    latest_details: dict[str, Any] = {}
    event_stream = None
    action_accepted = False

    def remaining() -> float:
        """返回距 deadline 的剩余秒数（下限 0.1，避免超时参数为 0）。"""
        return max(0.1, deadline - time.monotonic())

    try:
        with _ChannelHandle(_get_channel(EDA_GRPC_SERVER)) as channel:
            stub = ecserver_pb2_grpc.ExternalCallStub(channel)

            # ── 1. 先建立 FetchEvent 订阅 ──
            _logger.info("task=%s client=%s type=%s phase=SUBSCRIBING",
                         task_id[:12], client_uuid[:12], task_type_name)
            event_stream = stub.FetchEvent(
                ecserver_pb2.FetchEventRequest(client_uuid=client_uuid),
                timeout=remaining(),
            )
            _logger.info("task=%s phase=SUBSCRIBED", task_id[:12])

            # ── 2. 再提交 PerformAction ──
            _logger.info("task=%s phase=PERFORM_ACTION", task_id[:12])
            response = stub.PerformAction(
                request,
                timeout=min(_PERFORM_ACTION_TIMEOUT, remaining()),
            )

            # ── 3. 未受理 ──
            if response.code != 0:
                message = response.message or f"EDA 服务未受理 {task_type_name} 任务"
                _logger.warning("task=%s phase=REJECTED code=%d message=%s",
                                task_id[:12], response.code, message)
                _emit_event(on_event, {
                    "phase": "REJECTED",
                    "client_uuid": client_uuid,
                    "task_id": task_id,
                    "task_type": task_type_name,
                    "status": "REJECTED",
                    "message": message,
                    "ads_output_chunk": "",
                    "details": {},
                })
                return _terminal_result(
                    success=False, status="REJECTED", message=message,
                    client_uuid=client_uuid, task_id=task_id,
                    task_type_name=task_type_name,
                    project_path=payload.get("project_path", ""),
                    result_path="", ads_output="", log_complete=True,
                    latest_details={},
                    outcome_known=True,
                )

            # ── 4. 回显校验 ──
            def _mismatch(msg: str) -> dict[str, Any]:
                _logger.error("task=%s %s", task_id[:12], msg)
                return _terminal_result(
                    success=False, status="PROTOCOL_MISMATCH", message=msg,
                    client_uuid=client_uuid, task_id=task_id,
                    task_type_name=task_type_name,
                    project_path=payload.get("project_path", ""),
                )
            if response.client_uuid and response.client_uuid != client_uuid:
                return _mismatch(f"PerformAction client_uuid 不匹配: sent={client_uuid} got={response.client_uuid}")
            if response.task_id and response.task_id != task_id:
                return _mismatch(f"PerformAction task_id 不匹配: sent={task_id} got={response.task_id}")
            if response.event_type not in (
                    ecserver_pb2.EVENT_TYPE_UNSPECIFIED,
                    task_type,
            ):
                return _mismatch(f"PerformAction event_type 不匹配: sent={task_type_name} got={ecserver_pb2.EventType.Name(response.event_type)}")

            # ── 5. ACCEPTED 回调 ──
            _logger.info("task=%s phase=ACCEPTED code=0", task_id[:12])
            _emit_event(on_event, {
                "phase": "ACCEPTED",
                "client_uuid": client_uuid,
                "task_id": task_id,
                "task_type": task_type_name,
                "status": "ACCEPTED",
                "message": response.message or "task accepted",
                "ads_output_chunk": "",
                "details": {},
            })

            # ── 6. 消费已建立的事件流 ──
            # 三重筛选：client_uuid + task_id + event_type 全部匹配才处理
            # 增量收集 ads_output（不 strip、不覆写），终态事件使用完整日志
            chunk_count = 0
            action_accepted = response.code == 0
            for event in event_stream:
                chunk = ""  # 防止终态事件先于普通事件到达时 UnboundLocalError
                if event.client_uuid != client_uuid:
                    continue
                if event.task_id != task_id:
                    continue
                if event.event_type != task_type:
                    continue

                details, parse_error = _parse_payload_json(event.payload_json)

                is_terminal = event.status in (
                    ecserver_pb2.RESULT_STATUS_SUCCESS,
                    ecserver_pb2.RESULT_STATUS_FAILED,
                )

                if is_terminal:
                    # 终态：使用完整日志
                    final_output = details.get("ads_output", "")
                    ads_output = final_output if isinstance(final_output, str) and final_output \
                        else "".join(ads_output_chunks)
                else:
                    chunk = details.get("ads_output", "")
                    if chunk is None:
                        chunk = ""
                    elif not isinstance(chunk, str):
                        chunk = str(chunk)
                    if chunk:
                        ads_output_chunks.append(chunk)
                        chunk_count += 1

                for key, value in details.items():
                    if key != "ads_output":
                        latest_details[key] = value

                status_name = ecserver_pb2.ResultStatus.Name(event.status)

                _emit_event(on_event, {
                    "phase": "EVENT",
                    "client_uuid": client_uuid,
                    "task_id": task_id,
                    "task_type": task_type_name,
                    "status": status_name,
                    "message": event.message,
                    "ads_output_chunk": chunk,
                    "details": {k: v for k, v in details.items() if k != "ads_output"},
                    "payload_parse_error": parse_error,
                })

                project_path = latest_details.get("project_path", payload.get("project_path", ""))
                result_path = latest_details.get("result_path", "")

                if event.status == ecserver_pb2.RESULT_STATUS_SUCCESS:
                    _logger.info("task=%s phase=COMPLETED status=SUCCEEDED duration=%.1fs chunks=%d",
                                 task_id[:12], time.monotonic() - started_at, chunk_count)
                    # Verify payload integrity: SUCCESS must have parseable payload
                    if parse_error:
                        _logger.error("task=%s SUCCEEDED but payload_json invalid: %s",
                                      task_id[:12], parse_error)
                        return _terminal_result(
                            success=False, status="PROTOCOL_MISMATCH",
                            message=f"SUCCEEDED 事件 payload_json 无法解析: {parse_error}",
                            client_uuid=client_uuid, task_id=task_id,
                            task_type_name=task_type_name,
                            project_path=project_path, result_path=result_path,
                            ads_output=ads_output, log_complete=True,
                            latest_details=latest_details,
                        )
                    return _terminal_result(
                        success=True, status="SUCCEEDED",
                        message=event.message or "task completed",
                        client_uuid=client_uuid, task_id=task_id,
                        task_type_name=task_type_name,
                        project_path=project_path, result_path=result_path,
                        ads_output=ads_output, log_complete=True,
                        latest_details=latest_details,
                        outcome_known=True,
                    )

                if event.status == ecserver_pb2.RESULT_STATUS_FAILED:
                    _logger.info("task=%s phase=COMPLETED status=FAILED duration=%.1fs chunks=%d",
                                 task_id[:12], time.monotonic() - started_at, chunk_count)
                    return _terminal_result(
                        success=False, status="FAILED",
                        message=event.message or "task failed",
                        client_uuid=client_uuid, task_id=task_id,
                        task_type_name=task_type_name,
                        project_path=project_path, result_path=result_path,
                        ads_output=ads_output, log_complete=True,
                        latest_details=latest_details,
                        outcome_known=True,
                    )

            # ── 流结束但无终态 ──
            return _terminal_result(
                success=False, status="STREAM_DISCONNECTED",
                message="FetchEvent 流已结束但未收到终态事件，EDI 端任务状态未知",
                client_uuid=client_uuid, task_id=task_id,
                task_type_name=task_type_name,
                project_path=latest_details.get("project_path", payload.get("project_path", "")),
                result_path=latest_details.get("result_path", ""),
                ads_output="".join(ads_output_chunks), log_complete=False,
                latest_details=latest_details,
            )

    except grpc.RpcError as exc:
        code = exc.code().name if exc.code() else "UNKNOWN"
        code_enum = exc.code()
        # 消息过大（如长仿真日志超过 256MB 上限）
        if code_enum == grpc.StatusCode.RESOURCE_EXHAUSTED:
            _logger.error("task=%s phase=PAYLOAD_TOO_LARGE", task_id[:12])
            return _terminal_result(
                success=False, status="PAYLOAD_TOO_LARGE",
                message=f"EDI 返回的消息过大（>256MB），日志已部分接收",
                client_uuid=client_uuid, task_id=task_id,
                task_type_name=task_type_name,
                project_path=latest_details.get("project_path", payload.get("project_path", "")),
                result_path=latest_details.get("result_path", ""),
                ads_output="".join(ads_output_chunks), log_complete=False,
                latest_details=latest_details,
            )
        # 区分超时、断连、不可达三种异常
        if code_enum == grpc.StatusCode.DEADLINE_EXCEEDED:
            _logger.warning("task=%s phase=TIMEOUT", task_id[:12])
            return _terminal_result(
                success=False, status="TIMEOUT",
                message=f"MCP 已停止等待（{_given_timeout:.0f}s），EDI 端任务状态未知",
                client_uuid=client_uuid, task_id=task_id,
                task_type_name=task_type_name,
                project_path=latest_details.get("project_path", payload.get("project_path", "")),
                result_path=latest_details.get("result_path", ""),
                ads_output="".join(ads_output_chunks), log_complete=False,
                latest_details=latest_details,
            )
        # 任务已受理后断开 → STREAM_DISCONNECTED；未受理 → GRPC_UNAVAILABLE
        if action_accepted:
            _logger.error("task=%s phase=STREAM_DISCONNECTED code=%s", task_id[:12], code)
            return _terminal_result(
                success=False, status="STREAM_DISCONNECTED",
                message=f"FetchEvent 长连接中断 ({code}): {exc.details() or exc}",
                client_uuid=client_uuid, task_id=task_id,
                task_type_name=task_type_name,
                project_path=latest_details.get("project_path", payload.get("project_path", "")),
                result_path=latest_details.get("result_path", ""),
                ads_output="".join(ads_output_chunks), log_complete=False,
                latest_details=latest_details,
            )
        return _terminal_result(
            success=False, status="GRPC_UNAVAILABLE",
            message=f"无法连接 EDA gRPC ({EDA_GRPC_SERVER}, {code})，请手动启动 EDI 软件后重试: {exc.details() or exc}",
            client_uuid=client_uuid, task_id=task_id,
            task_type_name=task_type_name,
            project_path=payload.get("project_path", ""),
            result_path="", ads_output="", log_complete=False,
            latest_details={},
        )

    finally:
        # ── 确保事件流释放 ──
        if event_stream is not None:
            event_stream.cancel()


def call_project_grpc(
    task_type: int,
    project_path: str,
    timeout_seconds: int,
    *,
    max_timeout_seconds: int = 300,
    **payload_extras: Any,
) -> dict[str, Any]:
    """校验工程路径后调用 gRPC，消除「validate + call_grpc 三段式」样板。

    大多数 EDA gRPC 工具的 payload 都以 project_path 为核心字段，额外字段通过
    payload_extras 传入（如 need_save、instance_name、component_type 等）。

    用法：
        return call_project_grpc(ecserver_pb2.CLOSE_PROJECT, project_path,
                                 timeout_seconds, need_save=need_save)
    """
    resolved = validate_project_path(project_path)
    return call_grpc(
        task_type,
        {"project_path": resolved, **payload_extras},
        timeout_seconds,
        max_timeout_seconds=max_timeout_seconds,
    )
