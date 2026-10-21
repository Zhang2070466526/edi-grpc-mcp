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
                "requirements": {"变频增益": "≥ 20"},
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


@mcp.tool()
def describe_tool(name: str = "") -> dict:
    """查看某个工具要哪些参数、怎么填、最小可运行示例 —— 参数拿不准时先调它，别猜也别读源码。

    用法：`describe_tool("run_mixer_simulation")` → 拿到完整 docstring、参数表（含数组元素
    结构提示）、可直接照抄的 example；`name` 留空返回全部工具名+一句话说明。

    为什么要有它：MCP 只把 docstring 首行发给模型，且 list 型参数的元素结构在 schema 里是空的
    （如 links / stopband_specs）⇒ 不看这个工具就只能猜参数、猜不中就去翻仓库。

    Args:
        name: 工具名（与 tools/list 里一致）。留空返回全部工具清单（name + 首行说明）。

    Returns:
        {"success": True, "name": ..., "summary": 首行, "doc": 完整 docstring,
         "parameters": [{name,type,required,default,description,items_hint?}],
         "example": {...}, "example_source": "handwritten|skeleton", "notes": [...]}
        工具不存在 → TOOL_NOT_FOUND + similar（相似名建议）。
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
    if tool is None:
        similar = difflib.get_close_matches(key, list(tools), n=3, cutoff=0.6)
        resp = error_response(
            "TOOL_NOT_FOUND",
            f"没有名为 {key!r} 的工具" + (f"；你是不是想要：{', '.join(similar)}" if similar else ""),
            hint="describe_tool(\"\") 可列出全部工具名",
        )
        # 顶层直给（不放 details）：agent 读一眼就能改名重试
        resp["similar"] = similar
        resp["total_tools"] = len(tools)
        return resp

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
