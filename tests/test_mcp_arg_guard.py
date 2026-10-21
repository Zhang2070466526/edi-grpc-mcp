"""参数守门 + 默认 hint 的回归测试。

对应「agent 参数凑不齐就翻源码」的两处修复：
  ① 缺参不再回 SDK 英文 ToolError，而是 `MISSING_REQUIRED_ARGUMENT` + missing/next_actions
  ② 参数名拼错时给出相似名建议（unexpected）
  ③ 284 处 error_response 默认 hint 变成可执行动作（不再只有 do_not_retry）
"""

from __future__ import annotations

import asyncio

import pytest

import servers.registry_server  # noqa: F401  注册全部工具 + 安装守门
from servers import mcp


def _call(name: str, arguments: dict) -> dict:
    """走真实 ToolManager 路径（与 MCP 客户端调用一致）。"""
    return asyncio.run(mcp._tool_manager.call_tool(name, arguments))


# ── ① 缺参 ───────────────────────────────────────────────────

def test_missing_required_returns_structure():
    r = _call("open_edi_project", {})
    assert r["success"] is False
    assert r["error_code"] == "MISSING_REQUIRED_ARGUMENT"
    assert r["missing"] == ["project_path"]
    assert r["tool"] == "open_edi_project"
    # 必须告诉 agent 下一步做什么
    assert r["next_actions"] == [{"tool": "describe_tool", "arguments": {"name": "open_edi_project"}}]
    assert "describe_tool" in r["hint"]
    assert r["received"] == []


def test_missing_multiple_fields_all_listed():
    r = _call("filter_order_lowpass", {})
    assert r["error_code"] == "MISSING_REQUIRED_ARGUMENT"
    assert set(r["missing"]) == {"f_p_ghz", "stopband_specs"}


# ── ② 参数名拼错 ─────────────────────────────────────────────

def test_misspelled_param_gets_suggestion():
    r = _call("open_edi_project", {"projectpath": "C:/x.epp"})
    assert r["error_code"] == "MISSING_REQUIRED_ARGUMENT"
    assert r["missing"] == ["project_path"]
    assert r["unexpected"].get("projectpath") == "project_path"
    assert "是否想传 project_path" in r["message"]


def test_unknown_param_without_close_match():
    r = _call("open_edi_project", {"whatever_xyz": 1})
    assert r["unexpected"] == {"whatever_xyz": None}
    assert r["missing"] == ["project_path"]


# ── ③ 类型错 ─────────────────────────────────────────────────

def test_wrong_type_returns_invalid_parameters():
    r = _call("open_edi_project", {"project_path": "C:/x.epp", "timeout_seconds": "abc"})
    assert r["error_code"] == "INVALID_PARAMETERS"
    assert r["invalid"] and r["invalid"][0]["field"] == "timeout_seconds"
    assert r["missing"] == []


# ── ④ 正常参数不被拦截 ────────────────────────────────────────

def test_valid_args_not_intercepted():
    """参数齐全时守门必须放行（交给业务逻辑，哪怕业务层随后报错）。"""
    r = _call("filter_order_lowpass", {"f_p_ghz": 1.0, "stopband_specs": [[2.0, 40.0]]})
    assert r.get("success") is True and r["N"] >= 3


# ── ⑤ 默认 hint 表 ───────────────────────────────────────────

def test_default_hints_are_actionable():
    from servers.utils import error_response

    for code in ("INVALID_PARAMETERS", "PATH_NOT_FOUND", "PROJECT_NOT_FOUND", "NO_SOLUTION"):
        hint = error_response(code, "x")["hint"]
        assert hint not in ("do_not_retry", "retry_safe"), f"{code} 仍是无信息的默认 hint"

    # 未知码保持旧行为（不破坏既有调用方与测试）
    assert error_response("SOME_UNKNOWN_CODE", "x")["hint"] == "do_not_retry"
    assert error_response("SOME_UNKNOWN_CODE", "x", retryable=True)["hint"] == "retry_safe"
    # 显式 hint 永远优先
    assert error_response("INVALID_PARAMETERS", "x", hint="自定义")["hint"] == "自定义"


@pytest.mark.parametrize("code", ["MISSING_REQUIRED_ARGUMENT", "DEPENDENCY_MISSING", "CURVE_NOT_FOUND"])
def test_hints_registered_in_chat_dictionary(code):
    """chat 词典必须覆盖守门会返回的码，否则 chat 里显示裸英文码。"""
    from servers.chat.service import ERROR_CODES

    assert code in ERROR_CODES
