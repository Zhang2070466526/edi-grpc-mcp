"""describe_tool 注册与返回形状的回归测试。

覆盖（对应「参数凑不齐 → 反编译」问题的修复点）：
  ① 已知工具：返回完整 docstring（长度 > 首行）+ 参数表（含数组元素提示）
  ② 复杂参数（run_mixer_simulation.links）：example 是「可直接照抄」的结构
  ③ 空 name：返回全量工具清单，数量 = 注册表实际数量
  ④ 拼错工具名：TOOL_NOT_FOUND + similar 建议（agent 可自愈）
  ⑤ schema 有 items={} 的参数：必须有 items_hint（否则 agent 无法构造）
"""

from __future__ import annotations

import json

import servers.registry_server  # noqa: F401  触发全部工具注册
from servers import mcp
from servers.describe_tool import _EXAMPLES, _param_rows, describe_tool
from servers.utils import registered_tools


def _tool_names() -> set[str]:

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
    # 名字像但**不是任何工具名的子串** → 走 difflib 相似名建议
    r = describe_tool("filter_order_lowpazz")
    assert r["success"] is False
    assert r["similar"] and r["similar"][0] == "filter_order_lowpass"


def test_every_handwritten_example_is_callable_shape():
    """手写示例必须只用真实参数名（防文档漂移），且覆盖到的工具都存在。"""

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


# ── 能力关键词检索（"我想算微带线宽" → 具体工具，而不是自己写脚本）────────────

def test_keyword_single_answer_returns_detail():
    r = describe_tool("微带线宽")
    assert r["success"] is True and r["matched_by"] == "keyword"
    assert r["detail"]["name"] == "calculate_transline"
    assert r["detail"]["parameters"], "命中后要能直接看到参数表"


def test_keyword_ambiguous_lists_candidates_without_picking():
    """滤波器阶数有低通/高通/带阻三个工具 → 不替调用方拍板，只给候选。"""
    r = describe_tool("滤波器阶数")
    assert r["matched_by"] == "keyword"
    names = [c["name"] for c in r["candidates"]]
    assert {"filter_order_lowpass", "filter_order_highpass", "filter_order_bandstop"} <= set(names)
    assert "detail" not in r, "分差不大时不应替调用方选定一个"
    assert "candidates" in r["hint"] or "summary" in r["hint"]


def test_keyword_never_returns_itself():
    for q in ("微带线宽", "滤波器阶数", "混频", "报告"):
        r = describe_tool(q)
        names = [c["name"] for c in (r.get("candidates") or [])]
        assert "describe_tool" not in names, f"{q}: 查询者不该出现在结果里"


def test_keyword_mixer_maps_to_mixer_tool():
    r = describe_tool("混频")
    assert r["detail"]["name"] == "run_mixer_simulation"


def test_prefix_typo_resolves_to_real_tool():
    """工具名前缀/少字母 → 直接给出正确工具的完整说明，而不是让调用方去翻源码。"""
    r = describe_tool("filter_order_lowpas")
    assert r["matched_by"] == "name_fuzzy"
    assert r["resolved_to"] == "filter_order_lowpass"
    assert r["detail"]["name"] == "filter_order_lowpass"


def test_keyword_without_match_is_tool_not_found():
    r = describe_tool("zzz_完全没有的能力_zzz")
    assert r["success"] is False and r["error_code"] == "TOOL_NOT_FOUND"


#: 已废弃的链路字段 —— 示例里绝不能再出现（新增废弃字段时加到这里）
_DEPRECATED_FIELDS = ("requirements",)


def test_examples_do_not_carry_deprecated_fields():
    """链路级 `requirements` 已于 2026-10-22 废弃（由 indicators + 报告判定取代）。

    示例是 agent 唯一会照抄的东西：留着废弃字段，agent 就会一直传它，
    而参数校验会静默忽略 —— 表现为"我传了却完全没生效"。
    """

    offenders = []
    for name, example in _EXAMPLES.items():
        body = json.dumps(example, ensure_ascii=False)
        for field in _DEPRECATED_FIELDS:
            if field in body:
                offenders.append(f"{name}: {field}")
    assert not offenders, "这些示例仍含已废弃字段: " + ", ".join(offenders)


def test_mixer_example_matches_current_signature():
    """示例里的顶层字段必须都是 run_mixer_simulation 的真实参数。"""

    tool = {t.name: t for t in registered_tools(mcp)}["run_mixer_simulation"]
    params = set(tool.fn_metadata.arg_model.model_fields)
    assert "requirements" not in params, "requirements 已从签名移除"
    assert set(_EXAMPLES["run_mixer_simulation"]) <= params, (
        f"示例含签名外字段: {set(_EXAMPLES['run_mixer_simulation']) - params}"
    )
