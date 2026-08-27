r"""聊天服务 — 会话管理、LLM 多轮调用、工具闭环、参数校验。

职责：
  - 会话创建/查询/清理（内存，2 小时 TTL，100 上限）
  - 构建带动态上下文的 system prompt
  - LLM 调用 → 工具执行 → 结果交回模型 → 最多 5 轮循环
  - 工具参数自动补齐（当前工程/task_id/"第一个"）
  - 重复调用保护、空参数拒绝

单例模式：ChatService.instance() 获取全局实例。
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("chat_service")

import httpx
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# 工具函数（直接导入，不走 MCP 装饰器开销）
# ---------------------------------------------------------------------------
from servers.eda.project_manage import (  # noqa: E402
    open_edi_project, close_edi_project,
    list_epp_projects,
    list_schematic_components, get_schematic_component_info,
    get_project_summary,
    analyze_variables,
)
from servers.eda.simulation_components import (  # noqa: E402
    get_simulation_component_schema, list_simulation_components,
    create_simulation_component, update_simulation_component,
    delete_simulation_component, set_component_active_state,
    generate_schematic_from_netlist,
    replace_port_component,
    replace_schematic_from_file,
)
from servers.eda.simulation import (  # noqa: E402
    start_simulation_async, get_simulation_async_status, get_simulation_async_result,
    list_eda_tasks,
)
from servers.eda.design_export import (  # noqa: E402
    export_project_netlist, capture_schematic,
)
from servers.eda.model_replace import replace_models_from_csv  # noqa: E402
from servers.eda.edi_launcher import launch_edi  # noqa: E402
from servers.turbocharts.compare_results import compare_simulation_results  # noqa: E402
from servers.turbocharts.convert_raw import turbocharts_convert, list_result_curves  # noqa: E402
from servers.multimodal_vision import show_image, copy_image_to_workspace, analyze_image, OPENCLAW_WORKSPACE_PATH, open_document, register_image_url  # noqa: E402
from servers.report import generate_simulation_report  # noqa: E402
from servers.settings import get_settings  # noqa: E402
from servers.metrics import record_tool_call  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_MAX_ROUNDS = 5          # 最多工具调用轮数
_MAX_MESSAGES = 30       # 每个会话最多保留消息数
_MAX_SESSIONS = 100      # 全局会话数上限
_SESSION_TTL = 7200      # 会话有效期 2 小时（秒）
_PRUNE_INTERVAL = 300    # 清理间隔 5 分钟
_MAX_MESSAGE_CHARS = 20_000   # 单条消息最大字符数
_MAX_SESSION_ID_CHARS = 128   # session_id 最大长度
_MAX_TOOL_CALLS_PER_ROUND = 8 # 单轮最多工具调用数
_MAX_TOOL_RESULT_CHARS = 100_000  # 工具返回最大字符数
_PENDING_ACTION_TTL = 300      # 待确认操作有效期 5 分钟

# 破坏性工具：Chat 层需用户确认后才执行（仅工具名匹配，参数条件在下方单独处理）
_DESTRUCTIVE_CHAT_TOOLS = {
    "delete_simulation_component",
    "replace_models_from_csv",
    "replace_port_component",
    "replace_schematic_from_file",   # 整体替换原理图，无条件确认
    "close_edi_project",          # 确认时需要 need_save=true
    "generate_simulation_report",  # 确认时需要 overwrite=true
}

# ---------------------------------------------------------------------------
# Chat 工具注册表 — 从 MCP 元数据自动生成，不手工维护第二套列表
# 排除：同步阻塞、ANSYS COM 依赖、需本地网表文件
# ---------------------------------------------------------------------------
_CHAT_EXCLUDED_TOOLS = {
    "simulate_project",          # 同步阻塞，不适合 Chat
    "simulate_netlist",          # 需要本地网表文件
    "simulate_netlist_with_ads", # 需要 ADS 安装
    "open_hfss_project",         # ANSYS COM 依赖
    "close_hfss_project",
    "launch_aedt",
    "get_hfss_project_info",
    "start_hfss_analysis_async",
    "get_hfss_analysis_status",
}

# 需要增强描述的工具（补充使用注意事项）
_CHAT_TOOL_DESCRIPTIONS: dict[str, str] = {
    "turbocharts_convert": (
        "ADS RAW 转曲线图和 CSV。"
        "多条 VSWR 自动拆分为多次导出。导出后核对行数列数"
    ),
    "show_image": "读取本地图片，返回 MCP ImageContent（不要自行生成 MEDIA）",
    "analyze_image": (
        "调用视觉模型分析图片内容（会上传到第三方）。"
        "仅用户明确要求分析时调用，显示用 show_image。"
        "prompt 参数尽量具体，如'详细描述图中的曲线数据、文字标注、数值范围'"
    ),
    "generate_simulation_report": (
        "生成本地仿真报告（PDF/DOCX）。"
        "只负责校验数据并调用渲染服务，不会自动仿真或编造数据"
    ),
    "open_document": (
        "打开本地文档：link 模式生成临时 HTTP 链接，local 模式系统默认程序打开。"
        "仅用户明确要求时调用，生成报告后不得自动打开"
    ),
    "delete_simulation_component": (
        "按实例名删除任意原理图器件及其连接线；删除直接由 EDI 执行"
    ),
    "set_component_active_state": (
        "确定性设置器件状态为 NORMAL、DISABLED 或 SHORTED，不是状态切换"
    ),
    "generate_schematic_from_netlist": (
        "从网表追加或重建 main 原理图；"
        "clear_before_import=true 会清空原理图，必须同时确认"
    ),
    "update_simulation_component": (
        "按实例名更新器件参数。SP/HB/XDB 支持校验，其他类型参数原样发送"
    ),
    "replace_port_component": (
        "替换端口器件类型（TermG-P_nToneG），服务端保留位置和连线"
    ),
    "create_simulation_component": (
        "使用 EDI 器件工厂默认参数创建器件。"
        "创建后根据 instance_name 调用 update 设置参数。支持任意 EDI 工厂类型"
    ),
    "get_simulation_component_schema": (
        "查询仿真控件支持的参数、类型和单位；配置控件前优先调用"
    ),
}


def _auto_build_chat_tools() -> tuple[dict[str, Any], list[dict]]:
    """从 MCP 工具注册表自动生成 Chat 工具映射和 OpenAI function-calling schema。

    FastMCP 的 t.parameters 已经是标准 JSON Schema，直接用作 function parameters。
    """
    from servers import mcp as _mcp

    tool_map: dict[str, Any] = {}
    schema: list[dict] = []

    for t in _mcp._tool_manager._tools.values():
        if t.name in _CHAT_EXCLUDED_TOOLS:
            continue

        tool_map[t.name] = t.fn

        desc = _CHAT_TOOL_DESCRIPTIONS.get(t.name, t.description or "")
        schema.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": desc,
                "parameters": t.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        })

    return tool_map, schema


CHAT_TOOL_MAP, CHAT_TOOLS_SCHEMA = _auto_build_chat_tools()


def _ensure_chat_tools() -> None:
    """每次 Chat 请求时刷新工具列表（原地更新，所有引用可见）。
    可选模块（如 knowledge）在 MCP 注册之后注入到 Chat 工具映射中。
    """
    new_map, new_schema = _auto_build_chat_tools()

    # 注入知识库工具（不注册为 MCP 工具，仅 Chat 内部可用）
    try:
        from servers.knowledge.rag.rag_mcp_tools import (
            search_knowledge, ask_knowledge, add_knowledge, list_knowledge_sources
        )
        new_map["search_knowledge"] = search_knowledge
        new_map["ask_knowledge"] = ask_knowledge
        new_map["add_knowledge"] = add_knowledge
        new_map["list_knowledge_sources"] = list_knowledge_sources
        new_schema.extend([
            {"type": "function", "function": {"name": "search_knowledge",
                "description": "语义检索知识库", "parameters": {"type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"]}}},
            {"type": "function", "function": {"name": "ask_knowledge",
                "description": "基于知识库生成回答（检索 + LLM）", "parameters": {"type": "object",
                "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
            {"type": "function", "function": {"name": "add_knowledge",
                "description": "向知识库追加文档", "parameters": {"type": "object",
                "properties": {"content": {"type": "string"}, "file_path": {"type": "string"},
                "source_name": {"type": "string"}}, "required": []}}},
            {"type": "function", "function": {"name": "list_knowledge_sources",
                "description": "列出已入库的文档来源", "parameters": {"type": "object",
                "properties": {}, "required": []}}},
        ])
    except Exception as exc:
        # 知识库是可选模块：依赖缺失（ImportError）或初始化异常（chroma.sqlite3 被锁/损坏等）
        # 都应降级跳过，不能拖垮整个 Chat
        _logger.warning("知识库工具注入失败，已跳过: %s", exc)

    CHAT_TOOL_MAP.clear()
    CHAT_TOOL_MAP.update(new_map)
    CHAT_TOOLS_SCHEMA.clear()
    CHAT_TOOLS_SCHEMA.extend(new_schema)

# 模块加载时填充一次
_ensure_chat_tools()

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Activity:
    """一次工具调用的摘要。"""
    tool: str
    label: str
    status: str          # "success" | "error"
    duration_ms: float = 0
    summary: str = ""     # 简短描述
    args: dict | None = None
    result: Any = None
    error: str = ""


@dataclass
class PendingAction:
    """待确认的破坏性操作。"""
    action_id: str
    tool_name: str
    arguments: dict[str, Any]
    summary: str
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def __post_init__(self):
        """未显式指定 expires_at 时按 TTL 计算过期时间。"""
        if not self.expires_at:
            self.expires_at = self.created_at + _PENDING_ACTION_TTL


@dataclass
class ChatSession:
    """会话状态。"""
    session_id: str
    messages: list[dict] = field(default_factory=list)
    current_project_path: str | None = None
    current_project_name: str | None = None
    last_folder_path: str | None = None
    last_projects: list[dict] = field(default_factory=list)
    last_simulation_task_id: str | None = None
    simulation_task_ids: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    pending_action: PendingAction | None = None
    chat_lock: Any = field(default_factory=lambda: asyncio.Lock())


@dataclass
class ChatResponse:
    """Chat 响应数据类，包含回复文本、工具执行摘要、上下文和媒体。"""
    success: bool
    session_id: str
    request_id: str
    reply: str
    activities: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    media: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为 dict，供 JSON 响应返回。"""
        return {
            "success": self.success,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "reply": self.reply,
            "activities": self.activities,
            "context": self.context,
            "media": self.media,
        }


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是本地 EDI 工程助手，请使用中文回答。\n\n"
    "规则：\n"
    "1. 不得虚构文件路径、工程名称、元件或仿真结果。\n"
    "2. 工具必填参数缺失时，先询问用户，不要使用空字符串或猜测的路径调用。\n"
    "3. 如果提供了当前工程，\"当前工程\"\"这个工程\"均指该路径。\n"
    "4. 执行仿真时优先调用 start_simulation_async。仿真日志通过 FetchEvent 长连接增量推送，"\
    "用户询问\"当前日志\"\"仿真输出\"\"失败原因\"时，使用最近一次 task_id 调用 "\
    "get_simulation_async_result 获取 ads_output 分析。不得调用 LOG_EVENT，不得为查询日志重新启动仿真。\n"
    "5. 工具执行成功后，用自然语言概括结果，不要直接输出原始 JSON。\n"
    "6. 工具失败时说明失败原因和下一步，不要反复使用相同参数调用。\n"
    "7. 用户说\"第一个\"\"第二个\"时，根据最近一次工程列表解析。\n"
    "8. 除非用户要求，不展示内部工具名称、参数和调用细节。\n"
    "9. analyze_image 返回的图片分析结果，用 Markdown 结构化输出："
    "表格展示列表数据，加粗标题，分节呈现，不直接粘贴原始内容。\n"
    "10. 有数据时优先用表格，有代码时用代码块，有步骤时用有序列表。\n"
    "11. 调用工具有默认值或会产生输出文件时，先告知用户将采用的默认值或输出位置，"
    "并询问是否需要调整。例如：\"我将把原理图截图保存到 C:/screenshots/circuit.png，需要改位置请告诉我。\""
)


def _system_with_context(session: ChatSession) -> str:
    """拼接系统提示词 + 当前会话上下文（工程/目录/仿真任务）。"""
    ctx_parts = []
    if session.current_project_path:
        ctx_parts.append(f"当前工程：{session.current_project_name or session.current_project_path}")
    else:
        ctx_parts.append("当前工程：未选择")
    if session.last_folder_path:
        ctx_parts.append(f"最近扫描目录：{session.last_folder_path}")
    if session.last_simulation_task_id:
        ctx_parts.append(f"最近仿真任务：{session.last_simulation_task_id}")
    ctx = "\n".join(ctx_parts)
    return f"{_SYSTEM_PROMPT}\n\n当前上下文：\n{ctx}"


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------

ERROR_CODES = {
    "MISSING_REQUIRED_ARGUMENT": "缺少必填参数",
    "INVALID_PATH": "路径格式不正确",
    "FILE_NOT_FOUND": "文件不存在",
    "EDA_GRPC_OFFLINE": "EDA gRPC 服务未启动",
    "TOOL_TIMEOUT": "工具执行超时",
    "TOOL_EXECUTION_FAILED": "工具执行失败",
    "DUPLICATE_TOOL_CALL": "重复调用保护",
    "TOOL_LOOP_LIMIT": "超过最大工具调用轮数",
    "SIMULATION_ALREADY_RUNNING": "工程已有仿真正在运行",
    "LLM_REQUEST_FAILED": "模型接口请求失败",
    "NO_CURRENT_PROJECT": "当前没有选定工程",
}


def _tool_error(code: str, detail: str = "") -> dict:
    """构建工具调用错误响应。"""
    return {
        "success": False,
        "error": {
            "code": code,
            "message": ERROR_CODES.get(code, code),
            "detail": detail,
        },
    }


# ---------------------------------------------------------------------------
# ChatService 单例
# ---------------------------------------------------------------------------

class ChatService:
    """聊天服务 — 单例，管理会话和工具调用闭环。"""

    _instance: ChatService | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """初始化会话字典、会话锁和清理时间戳。"""
        self._sessions: dict[str, ChatSession] = {}
        self._session_lock = threading.Lock()
        self._last_prune = time.time()

    @classmethod
    def instance(cls) -> ChatService:
        """返回全局单例（双重检查锁）。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 会话管理 ──

    def _get_or_create(self, session_id: str) -> ChatSession | None:
        """获取已有会话，或新建。返回 None 表示 session_id 失效（如重启后）。"""
        self._prune()
        sid = session_id.strip() if session_id else ""
        with self._session_lock:
            if sid and sid in self._sessions:
                s = self._sessions[sid]
                s.updated_at = time.time()
                return s
            if sid:
                # 客户端携带了旧 session_id 但服务端已不存在（重启/TTL）
                return None
            # 空 session_id → 新建
            new_id = uuid.uuid4().hex[:12]
            s = ChatSession(session_id=new_id)
            self._sessions[new_id] = s
            return s

    def _prune(self) -> None:
        """按 TTL 清理过期会话，超过上限时删最旧的。"""
        now = time.time()
        if now - self._last_prune < _PRUNE_INTERVAL:
            return
        self._last_prune = now
        with self._session_lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if not s.chat_lock.locked() and now - s.updated_at > _SESSION_TTL
            ]
            for sid in expired:
                del self._sessions[sid]
            # 超过上限则删最旧的
            if len(self._sessions) > _MAX_SESSIONS:
                sorted_ids = sorted(
                    self._sessions.keys(),
                    key=lambda sid: self._sessions[sid].updated_at,
                )
                for sid in sorted_ids[:len(self._sessions) - _MAX_SESSIONS]:
                    del self._sessions[sid]

    # ── 主入口 ──

    async def chat(self, session_id: str, message: str) -> ChatResponse:
        """主入口：输入校验 → 会话锁串行 → 处理确认/正常消息。"""
        request_id = uuid.uuid4().hex[:12]

        # ── 输入校验 ──
        if len(session_id) > _MAX_SESSION_ID_CHARS:
            return ChatResponse(success=False, session_id="", request_id=request_id,
                                reply="session_id 过长。")
        if len(message) > _MAX_MESSAGE_CHARS:
            return ChatResponse(success=False, session_id=session_id,
                                request_id=request_id,
                                reply=f"消息过长（最大 {_MAX_MESSAGE_CHARS} 字符）。")

        session = self._get_or_create(session_id)
        if session is None:
            # 旧 session 失效（重启/过期），自动创建新会话，对用户透明
            _logger.warning("MCP_SESSION_NOT_FOUND session=%s — auto-creating new", session_id[:12])
            session = self._get_or_create("")
            session_id = session.session_id  # 更新为新 session_id

        # ── 会话锁：同 session 串行（先锁，再处理确认/正常消息）──
        try:
            await asyncio.wait_for(session.chat_lock.acquire(), timeout=10)
        except asyncio.TimeoutError:
            return ChatResponse(success=False, session_id=session.session_id,
                                request_id=request_id,
                                reply="当前会话正在处理上一条请求，请稍后重试。")

        try:
            # 确认处理：在锁内原子取走 pending
            if session.pending_action:
                pending = session.pending_action
                msg_lower = _norm_confirmation(message)
                if self._is_confirmation(msg_lower, pending):
                    session.pending_action = None  # 原子取走
                    return await self._execute_pending(session, pending, request_id)
                if _is_cancel(msg_lower):
                    session.pending_action = None
                    return ChatResponse(success=True, session_id=session.session_id,
                                        request_id=request_id, reply="已取消操作。")
                # 消息不匹配 — 再次提示
                return ChatResponse(success=True, session_id=session.session_id,
                                    request_id=request_id,
                                    reply=_confirmation_text(pending))
            return await self._chat_locked(
                session, message, request_id,
            )
        finally:
            session.chat_lock.release()

    async def _chat_locked(
        self, session: ChatSession, message: str, request_id: str,
    ) -> ChatResponse:
        """已持会话锁：执行多轮 LLM 工具调用闭环（最多 _MAX_ROUNDS 轮）。"""
        _ensure_chat_tools()  # 每次请求刷新，包含可选模块（如 knowledge）
        activities: list[Activity] = []
        called_fingerprints: set[str] = set()
        media: list[dict] = []
        t_start = time.time()

        _logger.info("request=%s session=%s chat started msg_len=%d",
                      request_id, session.session_id[:12], len(message))

        # 构建 messages
        system_content = _system_with_context(session)
        if not session.messages or session.messages[0].get("role") != "system":
            session.messages.insert(0, {"role": "system", "content": system_content})
        else:
            session.messages[0]["content"] = system_content
        session.messages.append({"role": "user", "content": message})

        llm_cfg = get_settings()
        api_key = llm_cfg.llm_api_key
        base_url = llm_cfg.llm_base_url
        model = llm_cfg.llm_model

        if not all([api_key, base_url, model]):
            return ChatResponse(
                success=False, session_id=session.session_id,
                request_id=request_id,
                reply="Chat 不可用。请在 .env 中配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。",
            )

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                for _round in range(_MAX_ROUNDS):
                    resp = await client.post(
                        f"{base_url}/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "max_tokens": 2048,
                            "messages": session.messages,
                            "tools": CHAT_TOOLS_SCHEMA,
                        },
                    )

                    if resp.status_code != 200:
                        _logger.error("request=%s llm_status=%d body=%s",
                                      request_id, resp.status_code, resp.text[:500])
                        return ChatResponse(
                            success=False, session_id=session.session_id,
                            request_id=request_id,
                            reply=f"LLM 调用失败: {resp.status_code}: {resp.text[:200]}",
                        )

                    data = resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    msg = choice.get("message", {})
                    assistant_text = msg.get("content", "")
                    tool_calls = msg.get("tool_calls", [])

                    # 无 tool_calls → 最终回复
                    if not tool_calls:
                        session.messages.append({
                            "role": "assistant",
                            "content": assistant_text or "操作已完成。",
                        })
                        self._trim_messages(session)
                        _logger.info("request=%s chat finished rounds=%d total_duration=%dms",
                                     request_id, _round + 1,
                                     round((time.time() - t_start) * 1000))
                        return ChatResponse(
                            success=True, session_id=session.session_id,
                            request_id=request_id,
                            reply=assistant_text or "操作已完成。",
                            media=media, activities=[a.__dict__ for a in activities],
                            context=self._build_context(session),
                        )

                    # 单轮工具数上限
                    if len(tool_calls) > _MAX_TOOL_CALLS_PER_ROUND:
                        return ChatResponse(
                            success=False, session_id=session.session_id,
                            request_id=request_id,
                            reply=f"模型一次请求了过多操作（{len(tool_calls)} 个），已停止。",
                        )

                    # 破坏性工具检查：必须是本轮唯一调用
                    destructive = []
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args = _safe_json(fn.get("arguments", "{}"))
                        if name in _DESTRUCTIVE_CHAT_TOOLS:
                            # 参数感知确认：仅在实际会造成破坏时才拦截
                            if name == "close_edi_project" and not args.get("need_save"):
                                continue
                            if name == "generate_simulation_report" and not args.get("overwrite"):
                                continue
                            destructive.append(tc)
                        elif name == "generate_schematic_from_netlist":
                            if args.get("clear_before_import"):
                                destructive.append(tc)
                    if destructive:
                        if len(tool_calls) > 1:
                            return ChatResponse(
                                success=False, session_id=session.session_id,
                                request_id=request_id,
                                reply="破坏性操作不能与其他操作同时执行，请单独请求。",
                            )
                        tc = destructive[0]
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "")
                        tool_args = _safe_json(fn.get("arguments", "{}"))
                        is_valid, validation_result = self._validate(tool_name, tool_args, session)
                        if not is_valid:
                            return ChatResponse(success=False, session_id=session.session_id,
                                                request_id=request_id,
                                                reply=f"参数校验失败: {validation_result.get('error', {}).get('detail', '')}",
                                                context=self._build_context(session))
                        # 清空原理图：Chat 层补 confirm_clear=True，不信任模型传入
                        if tool_name == "generate_schematic_from_netlist":
                            validation_result["confirm_clear"] = True
                        pending = _create_pending(tool_name, validation_result)
                        session.pending_action = pending
                        return ChatResponse(
                            success=True, session_id=session.session_id,
                            request_id=request_id,
                            reply=_confirmation_text(pending),
                            context=self._build_context(session),
                        )

                    # 有 tool_calls → 写入历史并执行
                    session.messages.append({
                        "role": "assistant",
                        "content": assistant_text or "",
                        "tool_calls": tool_calls,
                    })

                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name", "")
                        tool_args = _safe_json(fn.get("arguments", "{}"))

                        is_valid, validation_result = self._validate(
                            tool_name, tool_args, session
                        )
                        act = Activity(
                            tool=tool_name,
                            label=_tool_label(tool_name),
                            status="success" if is_valid else "error",
                        )
                        t0 = time.time()

                        if not is_valid:
                            act.error = validation_result.get("error", {}).get("detail", "")
                            act.status = "error"
                            act.result = validation_result
                            tool_result = validation_result
                        else:
                            # 重复调用保护 — 仅限本轮，下一条用户消息重置
                            if _is_duplicate_tool_call(called_fingerprints, tool_name, validation_result):
                                act.status = "error"
                                act.error = "本轮对话中相同参数重复调用"
                                tool_result = _tool_error("DUPLICATE_TOOL_CALL")
                            else:
                                try:
                                    func = CHAT_TOOL_MAP[tool_name]
                                    result = await asyncio.to_thread(func, **validation_result)
                                    # 将 TextContent 列表转为纯文本，避免 JSON 序列化报错
                                    if isinstance(result, list):
                                        act.result = "\n".join(r.text for r in result if hasattr(r, "text"))
                                    else:
                                        act.result = result
                                    if isinstance(result, dict) and not result.get("success", True):
                                        act.status = "error"
                                        act.error = result.get("message", "")
                                    self._update_context(session, tool_name, validation_result, result)
                                    tool_result = result
                                except Exception as exc:
                                    act.status = "error"
                                    act.error = str(exc)
                                    tool_result = _tool_error("TOOL_EXECUTION_FAILED", str(exc))

                        act.duration_ms = round((time.time() - t0) * 1000)
                        act.summary = _result_summary(act)

                        # 记录运行时指标（次数/失败/耗时，供 /metrics 端点）
                        record_tool_call(tool_name, act.status == "success", act.duration_ms)

                        # 记录关键参数（脱敏：不记录完整路径中的用户名）
                        tool_args_log = _tool_args_summary(tool_name, validation_result)
                        _logger.info("request=%s tool=%s args=%s duration=%dms success=%s",
                                     request_id, tool_name, tool_args_log, act.duration_ms,
                                     act.status == "success")

                        # show_image：用原始 image_path 注册 HTTP URL 供前端渲染
                        if tool_name == "show_image" and act.status == "success":
                            img_path = validation_result.get("image_path", "")
                            if img_path:
                                image_url = register_image_url(img_path)
                                if image_url:
                                    media.append({"type": "image", "url": image_url, "name": Path(img_path).name})

                        activities.append(act)

                        # 工具结果交回模型（截断过长内容）
                        serialized = _serialize_result(tool_result)
                        session.messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "name": tool_name,
                            "content": serialized,
                        })

                # 超过最大轮数
                _logger.warning("request=%s tool loop limit reached rounds=%d",
                                request_id, _MAX_ROUNDS)
                return ChatResponse(
                    success=False, session_id=session.session_id,
                    request_id=request_id,
                    reply=f"工具调用轮数超过上限（{_MAX_ROUNDS} 轮），请简化你的问题或补充必要信息。",
                    media=media, activities=[a.__dict__ for a in activities],
                    context=self._build_context(session),
                )

        except httpx.RequestError as exc:
            _logger.error("request=%s llm_request_failed error=%s", request_id, exc)
            return ChatResponse(
                success=False, session_id=session.session_id,
                request_id=request_id,
                reply=f"模型接口连接失败: {exc}",
            )
        except Exception:
            _logger.exception("request=%s chat internal error", request_id)
            return ChatResponse(
                success=False, session_id=session.session_id,
                request_id=request_id,
                reply="聊天服务内部错误，请重试。",
                media=media, activities=[a.__dict__ for a in activities],
            )

    # ── 工具校验 ──

    def _validate(self, tool_name: str, args: dict, session: ChatSession) -> tuple[bool, dict]:
        """校验工具参数，必要时自动补齐。返回 (is_valid, args_or_error_dict)。"""
        func = CHAT_TOOL_MAP.get(tool_name)
        if func is None:
            return False, _tool_error("TOOL_EXECUTION_FAILED", f"未知工具: {tool_name}")

        if isinstance(args, list):
            return False, _tool_error("INVALID_PATH", f"{tool_name} 的参数格式错误，不应为数组")

        # 通用：空字符串拒绝
        for key, val in args.items():
            if isinstance(val, str) and val.strip() == "":
                if key in ("project_path", "folder_path", "netlist_path",
                           "raw_path", "img_path", "csv_path", "image_path",
                           "component_id", "instance_name", "result_paths", "curve"):
                    return False, _tool_error("MISSING_REQUIRED_ARGUMENT",
                                              f"{key} 不能为空，请提供正确的 {key}")

        # 自动补齐 project_path（仅对需要工程路径的工具）
        _PROJECT_PATH_TOOLS = {
            "open_edi_project", "close_edi_project",
            "get_project_summary", "analyze_variables",
            "capture_schematic", "export_project_netlist",
            "start_simulation_async", "simulate_project",
            "list_simulation_components",
            "list_schematic_components", "get_schematic_component_info",
            "create_simulation_component", "update_simulation_component",
            "delete_simulation_component", "set_component_active_state",
            "generate_schematic_from_netlist",
            "replace_models_from_csv",
            "replace_port_component", "attach_out_component",
            "replace_schematic_from_file",
        }
        if tool_name in _PROJECT_PATH_TOOLS:
            if not args.get("project_path"):
                if session.current_project_path:
                    args = dict(args, project_path=session.current_project_path)
                else:
                    return False, _tool_error("NO_CURRENT_PROJECT",
                                              "当前没有选定工程，请先提供工程路径或让我扫描目录")

        # 自动补齐仿真 task_id
        if tool_name in ("get_simulation_async_status", "get_simulation_async_result"):
            if not args.get("task_id"):
                if session.last_simulation_task_id:
                    args = dict(args, task_id=session.last_simulation_task_id)
                else:
                    return False, _tool_error("MISSING_REQUIRED_ARGUMENT",
                                              "没有可查询的仿真任务，请先执行 start_simulation_async")

        return True, args

    # ── 上下文更新 ──

    def _update_context(self, session: ChatSession, tool_name: str, args: dict, result: Any) -> None:
        """工具执行成功后更新会话上下文。"""
        if not isinstance(result, dict) or not result.get("success"):
            return

        if tool_name == "list_epp_projects":
            session.last_folder_path = args.get("folder_path")
            session.last_projects = result.get("projects", [])

        elif tool_name == "open_edi_project":
            session.current_project_path = args.get("project_path")
            session.current_project_name = (
                Path(args["project_path"]).stem if args.get("project_path") else None
            )

        elif tool_name == "close_edi_project":
            session.current_project_path = None
            session.current_project_name = None

        elif tool_name == "start_simulation_async":
            task_id = result.get("task_id")
            if task_id:
                session.last_simulation_task_id = task_id
                session.simulation_task_ids.append(task_id)
                session.simulation_task_ids = session.simulation_task_ids[-20:]

    # ── 上下文导出 ──

    # ── 破坏性工具确认（ChatService 方法）──

    def _is_confirmation(self, text: str, pending: PendingAction) -> bool:
        """判断用户输入是否为确认（肯定词或「确认 action_id」）。"""
        norm = _norm_confirmation(text)
        return norm in _CONFIRM_WORDS or norm == f"确认 {pending.action_id}"

    async def _execute_pending(
        self, session: ChatSession, pending: PendingAction, request_id: str,
    ) -> ChatResponse:
        """执行已确认的破坏性操作（过期则拒绝）。"""
        if time.time() > pending.expires_at:
            return ChatResponse(success=False, session_id=session.session_id,
                                request_id=request_id, reply="操作已过期，请重新发起。")
        func = CHAT_TOOL_MAP.get(pending.tool_name)
        if func is None:
            return ChatResponse(success=False, session_id=session.session_id,
                                request_id=request_id,
                                reply=f"未知工具: {pending.tool_name}")
        try:
            result = await asyncio.to_thread(func, **pending.arguments)
        except Exception as exc:
            return ChatResponse(success=False, session_id=session.session_id,
                                request_id=request_id, reply=f"执行失败: {exc}")

        # 判断工具实际是否成功
        tool_success = not (isinstance(result, dict) and result.get("success") is False)
        result_text = _json.dumps(result, ensure_ascii=False)
        if len(result_text) > 2000:
            result_text = result_text[:2000] + "\n...[truncated]"
        reply = f"操作已完成。\n\n```json\n{result_text}\n```" if tool_success \
            else f"操作执行失败。\n\n```json\n{result_text}\n```"

        # 更新上下文 + 记录活动
        self._update_context(session, pending.tool_name, pending.arguments, result)

        return ChatResponse(
            success=tool_success, session_id=session.session_id,
            request_id=request_id, reply=reply,
            context=self._build_context(session),
        )

    def _build_context(self, session: ChatSession) -> dict:
        """构建响应中的上下文（当前工程/仿真状态/日志尾部）。"""
        sim_status = None
        sim_ads_output = ""
        sim_log_complete = False
        if session.last_simulation_task_id:
            try:
                status_result = get_simulation_async_status(session.last_simulation_task_id)
                sim_status = status_result.get("status")
                sim_ads_output = status_result.get("ads_output", "")
                sim_log_complete = status_result.get("log_complete", False)
            except Exception:
                pass
        return {
            "current_project_name": session.current_project_name,
            "simulation_task_id": session.last_simulation_task_id,
            "simulation_status": sim_status,
            "simulation_ads_output_tail": sim_ads_output[-2000:] if sim_ads_output else "",
            "simulation_log_complete": sim_log_complete,
        }

    # ── 消息裁剪 ──

    def _trim_messages(self, session: ChatSession) -> None:
        """按完整轮次裁剪，不从 tool_calls 中间切断。"""
        messages = session.messages
        if len(messages) <= _MAX_MESSAGES:
            return

        system_msgs = [m for m in messages if m.get("role") == "system"]
        conversation = [m for m in messages if m.get("role") != "system"]

        # 从截断点向前查找最近的 user 消息边界
        start = max(0, len(conversation) - _MAX_MESSAGES)
        while start > 0 and conversation[start].get("role") != "user":
            start -= 1

        session.messages = system_msgs + conversation[start:]


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _is_duplicate_tool_call(
    called: set[str],
    tool_name: str,
    arguments: dict[str, Any],
) -> bool:
    """检查本轮是否已用相同参数调用过该工具。"""
    fingerprint = (
        f"{tool_name}:"
        f"{_json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"
    )
    if fingerprint in called:
        return True
    called.add(fingerprint)
    return False


def _safe_json(raw: str) -> dict:
    """安全解析 JSON，解析失败返回空 dict。"""
    try:
        val = _json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (_json.JSONDecodeError, TypeError):
        return {}


_TOOL_LABELS: dict[str, str] = {
    "analyze_variables": "分析变量",
    "list_epp_projects": "扫描工程",
    "open_edi_project": "打开工程",
    "close_edi_project": "关闭工程",
    "get_project_summary": "工程概览",
    "start_simulation_async": "启动仿真",
    "get_simulation_async_status": "查询进度",
    "get_simulation_async_result": "获取结果",
    "list_eda_tasks": "任务列表",
    "capture_schematic": "截图原理图",
    "export_project_netlist": "导出网表",
    "replace_models_from_csv": "替换模型",
    "launch_edi": "启动 EDI",
    "get_service_status": "服务状态",
    "compare_simulation_results": "对比结果",
    "list_result_curves": "RAW 曲线",
    "turbocharts_convert": "RAW 转图",
    "show_image": "显示图片",
    "analyze_image": "分析图片",
    "generate_simulation_report": "生成报告",
    "open_document": "打开文档",
    "get_simulation_component_schema": "控件参数",
    "list_simulation_components": "查询器件",
    "list_schematic_components": "实时器件列表",
    "get_schematic_component_info": "查器件详情",
    "create_simulation_component": "新增器件",
    "update_simulation_component": "更新器件",
    "delete_simulation_component": "删除器件",
    "set_component_active_state": "设置状态",
    "generate_schematic_from_netlist": "生成原理图",
    "replace_port_component": "替换端口",
    "attach_out_component": "挂载Out器件",
    "replace_schematic_from_file": "替换原理图",
    "cst_solve_async": "CST求解",
    "cst_solve_query": "CST求解查询",
    "cst_export_snp": "CST导出S参数",
    "cst_export_farfield": "CST导出远场方向图",
    "cst_export_farfield_query": "CST导出远场查询",
}
if OPENCLAW_WORKSPACE_PATH is not None:
    _TOOL_LABELS["copy_image_to_workspace"] = "复制到工作区"


def _tool_args_summary(tool_name: str, args: dict) -> str:
    """提取工具调用的关键参数用于日志（脱敏）。"""
    key_fields = {
        "list_epp_projects": ["folder_path"],
        "open_edi_project": ["project_path"],
        "start_simulation_async": ["project_path"],
        "turbocharts_convert": ["chart_type", "linename"],
        "analyze_image": ["image_path"],
        "generate_simulation_report": ["output_path", "model_name"],
        "capture_schematic": ["project_path"],
        "compare_simulation_results": ["curve", "alignment"],
        "show_image": ["image_path"],
        "delete_simulation_component": ["instance_name"],
        "update_simulation_component": ["instance_name"],
    }
    fields = key_fields.get(tool_name, ["project_path"])
    parts = []
    for f in fields:
        val = args.get(f, "")
        if val and isinstance(val, str) and "\\" in str(val):
            # 只记录文件名，不暴露完整路径中的用户名
            parts.append(f"{f}={Path(str(val)).name}")
        elif val:
            parts.append(f"{f}={val}")
    return " ".join(parts) if parts else ""


def _tool_label(name: str) -> str:
    """返回工具的中文显示标签。"""
    return _TOOL_LABELS.get(name, name)


# ── 破坏性工具确认（ChatService 方法）──

_CONFIRM_WORDS: set[str] = {"确认", "是", "yes", "ok", "执行", "继续", "confirm", "好的", "可以", "行"}
_CANCEL_WORDS: set[str] = {"取消", "不要执行", "no", "cancel"}


def _norm_confirmation(text: str) -> str:
    """归一化确认输入（去空格、小写），用于匹配确认/取消词。"""
    return " ".join(text.strip().lower().split())


def _create_pending(tool_name: str, args: dict) -> PendingAction:
    """为破坏性操作生成待确认对象（含 action_id 和操作摘要）。"""
    summary = {
        "delete_simulation_component":
            f"从工程中删除器件 {args.get('instance_name', '?')} 及其连接线",
        "replace_models_from_csv":
            f"使用 {args.get('csv_path', '?')} 批量替换工程模型",
        "close_edi_project":
            f"关闭工程 {args.get('project_path', '?')} 并保存修改",
        "generate_simulation_report":
            f"覆盖已有报告文件 {args.get('output_path', '?')}",
        "replace_port_component":
            f"替换端口器件 {args.get('target_instance_name', '?')} 为 {args.get('replacement_component_type', '?')}",
        "replace_schematic_from_file":
            f"用 {args.get('schematic_path', '?')} 整体替换当前原理图",
    }.get(tool_name, f"执行 {tool_name}")
    return PendingAction(
        action_id=uuid.uuid4().hex[:8],
        tool_name=tool_name,
        arguments=args,
        summary=summary,
    )


def _confirmation_text(pending: PendingAction) -> str:
    """生成破坏性操作的确认提示文案。"""
    target = {
        "delete_simulation_component": f"{pending.summary}。此操作无法由 MCP 自动恢复。",
        "replace_models_from_csv": f"{pending.summary}。此操作将修改工程中的模型。",
        "close_edi_project": f"{pending.summary}。保存后无法撤销。",
        "generate_simulation_report": f"{pending.summary}。原文件将被覆盖。",
        "replace_port_component": f"{pending.summary}。此操作将替换器件并重新连线。",
        "replace_schematic_from_file": f"{pending.summary}。当前原理图将被整体替换，原内容丢失。",
    }.get(pending.tool_name, pending.summary)
    return (
        f"⚠️ **确认操作**\n\n{target}\n\n"
        f"回复 **确认** 继续，或回复 **取消** 放弃。"
    )


def _is_cancel(text: str) -> bool:
    """检查用户输入是否为取消操作。"""
    return _norm_confirmation(text) in _CANCEL_WORDS


def _serialize_result(result: Any) -> str:
    """序列化工具结果为字符串，超过限制截断。"""
    if isinstance(result, list):
        text_parts = []
        for item in result:
            if hasattr(item, "text"):
                text_parts.append(item.text)
        serialized = "\n".join(text_parts) if text_parts else _json.dumps(result, ensure_ascii=False)
    else:
        serialized = _json.dumps(result, ensure_ascii=False)
    if len(serialized) > _MAX_TOOL_RESULT_CHARS:
        serialized = serialized[:_MAX_TOOL_RESULT_CHARS] + "\n...[tool result truncated]"
    return serialized


def _result_summary(act: Activity) -> str:
    """根据 Activity 生成简洁结果摘要。"""
    if act.status == "error":
        return act.error or "执行失败"
    r = act.result
    if isinstance(r, dict):
        if r.get("success"):
            if "count" in r:
                return f"找到 {r['count']} 个工程"
            if "total" in r:
                return f"共 {r['total']} 个元件"
            if "task_id" in r:
                return f"任务: {r['task_id']}"
            return "OK"
        return r.get("message", "执行失败")[:80]
    return "OK"
