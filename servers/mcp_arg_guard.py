"""MCP 参数守门 —— 缺参/参数名拼错时回统一信封，而不是 SDK 的英文校验堆栈。

为什么要有它（2026-10-21 实测）：

    open_edi_project() 缺 project_path 时，FastMCP 直接抛
        ToolError("Error executing tool open_edi_project: 1 validation error ... project_path Field required")
    → MCP 客户端只能拿到 `isError: true` + 一段英文堆栈：没有 `missing` 字段、没有
      「去哪查参数」的提示 ⇒ agent 只能瞎猜参数或去翻仓库/反编译。这是「参数凑不齐」
      最主要的技术原因。

做法：包装 `ToolManager.call_tool`，在真正执行前用 `tool.fn_metadata.arg_model` 先校验一次；
失败就地返回与业务工具**同构**的 dict 信封（走 structuredContent，`isError` 保持 false）：

    {"success": False,
     "error_code": "MISSING_REQUIRED_ARGUMENT",     # 词典里已有的码，不是新造
     "missing": ["project_path"],                    # 缺哪些
     "unexpected": {"projectpath": "project_path"},  # 拼错的参数 + 相似名建议
     "invalid": [{"field": "timeout_seconds", "reason": "..."}],
     "hint": "参数说明与可抄示例：describe_tool(\\"open_edi_project\\")",
     "next_actions": [{"tool": "describe_tool", "arguments": {"name": "open_edi_project"}}]}

命中条件只限「参数校验失败」：工具名不存在、工具内部报错等一律交还原逻辑处理。
"""

from __future__ import annotations

import difflib
from typing import Any

from pydantic import ValidationError

from servers import mcp
from servers.utils import error_response

#: 用实例反查具体类，避免依赖 SDK 内部的模块路径（不同版本搬过家）
_ToolManager = type(mcp._tool_manager)
_ORIG_CALL_TOOL = _ToolManager.call_tool


def _build_envelope(tool_name: str, arguments: dict, model: Any,
                    exc: ValidationError) -> dict[str, Any]:
    """把 pydantic 校验错误翻译成 agent 能直接照做的信封。"""
    fields = list(getattr(model, "model_fields", {}) or {})
    missing: list[str] = []
    invalid: list[dict[str, str]] = []
    for err in exc.errors():
        loc = tuple(err.get("loc") or ())
        field = str(loc[0]) if loc else ""
        if err.get("type") == "missing":
            if field and field not in missing:
                missing.append(field)
        else:
            invalid.append({"field": field, "reason": str(err.get("msg", ""))})

    unexpected: dict[str, str | None] = {}
    for key in arguments:
        if key not in fields:
            close = difflib.get_close_matches(key, fields, n=1, cutoff=0.6)
            unexpected[key] = close[0] if close else None

    parts: list[str] = []
    if missing:
        parts.append("缺少必填参数：" + "、".join(missing))
    if invalid:
        parts.append("参数取值不合法：" + "、".join(f"{i['field']}（{i['reason']}）" for i in invalid))
    if unexpected:
        parts.append("无法识别的参数：" + "、".join(
            (f"{k} → 是否想传 {v}？" if v else k) for k, v in unexpected.items()))

    resp = error_response(
        "MISSING_REQUIRED_ARGUMENT" if missing else "INVALID_PARAMETERS",
        "；".join(parts) or "参数校验未通过",
        hint=f'参数说明与可抄示例：describe_tool("{tool_name}")',
    )
    resp["tool"] = tool_name
    resp["missing"] = missing
    if invalid:
        resp["invalid"] = invalid
    if unexpected:
        resp["unexpected"] = unexpected
    resp["received"] = sorted(arguments)
    resp["next_actions"] = [{"tool": "describe_tool", "arguments": {"name": tool_name}}]
    return resp


async def _guarded_call_tool(self, name: str, arguments: dict[str, Any],
                             context: Any = None, convert_result: bool = False) -> Any:
    tool = None
    getter = getattr(self, "get_tool", None)
    if callable(getter):
        try:
            tool = getter(name)
        except Exception:  # noqa: BLE001 — 取不到工具就交还原逻辑报「未知工具」
            tool = None

    if tool is not None and isinstance(arguments, dict):
        model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
        if model is not None:
            try:
                model.model_validate(arguments)
            except ValidationError as exc:
                return _build_envelope(tool.name or name, arguments, model, exc)
    return await _ORIG_CALL_TOOL(self, name, arguments, context, convert_result)


def install() -> None:
    """幂等安装（重复 import 不会叠加包装）。"""
    if _ToolManager.call_tool is not _guarded_call_tool:
        _ToolManager.call_tool = _guarded_call_tool


install()
