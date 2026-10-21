"""describe_tool 注册与返回形状的回归测试。

覆盖（对应「参数凑不齐 → 反编译」问题的修复点）：
  ① 已知工具：返回完整 docstring（长度 > 首行）+ 参数表（含数组元素提示）
  ② 复杂参数（run_mixer_simulation.links）：example 是「可直接照抄」的结构
  ③ 空 name：返回全量工具清单，数量 = 注册表实际数量
  ④ 拼错工具名：TOOL_NOT_FOUND + similar 建议（agent 可自愈）
  ⑤ schema 有 items={} 的参数：必须有 items_hint（否则 agent 无法构造）
"""

from __future__ import annotations

import servers.registry_server  # noqa: F401  触发全部工具注册
from servers.describe_tool import _EXAMPLES, _param_rows, describe_tool


def _tool_names() -> set[str]:
    from servers import mcp
    from servers.utils import registered_tools

    return {t.name for t in registered_tools(mcp)}


def test_known_tool_returns_full_doc_and_params():
    r = describe_tool("filter_order_lowpass")
    assert r["success"] is True
    assert r["name"] == "filter_order_lowpass"
    # 首行 vs 完整 docstring：必须给完整的那份
    assert len(r["doc"]) > len(r["summary"]) * 3
    assert "Args:" in r["doc"] or "Args" in r["doc"]
    names = {p["name"] for p in r["parameters"]}
    assert {"f_p_ghz", "stopband_specs"} <= names
    by_name = {p["name"]: p for p in r["parameters"]}
    assert by_name["f_p_ghz"]["required"] is True
    assert by_name["f_p_ghz"]["description"]          # 参数说明非空
    assert by_name["rl_db"]["default"] == -20.0


def test_complex_array_param_has_items_hint_and_example():
    """links 的 schema items 是 {} —— 必须给 items_hint + 手写示例。"""
    r = describe_tool("run_mixer_simulation")
    assert r["success"] is True and r["example_source"] == "handwritten"
    links = next(p for p in r["parameters"] if p["name"] == "links")
    assert links["type"] == "array"
    assert "items_hint" in links            # schema 未声明元素结构时的兜底提示

    ex = r["example"]
    assert isinstance(ex["links"], list) and ex["links"]
    link = ex["links"][0]
    assert {"name", "direction", "f_rf", "f_if", "bw", "pin", "p_lo", "stages"} <= set(link)
    assert {"mixer", "lo_port"} <= set(link["stages"][0])


def test_empty_name_lists_all_tools():
    r = describe_tool("")
    assert r["success"] is True
    assert r["count"] == len(_tool_names())
    assert {"describe_tool", "run_mixer_simulation"} <= {t["name"] for t in r["tools"]}
    assert all(t["summary"] for t in r["tools"])


def test_unknown_tool_gives_similar_names():
    r = describe_tool("filter_order_lowpas")     # 少一个 s
    assert r["success"] is False
    assert r["error_code"] == "TOOL_NOT_FOUND"
    assert r["similar"] and "filter_order_lowpass" in r["similar"]


def test_every_handwritten_example_is_callable_shape():
    """手写示例必须只用真实参数名（防文档漂移），且覆盖到的工具都存在。"""
    from servers import mcp
    from servers.utils import registered_tools

    tools = {t.name: t for t in registered_tools(mcp)}
    for name, ex in _EXAMPLES.items():
        assert name in tools, f"示例表中的工具不存在：{name}"
        props = set((tools[name].parameters or {}).get("properties") or {})
        unknown = set(ex) - props
        assert not unknown, f"{name} 的示例含未知参数：{unknown}"


def test_param_rows_marks_untyped_items():
    """items={} 必须转成 items_hint，不能静默丢掉。"""
    schema = {"type": "object",
              "properties": {"links": {"type": "array", "items": {}}},
              "required": ["links"]}
    rows = _param_rows(schema)
    assert rows[0]["items_hint"]
