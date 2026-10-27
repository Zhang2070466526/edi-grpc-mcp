"""TR 契约回归 —— 智能体不该猜参数：正确写法必须能从 schema / 报错 / resource 三处拿到。

覆盖：
  ① 指标目录 23 项、顺序与使用说明表一致，单位/实现方式成表
  ② links 元素结构直接写进 inputSchema（无 $ref/$defs、13 字段、required=name）
  ③ 指标名写错 → did_you_mean + 全量合法值（一次就能改对）
  ④ 链路表外字段 → UNKNOWN_LINK_FIELD + 相似字段建议（而不是等 exe 回错误）
  ⑤ 扫描类指标缺 devices → 调用前就拦（LINK_MISSING_DEVICES）
  ⑥ 嵌套校验错误 → 信封点名 links[0].min_freq，不是笼统的 links
  ⑦ 返回值 echo + 「请求了但没返回」的告警（防假成功）
  ⑧ resource edi://reference/tr-simulation-guide 与代码目录同源
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

import servers.registry_server  # noqa: F401
from servers import mcp
from servers.mcp_arg_guard import _build_envelope, _loc_path
from servers.resources_prompts.resources_reference import resource_tr_simulation_guide
from servers.simulation_tool import tr_simulation as tr
from servers.utils import registered_tools


def _tool():
    return [t for t in registered_tools(mcp) if t.name == "run_tr_simulation"][0]


def _guide() -> str:
    fn = getattr(resource_tr_simulation_guide, "fn", resource_tr_simulation_guide)
    return fn()


# ── ① 指标目录 ────────────────────────────────────────────────

def test_catalog_has_23_indicators_in_document_order():
    assert len(tr._TR_INDICATORS) == 23
    assert tr._TR_INDICATORS[0] == "工作频率"
    assert tr._TR_INDICATORS[8] == "输出驻波"
    assert tr._TR_INDICATORS[-1] == "Psat"
    assert tr._INDICATOR_CATALOG["延时步进"]["unit"] == "s"
    assert tr._INDICATOR_CATALOG["杂散抑制"]["unit"] == "dBc"
    assert tr._INDICATOR_CATALOG["输入驻波"]["unit"] == "无量纲"


# ── ② schema 自包含 ───────────────────────────────────────────

def test_link_schema_is_self_contained():
    """links 元素结构必须直接出现在 inputSchema（不是 $ref/$defs，也不是空 items）。"""
    params = _tool().parameters
    dumped = json.dumps(params, ensure_ascii=False)
    assert "$ref" not in dumped and "$defs" not in dumped
    items = params["properties"]["links"]["items"]
    assert list(items["properties"]) == list(tr._LINK_FIELDS)
    assert items["required"] == ["name"]


def test_link_schema_descriptions_match_authoritative_table():
    items = _tool().parameters["properties"]["links"]["items"]
    for field, meta in tr._LINK_FIELDS.items():
        assert meta["doc"] in items["properties"][field]["description"]


def test_describe_tool_reports_link_fields():
    from servers.describe_tool import describe_tool

    r = describe_tool("run_tr_simulation")
    row = {p["name"]: p for p in r["parameters"]}["links"]
    assert set(row["items_fields"]) == set(tr._LINK_FIELDS)
    assert row["items_required"] == ["name"]
    assert "items_hint" not in row  # 结构已声明，不该再提示「未声明」


# ── ③④⑤ 报错可自愈 ───────────────────────────────────────────

def test_unknown_indicator_suggests_correct_name():
    r = tr._check_indicators("输入P1dB")
    assert r["error_code"] == "UNKNOWN_INDICATOR"
    assert r["did_you_mean"] == "IP1dB"
    assert len(r["valid_indicators"]) == 23
    r = tr._check_indicators("增益")
    assert r["did_you_mean"] == "输出增益"


def test_unknown_link_field_is_rejected_with_suggestion():
    r = tr._check_links([{"name": "接收链路", "phase_devices_json": ["P1"]}], [])
    assert r["error_code"] == "UNKNOWN_LINK_FIELD"
    assert r["unknown_fields"] == {"phase_devices_json": "phase_devices"}
    assert len(r["valid_link_fields"]) == 13


def test_phase_indicator_requires_devices():
    r = tr._check_links([{"name": "接收链路"}], ["移相误差RMS"])
    assert r["error_code"] == "LINK_MISSING_DEVICES"
    assert r["missing_field"] == "phase_devices"


def test_atten_indicator_requires_devices():
    r = tr._check_links([{"name": "接收链路", "phase_devices": ["P1"]}], ["衰减误差RMS"])
    assert r["error_code"] == "LINK_MISSING_DEVICES"
    assert r["missing_field"] == "atten_devices"


def test_valid_link_and_indicators_pass():
    assert tr._check_indicators("输出增益,IP1dB,移相误差RMS") is None
    assert tr._check_links(
        [{"name": "接收链路", "min_freq": 29, "max_freq": 31,
          "phase_devices": ["P1"], "atten_devices": ["A1"]}],
        ["输出增益", "移相误差RMS", "衰减误差RMS"],
    ) is None


def test_optional_freq_fields_accept_explicit_null():
    """freq_rx/freq_pout 可选（默认 None），显式传 null 应与「不传」等价，不能误报「必须是数字」。"""
    link = tr.TrLink.model_validate({"name": "L", "freq_rx": None, "freq_pout": None})
    assert link.freq_rx is None and link.freq_pout is None
    # 非可选数值字段传 null 仍要拦（有默认值，显式 null 不该悄悄变成 0）
    with pytest.raises(ValidationError):
        tr.TrLink.model_validate({"name": "L", "min_freq": None})


# ── ⑥ 嵌套错误路径 ────────────────────────────────────────────

def test_nested_validation_error_is_pinpointed():
    """links[0].min_freq 传字符串 → 信封必须点到具体字段，而不是只说 links。"""
    model = _tool().fn_metadata.arg_model
    with pytest.raises(ValidationError) as ei:
        model.model_validate({"epp_path": "x.epp", "links": [{"name": "L", "min_freq": "29"}]})
    env = _build_envelope("run_tr_simulation", {}, model, ei.value)
    assert env["error_code"] == "INVALID_PARAMETERS"
    assert env["invalid"][0]["field"] == "links[0].min_freq"
    assert "JSON 数字" in env["invalid"][0]["reason"]


def test_loc_path_renders_index():
    assert _loc_path(("links", 0, "phase_devices", 1)) == "links[0].phase_devices[1]"


# ── ⑦ echo / 假成功告警 ───────────────────────────────────────

def _stub_pipeline(tmp_path, monkeypatch, results):
    epp = tmp_path / "x.epp"
    epp.write_text("dummy")
    (tmp_path / "turbocharts_app.exe").write_bytes(b"stub")
    monkeypatch.setattr(tr, "tcp_port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(tr, "run_exe", lambda *a, **k: {
        "returncode": 0, "stdout": "", "stderr": "", "json_error": "",
        "json": {"simulation_path": str(tmp_path), "results": results, "errors": []}})
    return str(epp)


def test_echo_and_missing_indicator_warning(tmp_path, monkeypatch):
    epp = _stub_pipeline(tmp_path, monkeypatch, [{
        "link": "接收链路",
        "electrical_parameters": [
            {"indicator": "工作频率", "minimum": "29", "typical": "", "maximum": "31", "unit": "GHz"},
            {"indicator": "输出增益", "minimum": "20", "typical": "", "maximum": "21", "unit": "dB"},
        ]}])
    r = tr.run_tr_simulation(str(epp), [{"name": "接收链路", "min_freq": 29, "max_freq": 31}],
                             indicators="输出增益,噪声系数",
                             edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["success"] is True
    assert r["echo"]["contract"].startswith("TR-")
    assert r["echo"]["indicators"] == ["输出增益", "噪声系数"]
    assert any("噪声系数" in w for w in r["warnings"])


def test_curve_only_indicators_do_not_warn(tmp_path, monkeypatch):
    """移相态/衰减态按 exe 文档只出曲线 CSV，不进结果 JSON —— 不该报「缺失」。"""
    epp = _stub_pipeline(tmp_path, monkeypatch, [{
        "link": "接收链路",
        "electrical_parameters": [
            {"indicator": "工作频率", "minimum": "29", "typical": "", "maximum": "31", "unit": "GHz"}]}])
    r = tr.run_tr_simulation(
        str(epp),
        [{"name": "接收链路", "phase_devices": ["P1"]}],
        indicators="移相态",
        edi_path=str(tmp_path), ads_path=str(tmp_path))
    assert r["success"] is True
    assert not r.get("warnings")


# ── ⑧ resource 同源 ──────────────────────────────────────────

def test_resource_guide_lists_all_indicators_and_fields():
    md = _guide()
    assert "TR-1.0" in md
    for name in tr._TR_INDICATORS:
        assert f"| {name} |" in md, f"resource 缺少指标 {name}"
    for field in tr._LINK_FIELDS:
        assert f"| {field} |" in md, f"resource 缺少链路字段 {field}"
    assert tr.TR_CONTRACT_VERSION.split(" ")[0] in md
