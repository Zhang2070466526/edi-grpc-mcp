"""`tools/list` 的入参结构：裸 dict 参数必须变成「有字段的」schema。

2026-11-26：`position: dict` / `parameters: dict` / `series: list[dict]` 这类注解在
`tools/list` 里只能生成 `{"type": "object", "additionalProperties": true}`，调用方看不到
字段名、类型和必填关系，只能靠报错反推（`run_mixer_simulation` 那次花了 3 轮 / 38.5 万 tokens）。

这里锁住三层：
1. `servers/param_models.py` 声明的每个参数，schema 里都能看到字段；
2. 注入后不留悬空 `$ref`（客户端不跟 `$defs` 的坑）；
3. 必填关系不变（`position`/`series` 仍必填，`marks`/`report` 仍可选）。

改这些工具的签名或 `param_models` 的表之后跑本文件。
"""

import json

import pytest

# (工具, 参数, 必须出现的字段)
CASES = [
    ("add_ideal_component", "position", {"x", "y"}),
    ("add_performance_component", "position", {"x", "y"}),
    ("update_schematic_components_transform", "components",
     {"uuid", "instance_name", "position", "rotation_deg", "mirrored_horizontal", "mirrored_vertical"}),
    ("update_components_from_schematic", "parameters", {"value", "unit"}),
    ("replace_port_component", "parameters", {"value", "unit"}),
    ("analyze_mixer_spur", "mixers",
     {"name", "enabled", "relation", "rf_center_mhz", "rf_bandwidth_mhz", "if_bandwidth_mhz",
      "rf_power_dbm", "lo_power_dbm"}),
    ("find_netlist_paths", "pairs", {"input", "output"}),
    ("draw_chart", "series", {"name", "points"}),
    ("draw_chart", "marks", {"series", "point", "label"}),
    ("export_simulation_report", "report",
     {"model_name", "description", "conclusion", "components", "links", "charts", "schematic_path"}),
]

#: 注入后仍应保持「必填」的参数（其余是可选）
STILL_REQUIRED = {
    ("add_ideal_component", "position"),
    ("add_performance_component", "position"),
    ("update_schematic_components_transform", "components"),
    ("analyze_mixer_spur", "mixers"),
    ("find_netlist_paths", "pairs"),
    ("draw_chart", "series"),
}


@pytest.fixture(scope="module")
def tools():
    import servers.registry_server  # noqa: F401 —— 导入即注册全部工具
    from servers import mcp as m
    from servers.utils import registered_tools

    return {t.name: t for t in registered_tools(m)}


def _props(tools, tool, param):
    params = tools[tool].parameters or {}
    props = params.get("properties") or {}
    assert param in props, f"{tool} 没有参数 {param}"
    return props[param]


def _object_node(schema):
    """把「数组元素 / 字典值 / anyOf 分支」统一降到带 properties 的那一层。"""
    node = schema
    if isinstance(node.get("anyOf"), list):
        node = next((a for a in node["anyOf"] if a.get("properties") or a.get("type") == "object"), node)
    if node.get("type") == "array":
        node = node.get("items") or {}
    if isinstance(node.get("anyOf"), list):
        node = next((a for a in node["anyOf"] if a.get("properties") or a.get("type") == "object"), node)
    if node.get("type") == "object" and not node.get("properties"):
        node = node.get("additionalProperties") or {}
    return node


@pytest.mark.parametrize("tool,param,fields", CASES, ids=[f"{t}.{p}" for t, p, _ in CASES])
def test_param_schema_exposes_fields(tools, tool, param, fields):
    node = _object_node(_props(tools, tool, param))
    missing = fields - set(node.get("properties") or {})
    assert not missing, f"{tool}.{param} 的 schema 缺字段 {sorted(missing)}；实际 {node}"


@pytest.mark.parametrize("tool,param,fields", CASES, ids=[f"{t}.{p}" for t, p, _ in CASES])
def test_no_dangling_ref(tools, tool, param, fields):
    dumped = json.dumps(_props(tools, tool, param), ensure_ascii=False)
    assert "$ref" not in dumped, f"{tool}.{param} 还留着悬空 $ref: {dumped[:200]}"
    assert "$defs" not in dumped, f"{tool}.{param} 还留着 $defs 未展开"


@pytest.mark.parametrize("tool,param", sorted(STILL_REQUIRED), ids=[f"{t}.{p}" for t, p in sorted(STILL_REQUIRED)])
def test_required_unchanged(tools, tool, param):
    params = tools[tool].parameters or {}
    assert param in (params.get("required") or []), f"{tool}.{param} 丢了必填标记"


def test_optional_params_stay_optional(tools):
    """`marks` / `report` / `sweeping_parameters` 不该因为注入 schema 变成必填。"""
    for tool, param in (("draw_chart", "marks"),
                        ("export_simulation_report", "report"),
                        ("export_laspcem_snp", "sweeping_parameters")):
        params = tools[tool].parameters or {}
        assert param not in (params.get("required") or []), f"{tool}.{param} 不该是必填"


def test_laspcem_sweeping_parameters_is_manual_schema(tools):
    """值类型随工程变量变（数字 / "nominal"），只给 object-of-array，不细化元素类型。"""
    node = _props(tools, "export_laspcem_snp", "sweeping_parameters")
    assert node.get("type") == "object" and node.get("additionalProperties"), node
    assert node["additionalProperties"].get("type") == "array", node


def test_unknown_param_name_is_ignored(capsys):
    """表里写错参数名时不造幽灵参数（签名才是参数来源）。"""
    from servers.schema_descriptions import inject_model_param_schemas
    from servers import mcp as m

    inject_model_param_schemas(m)          # 幂等：重复注入不应新增参数
    names = set()
    from servers.utils import registered_tools
    for t in registered_tools(m):
        names |= set((t.parameters or {}).get("properties") or {})
    assert "position" in names             # 正常参数在
    assert "sweeping_parameters" in names
