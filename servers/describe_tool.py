"""工具自描述 —— 一次调用拿到「完整参数说明 + 可抄示例 + 常见坑」，替代读源码/反编译。

为什么需要它（实测依据，2026-10-21）：

* MCP `tools/list` 只暴露 docstring **首行**（`Tool.description`，例：`filter_order_lowpass`
  只有 24 字），完整 docstring（603 字，含「用法」、Args、Returns）agent 看不到。
* 参数级 description 已由 FastMCP 从 docstring 的 `Args:` 自动生成（81/84 个工具非空），
  所以**简单参数能填对**。
* 但 `list` 型参数的元素结构是 `{"items": {}}` —— `run_mixer_simulation.links` 要
  `name/direction/f_rf/f_if/bw/pin/p_lo` + `stages[]`，`filter_order_*` 的
  `stopband_specs` 要 `[f, A]` 或 `[f_lo, f_hi, A]`，schema 里一个字都没有 ⇒
  agent 只能猜，猜不中就跑去翻仓库/反编译。

本模块把这些缺口补上：`describe_tool(name)` 回完整 docstring + 参数表（含数组元素提示）
+ **最小可运行示例**（复杂参数手写，其余自动骨架）+ 相似名建议（工具名拼错也能自愈）。
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from servers import mcp
from servers.utils import error_response, registered_tools

#: 一次列出全部工具名时的上限（84 个工具全名 + 首行 ≈ 6 KB，对 agent 可接受）
_MAX_NAME_LIST = 120

#: 手写示例：只覆盖「结构复杂 / 高频被问」的工具；其余走自动骨架。
#: 原则：示例必须能直接被调用（路径类用占位，但字段名/层次/类型全对）。
_EXAMPLES: dict[str, Any] = {
    # ── 独立仿真（三层 JSON，最容易卡住）────────────────────────────
    "run_mixer_simulation": {
        "epp_path": "C:/Users/<user>/EDI-Workspace/projects/11/11.epp",
        "links": [
            {
                "name": "下变频链路",
                "direction": "down",
                "f_rf": [33, 37],
                "f_if": 3.9,
                "bw": 0.4,
                "pin": -30,
                "p_lo": 4,
                "channels": [{"name": "CH1", "rf_port": 1, "if_port": 2, "mixer": "MIX1"}],
                "stages": [
                    {"mixer": "MIX1", "lo_port": 5, "relation": "diff",
                     "injection": "low", "f_if": 3.9}
                ],
                "simulation": {"max_order": 5, "step": 0.1},
            }
        ],
        "indicators": "变频增益,本振泄漏",
    },
    "export_simulation_report": {
        "report": {
            "title": "混频链路仿真报告",
            "links": [{"name": "下变频链路", "electrical_parameters": [
                {"indicator": "变频增益", "minimum": "25.19", "typical": "26.45",
                 "maximum": "27.10", "unit": "dB"}
            ]}],
        },
        "output_dir": "C:/Users/<user>/edi_tmp/report_out",
    },
    "run_tr_simulation": {
        "epp_path": "C:/Users/<user>/EDA-PMDS-Workspace/projects/11/11.epp",
        "links": [{"name": "接收链路", "min_freq": 29, "max_freq": 31,
                   "input_port": 1, "output_port": 2}],
        "indicators": "输出增益,噪声系数",
    },
    # ── 微波本地计算 ────────────────────────────────────────────
    "filter_order_lowpass": {
        "f_p_ghz": 1.0,
        "stopband_specs": [[2.0, 40], [3.0, 40]],   # 也可 [[2.0, 3.0, 40]] 频段形式
        "rl_db": -20.0,
        "max_notches": 4,
    },
    "filter_order_highpass": {
        "f_p_ghz": 1.0,
        "stopband_specs": [[0.3, 0.5, 40]],          # 频率须 < f_p_ghz
        "rl_db": -20.0,
    },
    "filter_order_bandstop": {
        "f_s1_ghz": 2.1,
        "f_s2_ghz": 2.2,
        "stopband_specs": 20,                        # 浮点=整段抑制；也可 [[2.1, 2.2, 20]]
        "passband_specs": [2.0, 1, 2.3, 1],          # [f_p1, A_p1, f_p2, A_p2]，须 f_p1<f_s1<f_s2<f_p2
        "q_unloaded": 500,
    },
    "wilkinson_marchand_order": {"ratio": 6, "rl_db": 15},   # rl_db 是正数（隔离度 dB）
    "calculate_transline": {"transline_type": "microstrip", "er": 4.4, "freq": 2.45,
                            "z0": 50, "h": 0.8},
    "design_wilkinson": {"h_mm": 0.8, "er": 4.4, "f0_ghz": 2.45, "n_sections": 2,
                         "ripple_db": 20},
    # ── 工程 / 器件 ────────────────────────────────────────────
    "open_edi_project": {"project_path": "C:/Users/<user>/EDI-Workspace/projects/11/11.epp"},
    "add_ideal_component": {"project_path": "C:/Users/<user>/EDA-PMDS-Workspace/projects/11/11.epp",
                            "component_type": "P_nToneG", "position": {"x": 0, "y": 0}},
    "add_wire": {"first_instance_name": "PORT1", "first_pin_index": 0,
                 "second_instance_name": "AMP1", "second_pin_index": 0},
    "update_simulation_component": {"project_path": "C:/path/11.epp",
                                    "instance_name": "SP1",
                                    "parameters": {"Start": {"value": "29", "unit": "GHz"}}},
    "create_simulation_component": {"project_path": "C:/path/11.epp",
                                     "component_type": "SParameter"},
    # ── 模型库 / 拓扑 / 软 IP ──────────────────────────────────
    "search_public_models": {"sub_type": "62", "filters": [{"key": "gain", "min": 20}],
                             "limit": 10},
    "design_chip": {"description": "2阶PCB带通", "save_path": "C:/Users/<user>/edi_tmp/chip.aedt",
                    "freq": 16.675535},
    "design_schematic_topology": {"requirement": "设计一个 Ka 波段八通道 TR 组件的收发链路"},
    # ── 出图 / 报告 ────────────────────────────────────────────
    "turbocharts_convert": {"raw_path": "C:/path/history/result.raw",
                            "output_path": "C:/Users/<user>/edi_tmp/curve.png",
                            "chart_type": "SP", "linename": "DB_S[2,1]"},
    "list_result_curves": {"result_path": "C:/path/history/result.raw"},
    "compare_simulation_results": {"result_paths": ["C:/a/result.raw", "C:/b/result.raw"],
                                   "curve": "DB_S[2,1]",
                                   "output_path": "C:/Users/<user>/edi_tmp/compare.png"},
    # ── CST / HFSS ────────────────────────────────────────────
    "cst_solve_async": {"model_path": "C:/Users/<user>/Desktop/CST/demo_horn_24GHz.cst"},
    "open_hfss_project": {"project_path": "C:/Users/<user>/Desktop/demo.aedt"},
}


def _type_of(spec: dict) -> str:
    """把 JSON Schema 片段压成一个可读类型串。"""
    if "type" in spec:
        return str(spec["type"])
    if "anyOf" in spec:
        return " | ".join(str(x.get("type", "?")) for x in spec["anyOf"])
    return "?"


def _param_rows(schema: dict | None) -> list[dict[str, Any]]:
    """把 inputSchema 摊平成参数表，并对「数组元素结构缺失」显式标注。"""
    schema = schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    rows: list[dict[str, Any]] = []
    for pname, spec in props.items():
        if not isinstance(spec, dict):
            spec = {}
        row: dict[str, Any] = {
            "name": pname,
            "type": _type_of(spec),
            "required": pname in required,
            "description": (spec.get("description") or "").strip(),
        }
        if "default" in spec:
            row["default"] = spec["default"]
        items = spec.get("items")
        if isinstance(items, dict):
            if not items:  # {"items": {}} —— agent 拿不到元素结构，这是最常踩的坑
                row["items_hint"] = ("数组元素结构未在 schema 中声明；"
                                     "请照本工具 example 里的同名字段填")
            else:
                row["items_type"] = _type_of(items)
                if items.get("properties"):
                    row["items_fields"] = list(items["properties"].keys())
        rows.append(row)
    return rows


_PLACEHOLDER = {"string": "<string>", "integer": 0, "number": 0.0,
                "boolean": False, "array": [], "object": {}}


def _skeleton(schema: dict | None) -> dict[str, Any]:
    """无手写示例时，按 required 生成占位骨架（只保证字段名/类型对）。"""
    schema = schema or {}
    props = schema.get("properties") or {}
    out: dict[str, Any] = {}
    for pname in schema.get("required") or []:
        spec = props.get(pname) or {}
        base = spec.get("type")
        if base is None and "anyOf" in spec:
            base = (spec["anyOf"][0] or {}).get("type")
        out[pname] = _PLACEHOLDER.get(base, "<value>")
    return out


# ── 能力关键词检索（"我想算微带线宽" → calculate_transline）─────────────────
_KEYWORD_NAME_BONUS = 6.0     # 工具名里直接含该词（压倒性，避免"描述里引用了别的工具名"而反超）
_KEYWORD_PHRASE_BONUS = 2.5   # 整个查询串出现在描述/文档里
_KEYWORD_DESC_BONUS = 1.0     # 单个检索词出现在描述里
_KEYWORD_DOC_BONUS = 0.3      # 只出现在完整 docstring 里（弱信号）
_KEYWORD_LIMIT = 5
_KEYWORD_ROUTE_SCORE = 10.0   # 直连表命中的分数
_KEYWORD_AMBIGUOUS_RATIO = 1.5  # top1 领先不足 1.5 倍 → 不替调用方拍板，只给候选

#: 高频能力 → 工具直连。通用打分对"这个词组在两个工具的描述里都合理"无能为力
#: （实测："微带线宽" 会先命中 design_wilkinson，因为它的描述写了"输出各级微带线宽"），
#: 所以把最常用的一批做成显式路由；未命中再走通用打分兜底。
_KEYWORD_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("滤波器阶数", "阶数估计", "几阶", "要几阶"), ("filter_order_lowpass", "filter_order_highpass", "filter_order_bandstop")),
    (("微带线宽", "线宽", "阻抗计算", "传输线", "微带"), ("calculate_transline",)),
    (("功分器", "威尔金森", "wilkinson", "隔离电阻", "功分"), ("design_wilkinson", "wilkinson_marchand_order")),
    (("混频", "变频", "混频器", "本振", "镜像抑制"), ("run_mixer_simulation",)),
    (("噪声系数", "tr仿真", "收发链路", "p-1db", "抗烧毁"), ("run_tr_simulation",)),
    (("s参数", "sparameter", "曲线", "画图", "raw"), ("list_result_curves", "turbocharts_convert")),
    (("报告", "pdf", "docx", "交付"), ("export_simulation_report", "run_mixer_simulation")),
    (("查器件", "型号", "封装", "器件参数", "成本"), ("search_public_models", "search_personal_models", "get_components_static_params")),
)


def _norm(text: str) -> str:
    """小写并去掉空白 —— "S参数" 要能匹配 "S 参数"。"""
    return re.sub(r"\s+", "", text.lower())


def _keyword_terms(query: str) -> set[str]:
    """拆检索词：空格/标点分隔 + 中文 2-gram（"微带线宽" → 微带/带线/线宽）。"""
    terms = {_norm(t) for t in re.split(r"[\s,，、/]+", query) if t.strip()}
    for chunk in list(terms):
        if len(chunk) >= 3 and all("\u4e00" <= c <= "\u9fff" for c in chunk):
            terms |= {chunk[i:i + 2] for i in range(len(chunk) - 1)}
    return {t for t in terms if t}


def _route_hits(query: str, tools: dict[str, Any]) -> list[str]:
    """直连表：把高频能力映射到确定的工具（按表内顺序，只保留确实存在的工具）。"""
    q = _norm(query)
    if not q:
        return []
    for keys, names in _KEYWORD_ROUTES:
        if any(_norm(k) in q for k in keys):
            return [n for n in names if n in tools]
    return []


def _keyword_match(query: str, tools: dict[str, Any],
                   limit: int = _KEYWORD_LIMIT) -> list[tuple[float, str]]:
    """按「你想做什么」给工具打分，返回 [(score, name)] 降序。

    两层：① `_route_hits` 直连表（高频能力，最可靠）；② 通用打分兜底。
    权重刻意做成「名字 ≫ 描述 > 完整 docstring」：docstring 很长、几乎什么都含；
    描述里又常引用别的工具名（互指），所以名字命中必须压倒性。
    describe_tool 自身不参与打分（查询者不该出现在结果里）。
    """
    q = _norm(query or "")
    if not q:
        return []

    scored: dict[str, float] = {}
    for name in _route_hits(query, tools):
        scored[name] = _KEYWORD_ROUTE_SCORE

    terms = _keyword_terms(query)
    for name, tool in tools.items():
        if name == "describe_tool":
            continue
        name_l = _norm(name)
        desc = _norm(tool.description or "")
        doc = _norm(tool.fn.__doc__ or "")
        score = scored.get(name, 0.0)
        if q in name_l:
            score += _KEYWORD_NAME_BONUS
        elif q in desc:
            score += _KEYWORD_PHRASE_BONUS + _KEYWORD_DESC_BONUS
        elif q in doc:
            score += _KEYWORD_PHRASE_BONUS * 0.5
        for term in terms:
            if term in name_l:
                score += _KEYWORD_DESC_BONUS
            elif term in desc:
                score += _KEYWORD_DESC_BONUS
            elif term in doc:
                score += _KEYWORD_DOC_BONUS
        if score > 0:
            scored[name] = score

    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[tuple[float, str]] = [(s, n) for n, s in ranked[:limit]]

    # 直连表命中时，把通用打分的前几名补进候选（让调用方看到"还有别的相关工具"）
    if out and out[0][0] >= _KEYWORD_ROUTE_SCORE:
        taken = {n for _, n in out}
        out += [(s, n) for n, s in ranked if n not in taken][: max(0, limit - len(out))]
    return out


def _build_detail(tool: Any, key: str) -> dict[str, Any]:
    """产出 describe_tool 的详情结构（精确命中与关键词命中共用，避免两套实现漂移）。"""
    doc = (tool.fn.__doc__ or "").strip()
    example = _EXAMPLES.get(key)
    source = "handwritten"
    if example is None:
        example = _skeleton(tool.parameters)
        source = "skeleton"
    notes = [
        "example 的路径类字段是占位（C:/path/...），换成你机器上的真实路径。",
        "参数说明里若提到「必须 <」「必须 >」等关系，先校验再调用，别等工具报错。",
    ]
    if source == "skeleton":
        notes.append("本工具暂无手写示例，example 是按必填字段生成的占位骨架；"
                     "数组/对象字段请按 parameters 里的 items_hint 与 doc 填写。")
    return {
        "success": True,
        "name": key,
        "summary": (tool.description or "").strip(),
        "doc": doc,
        "parameters": _param_rows(tool.parameters),
        "example": example,
        "example_source": source,
        "notes": notes,
    }


@mcp.tool()
def describe_tool(name: str = "") -> dict:
    """查工具参数/示例，或按能力关键词找工具（留空 name=列全部）；先查它，别猜也别读源码。

    用法：
    - `describe_tool("run_mixer_simulation")` → 完整 docstring、参数表（含数组元素结构提示）、
      可直接照抄的 example。
    - `describe_tool("微带线宽")` / `describe_tool("滤波器阶数")` → **按能力检索**：返回候选工具
      （带分数）与最匹配那个的完整说明。不知道工具名时用这条，别自己写脚本算。
    - `name` 留空 → 全部工具名 + 一句话说明。

    为什么要有它：MCP 只把 docstring 首行发给模型，且 list 型参数的元素结构在 schema 里是空的
    （如 links / stopband_specs）⇒ 不看这个工具就只能猜参数、猜不中就去翻仓库。

    Args:
        name: 工具名（与 tools/list 一致）或能力关键词（如 "微带线宽"）。留空返回全部工具清单。

    Returns:
        精确命中 → {"success": True, "name", "summary", "doc", "parameters", "example",
                    "example_source", "notes"}
        关键词命中 → {"success": True, "matched_by": "keyword", "candidates", "detail", "hint"}
        都不命中 → TOOL_NOT_FOUND（附 similar 相似名建议）。
    """
    tools = {t.name: t for t in registered_tools(mcp)}
    key = (name or "").strip()

    # 空 name：给出全量清单（agent 想知道「有哪些工具」时不必去翻 /tools/list）
    if not key:
        items = [
            {"name": n, "summary": (t.description or "").strip().splitlines()[0][:80] if t.description else ""}
            for n, t in sorted(tools.items())
        ]
        return {
            "success": True,
            "count": len(items),
            "tools": items[:_MAX_NAME_LIST],
            "message": f"共 {len(tools)} 个工具；传入具体 name 查看参数与示例",
        }

    tool = tools.get(key)
    if tool is not None:
        return _build_detail(tool, key)

    # 精确名没命中：名字命中（拼错/前缀）优先，其次能力关键词，最后相似名建议
    hits = _keyword_match(key, tools)

    # 只有「名字里真的含这个串」才算名字命中（拼错/前缀）；
    # 直连表命中的 10 分不能走这条，否则 3 个同分工具会被随便挑一个当答案。
    if hits and _norm(key) in _norm(hits[0][1]):
        top = hits[0][1]
        return {
            "success": True,
            "matched_by": "name_fuzzy",
            "query": key,
            "resolved_to": top,
            "hint": f"没有名为 {key!r} 的工具；名字最接近的是 {top}，已返回它的完整说明",
            "candidates": [{"name": n, "score": round(s, 1),
                            "summary": (tools[n].description or "").strip()} for s, n in hits],
            "detail": _build_detail(tools[top], top),
        }

    similar = difflib.get_close_matches(key, list(tools), n=3, cutoff=0.6)
    if similar:
        resp = error_response(
            "TOOL_NOT_FOUND",
            f"没有名为 {key!r} 的工具；你是不是想要：{', '.join(similar)}",
            hint='describe_tool("") 可列出全部工具名',
        )
        resp["similar"] = similar
        resp["total_tools"] = len(tools)
        return resp

    if hits:
        body: dict[str, Any] = {
            "success": True,
            "matched_by": "keyword",
            "query": key,
            "candidates": [
                {"name": n, "score": round(s, 1),
                 "summary": (tools[n].description or "").strip()}
                for s, n in hits
            ],
        }
        # 分差太小说明「这个词组在多个工具里都合理」→ 不替调用方拍板，只给候选
        ambiguous = len(hits) > 1 and hits[0][0] < hits[1][0] * _KEYWORD_AMBIGUOUS_RATIO
        if ambiguous:
            body["hint"] = ("多个工具都与该能力相关，未替你选定：先看 candidates 的 summary 选一个，"
                            "再 describe_tool(candidates[].name) 拿参数与示例。")
        else:
            body["detail"] = _build_detail(tools[hits[0][1]], hits[0][1])
            body["hint"] = (f"按能力关键词命中 {len(hits)} 个工具，detail 是最匹配的那个；"
                            "换工具就把 candidates[].name 传给 describe_tool。")
        return body

    resp = error_response(
        "TOOL_NOT_FOUND",
        f"没有名为 {key!r} 的工具，也没有匹配「{key}」的能力",
        hint='describe_tool("") 列出全部工具名；或换一个能力关键词，如 "微带线宽"、"滤波器阶数"',
    )
    resp["total_tools"] = len(tools)
    return resp
