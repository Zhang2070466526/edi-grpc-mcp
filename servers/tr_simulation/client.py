"""SimulationAgent HTTP 集成客户端 —— 会话管理、稳定 request_id、调用与轮询。

对接外部服务 SimulationAgent.exe（默认 http://127.0.0.1:17866，仅监听本机、无鉴权），
把 `/api/v1/integration/*` 的 RPC 调用封装成同步函数供 MCP 工具层使用。

关键设计：
- 会话按 epp_path 复用：有 epp_path 的工具按归一化路径映射到固定 session_id；
  无 epp_path 的工具复用「当前最近使用的 session」。
- 稳定 request_id：对 (tool, arguments) 取 sha256 摘要，相同参数重试会命中原 operation，
  避免同步阻塞下客户端超时重试导致重复执行仿真（SimulationAgent 按
  session_id+request_id+tool+arguments 在 EXE 生命周期内去重）。
- 后台工具在 handler 内部轮询 operation 到终态，不暴露 operation_id 查询工具。

参考文档：servers/tr_simulation/MCP_HTTP集成说明.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

import httpx

from servers.settings import get_settings
from servers.utils import error_response

_log = logging.getLogger("simulation_agent")

_settings = get_settings()
_BASE_URL = _settings.simulation_agent_url.rstrip("/")
_TIMEOUT = _settings.simulation_agent_timeout

# 后台工具轮询参数
_POLL_INTERVAL_SECONDS = 1.0
_POLL_TIMEOUT_SECONDS = 600

# session 状态：epp_path(归一化) -> session_id，以及「当前最近使用的 session」
_sessions: dict[str, str] = {}
_current_session_id: str | None = None
_lock = threading.Lock()


def _normalize_epp_path(path: str) -> str:
    """归一化 epp 路径：小写 + 正斜杠，避免 Windows 大小写/分隔符差异导致分 session。"""
    return (path or "").replace("\\", "/").strip().lower()


def _confirm_id() -> str:
    """生成非空 confirmation_id（确认门默认通过，见 docs/ACCESS 相关讨论）。"""
    return f"mcp-confirm-{int(time.time())}"


def _post(url: str, json_body: dict[str, Any]) -> httpx.Response:
    return httpx.post(url, json=json_body, timeout=_TIMEOUT)


def _get(url: str) -> httpx.Response:
    return httpx.get(url, timeout=_TIMEOUT)


def _create_session() -> str:
    """POST /api/v1/integration/sessions 创建会话，返回 server 生成的 session_id。"""
    resp = _post(f"{_BASE_URL}/api/v1/integration/sessions", {})
    if resp.status_code not in (200, 201):
        raise httpx.HTTPStatusError(
            f"创建会话失败 (HTTP {resp.status_code})", request=resp.request, response=resp
        )
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("SimulationAgent 创建会话返回无法解析")
    sid = data.get("session_id") if isinstance(data, dict) else None
    if not sid:
        raise RuntimeError("SimulationAgent 创建会话未返回 session_id")
    return sid


def _resolve_session(epp_path: str | None) -> str:
    """解析/创建 session：有 epp_path 按路径复用，无则复用当前 session。

    创建会话的阻塞 HTTP 调用放在锁外，避免多线程下互相阻塞；
    并发竞争时最多多建一个冗余会话（无害）。
    """
    global _current_session_id
    key = _normalize_epp_path(epp_path) if epp_path else None

    with _lock:
        sid = _sessions.get(key) if key else _current_session_id
        if sid:
            _current_session_id = sid
            return sid

    sid = _create_session()
    with _lock:
        if key:
            _sessions[key] = sid
        _current_session_id = sid
    return sid


def _invalidate_session(epp_path: str | None) -> bool:
    """清除缓存的 session（供 404 自愈时调用），返回是否确实清除了一个缓存会话。"""
    global _current_session_id
    key = _normalize_epp_path(epp_path) if epp_path else None
    with _lock:
        if key:
            had = key in _sessions
            _sessions.pop(key, None)
            return had
        if _current_session_id:
            _current_session_id = None
            return True
        return False


def _stable_request_id(tool: str, arguments: dict[str, Any]) -> str:
    """确定性 request_id：相同 (tool, arguments) 始终得到同一 id，用于幂等。"""
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"mcp-{tool}-{digest}"


def _http_error_operation(resp: httpx.Response) -> dict[str, Any]:
    """把 HTTP 4xx/5xx 映射为 operation 形态（status=failed），由 _normalize 转成错误响应。"""
    code = resp.status_code
    try:
        detail = resp.json()
    except Exception:
        detail = resp.text[:300]
    # 解析上游 JSON：提取 error / error_type 字段，别把整段 dict 塞进 message（agent 无法解析）
    error_type = None
    if isinstance(detail, dict):
        error_type = detail.get("error_type")
        detail = detail.get("error") or detail.get("message") or detail.get("detail") or detail
    # 剥离上游英文样板（「Please try again」与 hint=do_not_retry 语义相反）
    if isinstance(detail, str):
        detail = detail.replace("An error occurred while running the tool. Please try again. Error: ", "")
    if code == 400:
        msg = f"请求格式错误 (HTTP 400): {detail}"
    elif code == 404:
        msg = "会话、工具或 operation 不存在 (HTTP 404)"
    elif code == 409:
        msg = f"幂等冲突或缺少用户确认 (HTTP 409): {detail}"
    elif code == 422:
        msg = f"工具执行异常: {detail}"
    else:
        msg = f"SimulationAgent 调用失败 (HTTP {code}): {detail}"
    return {"status": "failed", "error": msg, "http_status": code, "error_type": error_type}


def _invoke(tool: str, arguments: dict[str, Any], epp_path: str | None,
            requires_confirmation: bool) -> dict[str, Any]:
    """POST invoke，返回 operation dict。HTTP 层异常向上抛，由 call_tool 统一映射。"""
    session_id = _resolve_session(epp_path)
    request_id = _stable_request_id(tool, arguments)
    body: dict[str, Any] = {
        "session_id": session_id,
        "request_id": request_id,
        "arguments": arguments,
    }
    if requires_confirmation:
        body["confirmation"] = {"confirmed_by_user": True, "confirmation_id": _confirm_id()}

    _log.info("tr_invoke tool=%s session=%s request=%s", tool, session_id[:8], request_id)
    resp = _post(f"{_BASE_URL}/api/v1/integration/tools/{tool}/invoke", body)

    if resp.status_code in (200, 201, 202):
        try:
            operation = resp.json()
        except Exception:
            return {"status": "failed", "error": f"SimulationAgent 返回无法解析: {resp.text[:200]}"}
        return operation if isinstance(operation, dict) else {"status": "failed", "error": "SimulationAgent 返回格式异常"}
    return _http_error_operation(resp)


def _poll_operation(operation_id: str) -> dict[str, Any]:
    """轮询 GET /operations/{id} 直到终态；超时或瞬时错误时返回 running 形态。"""
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while True:
        try:
            resp = _get(f"{_BASE_URL}/api/v1/integration/operations/{operation_id}")
        except httpx.HTTPError:
            # 瞬时错误：视为仍在运行，继续轮询直到超时
            resp = None
        if resp is not None and resp.status_code == 200:
            try:
                op = resp.json()
            except Exception:
                op = None
            if isinstance(op, dict) and op.get("status") not in ("queued", "running"):
                return op
        if time.monotonic() >= deadline:
            return {"status": "running", "operation_id": operation_id}
        time.sleep(_POLL_INTERVAL_SECONDS)


def _normalize(operation: dict[str, Any]) -> dict[str, Any]:
    """把 operation 归一化为 MCP 统一返回结构。"""
    status = operation.get("status")
    op_id = operation.get("operation_id")
    if status == "completed":
        result = operation.get("result", {})
        success = True
        if isinstance(result, dict) and "success" in result:
            success = bool(result["success"])
        return {
            "success": success,
            "status": "completed",
            "result": result,
            "operation_id": op_id,
        }
    if status in ("failed", "interrupted"):
        return error_response(
            "TR_TOOL_FAILED",
            operation.get("error") or f"工具执行 {status}",
            operation_id=op_id,
        )
    if status in ("queued", "running"):
        return {
            "success": False,
            "status": status,
            "operation_id": op_id,
            "message": "仿真仍在后台执行，请稍后重试相同调用（自动复用同一任务）",
        }
    return error_response("TR_TOOL_FAILED", f"未知 operation 状态: {status}", operation_id=op_id)


def call_tool(tool: str, arguments: dict[str, Any], *,
              requires_confirmation: bool = False) -> dict[str, Any]:
    """调用 SimulationAgent 工具并同步返回归一化结果。

    arguments 必须与 `/api/v1/integration/tools` 的 input_schema 一致；epp_path（若有）
    从 arguments 里取用于 session 解析。requires_confirmation=True 时注入确认门（默认通过）。
    """
    epp_path = arguments.get("epp_path")
    try:
        operation = _invoke(tool, arguments, epp_path, requires_confirmation)
    except httpx.ConnectError:
        return error_response(
            "TR_SERVICE_UNAVAILABLE",
            f"无法连接 SimulationAgent（{_BASE_URL}），请确认已启动。",
            retryable=True,
        )
    except httpx.TimeoutException:
        return error_response("TR_SERVICE_TIMEOUT", f"SimulationAgent 请求超时（{_TIMEOUT}s）", retryable=True)
    except httpx.RequestError as e:
        return error_response("TR_SERVICE_UNAVAILABLE", f"SimulationAgent 请求失败: {e}", retryable=True)
    except httpx.HTTPStatusError as e:
        return error_response(
            "TR_SERVICE_UNAVAILABLE",
            f"SimulationAgent 会话创建失败 (HTTP {e.response.status_code})",
        )
    except Exception as e:  # noqa: BLE001 — 兜底，避免异常穿透 MCP 工具
        return error_response("TR_SERVICE_UNAVAILABLE", f"SimulationAgent 调用异常: {e}")

    # 会话自愈：session 失效（SimulationAgent 重启）时重建会话并重试一次
    if operation.get("http_status") == 404 and _invalidate_session(epp_path):
        try:
            operation = _invoke(tool, arguments, epp_path, requires_confirmation)
        except Exception as e:  # noqa: BLE001
            return error_response("TR_SERVICE_UNAVAILABLE", f"SimulationAgent 重试失败: {e}")

    if operation.get("status") in ("queued", "running"):
        operation = _poll_operation(operation.get("operation_id"))
    return _normalize(operation)


def fetch_workflow() -> str:
    """实时拉取 /api/v1/integration/workflow 的 markdown；不可达时返回友好提示。"""
    url = f"{_BASE_URL}/api/v1/integration/workflow"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
        if resp.status_code == 200 and resp.text.strip():
            # 本 MCP 不暴露 tr_open_document（改用现有 open_document），把工作流里的引用重定向
            return resp.text.replace("tr_open_document", "open_document")
        _log.warning("tr_workflow fetch status=%d url=%s", resp.status_code, url)
        return (f"无法从 SimulationAgent 获取工作流说明 (HTTP {resp.status_code})，"
                f"请确认服务已启动：{_BASE_URL}")
    except httpx.HTTPError as e:
        _log.warning("tr_workflow fetch failed url=%s err=%s", url, e)
        return f"无法连接 SimulationAgent（{_BASE_URL}），请确认服务已启动。错误：{e}"
