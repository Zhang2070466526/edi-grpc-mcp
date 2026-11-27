"""prompt 入参 —— `analyze_log` 为什么只收字符串。

MCP 协议规定 `prompts/get` 的 `arguments` 是 `string -> string`：客户端传 bool / int 会在
协议层被 `-32602 Invalid request parameters` 拒掉（实测：连 `execution_mode=True` 这种
普通 str 参数也一样被拒），函数体根本进不去。所以这里只验「字符串真值/假值都解析对」，
以及注解保持 `str`（窄成 str 或 `str | bool` 都不影响协议层行为，别再当成可修的 bug）。
"""

from __future__ import annotations

import typing

from servers.resources_prompts.prompts_simulation import prompt_run_and_review_simulation as fn


def _text(msgs) -> str:
    out = []
    for m in msgs:
        c = m["content"]
        out.append(c if isinstance(c, str) else (c.get("text") or ""))
    return "".join(out)


def _flag(v) -> str:
    return [ln for ln in _text(fn("C:/x.epp", "async", v)).splitlines() if "分析日志" in ln][0]


def test_truthy_strings():
    for v in ("true", "TRUE", " yes ", "1", "on"):
        assert "分析日志：是" in _flag(v), v


def test_falsy_strings():
    for v in ("false", "No", "0", "off"):
        assert "分析日志：否" in _flag(v), v


def test_default_analyzes():
    assert "分析日志：是" in _text(fn("C:/x.epp"))


def test_annotation_stays_str():
    hints = typing.get_type_hints(fn)
    assert hints["analyze_log"] is str or hints["analyze_log"] == str
