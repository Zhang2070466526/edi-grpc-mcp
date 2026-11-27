"""run_mixer_simulation 参数表校验回归 —— 指标名/必填字段在 MCP 层就拦，别等 exe 报错。

覆盖：
  ① 15 指标目录顺序与使用说明一致
  ② 指标名写错 → UNKNOWN_INDICATOR + 相似名建议
  ③ 缺硬性字段（P1dB 缺 input_power_sweep_dbm 等）→ LINK_MISSING_FIELD + 点名补哪个
  ④ 合法参数表 → None（放行）
"""

from __future__ import annotations

import json

from servers.simulation_tool.mixer_simulation import (
    _INDICATOR_REQUIRES,
    _MINIMAL_PROJECT,
    _MIXER_INDICATORS,
    _check_netlist_consistency,
    _check_project,
    _failure_message,
    _fmt_errors,
    _imd_negative_warning,
    _missing_field,
    _noise_ssb_warning,
    _read_netlist_facts,
    run_mixer_simulation,
)


def _project(indicators):
    return {
        "name": "68",
        "epp": "C:/fake/p.epp",
        "links": [
            {"defaults": {"in_port": 1, "out_port": 2, "input_freq_ghz": [33, 37],
                          "input_power_dbm": -30, "lo": [{"freq_ghz": [29.1, 33.1], "power_dbm": 4}]},
             "indicators": indicators},
        ],
    }


def test_catalog_has_15_indicators_in_document_order():
    assert len(_MIXER_INDICATORS) == 15
    assert _MIXER_INDICATORS[0] == "变频增益"
    assert _MIXER_INDICATORS[4] == "输入 P1dB"
    assert _MIXER_INDICATORS[-1] == "交调抑制"
    # 使用说明「indicators」表：输入 P1dB 要 input_freq_ghz + input_power_sweep_dbm
    assert set(_INDICATOR_REQUIRES["输入 P1dB"]) == {"input_freq_ghz", "input_power_sweep_dbm"}
    assert _INDICATOR_REQUIRES["通道间隔离度"] == ("peer_link",)


def test_unknown_indicator_suggests_correct_name():
    r = _check_project(_project([{"name": "变频增"}]))
    assert r["error_code"] == "UNKNOWN_INDICATOR"
    assert r["did_you_mean"] == "变频增益"
    assert len(r["valid_indicators"]) == 15


def test_missing_hard_field_is_pinpointed():
    r = _check_project(_project([{"name": "输入 P1dB", "orders": {"FREQ": 5, "LO": 5}}]))
    assert r["error_code"] == "LINK_MISSING_FIELD"
    assert r["missing_field"] == "input_freq_ghz"  # 第一条缺失（local-only 标量）
    assert "输入 P1dB" in r["message"]

    r = _check_project(_project([{"name": "通道间隔离度"}]))
    assert r["missing_field"] == "peer_link"


def test_valid_project_passes():
    assert _check_project(_project([{"name": "变频增益"}])) is None
    assert _check_project(_project([{"name": "输入 P1dB", "input_freq_ghz": 35.0,
                                    "input_power_sweep_dbm": {"start": -40, "stop": -10, "step": 1}}])) is None
    # 受害链路 indicators 为空数组也是合法的
    assert _check_project(_project([])) is None


def _two_links(ind0, ind1, defaults0=None):
    proj = _project(ind0)
    if defaults0:
        proj["links"][0]["defaults"].update(defaults0)
    l1 = {"defaults": dict(proj["links"][0]["defaults"], in_port=3, out_port=4), "indicators": ind1}
    proj["links"].append(l1)
    return proj


def test_peer_link_must_be_local_and_not_self():
    # ① peer_link 放 defaults 不顶用（各链路受害方不同）
    r = _check_project(_two_links([{"name": "通道间隔离度"}], [],
                                 defaults0={"peer_link": {"link_index": 1}}))
    assert r["error_code"] == "LINK_MISSING_FIELD"
    assert r["missing_field"] == "peer_link"
    # ② 指向自己 → 拦
    r = _check_project(_two_links([{"name": "通道间隔离度", "peer_link": {"link_index": 0}}], []))
    assert r["error_code"] == "LINK_JSON_INVALID"
    assert r["location"].endswith("peer_link.link_index")
    # ③ 正常指向另一条 → 放行
    assert _check_project(_two_links([{"name": "通道间隔离度", "peer_link": {"link_index": 1}}], [])) is None


def test_non_dict_indicator_rejected():
    r = _check_project(_project(["变频增益"]))  # 字符串而不是 {"name": ...}
    assert r["error_code"] == "LINK_JSON_INVALID"


# ── 以下按 Mixer_Simulator使用说明.md 补的参数表校验 ──────────────────────────

def _link(**over):
    link = {"defaults": {"in_port": 1, "out_port": 2, "input_freq_ghz": [33, 37],
                         "input_power_dbm": -30, "lo": [{"freq_ghz": [29.1, 33.1], "power_dbm": 4}]},
            "indicators": [{"name": "变频增益"}]}
    link.update(over)
    return {"name": "68", "epp": "C:/fake/p.epp", "links": [link]}


def test_defaults_required_fields_and_ranges():
    # defaults 缺字段 → 点名
    r = _check_project(_link(defaults={"in_port": 1, "out_port": 2, "input_freq_ghz": [33, 37],
                                       "input_power_dbm": -30}))
    assert r["error_code"] == "LINK_MISSING_FIELD" and r["missing_field"] == "lo", r

    # 频率区间反序 → LINK_JSON_INVALID
    r = _check_project(_link(defaults={"in_port": 1, "out_port": 2, "input_freq_ghz": [37, 33],
                                       "input_power_dbm": -30,
                                       "lo": [{"freq_ghz": [29.1, 33.1], "power_dbm": 4}]}))
    assert r["error_code"] == "LINK_JSON_INVALID" and "min" in r["message"], r

    # lo 空数组 → LINK_JSON_INVALID
    r = _check_project(_link(defaults={"in_port": 1, "out_port": 2, "input_freq_ghz": [33, 37],
                                       "input_power_dbm": -30, "lo": []}))
    assert r["error_code"] == "LINK_JSON_INVALID" and "lo" in r["message"], r


def test_switch_bits_shape():
    r = _check_project(_link(switch_bits=[{"instance": "SW1", "bits": "12"}]))
    assert r["error_code"] == "LINK_JSON_INVALID" and "0/1" in r["message"], r
    r = _check_project(_link(switch_bits=[{"instance": "", "bits": "1"}]))
    assert r["error_code"] == "LINK_JSON_INVALID" and "instance" in r["message"], r
    assert _check_project(_link(switch_bits=[{"instance": "SW1", "bits": "101"}])) is None


def test_peer_link_shape_and_index_range():
    r = _check_project(_link(indicators=[{"name": "通道间隔离度", "peer_link": {"idx": 0}}]))
    assert r["error_code"] == "LINK_JSON_INVALID" and "link_index" in r["message"], r
    r = _check_project(_link(indicators=[{"name": "通道间隔离度", "peer_link": {"link_index": 3}}]))
    assert r["error_code"] == "LINK_JSON_INVALID" and "超出范围" in r["message"], r


def test_required_field_may_come_from_defaults():
    # 带内杂散 / 交调抑制 的 input_power_dbm 由 defaults 提供即可
    r = _check_project(_link(indicators=[{"name": "带内杂散"},
                                         {"name": "交调抑制", "tone_frequencies_ghz": [3.4, 3.45]}]))
    assert r is None, r
    # 交调抑制 缺 tone_frequencies_ghz（defaults 也没有）→ 点名
    r = _check_project(_link(indicators=[{"name": "交调抑制"}]))
    assert r["error_code"] == "LINK_MISSING_FIELD" and r["missing_field"] == "tone_frequencies_ghz", r


def test_precheck_netlist_and_component_before_exe(tmp_path):
    """工具应自己查 epp 同目录的 netlist.log / component.json，缺了直接点名（不启动 exe）。"""
    from servers.simulation_tool.mixer_simulation import run_mixer_simulation

    epp = tmp_path / "p.epp"
    epp.write_text("", encoding="utf-8")
    proj = {"name": "68", "epp": str(epp),
            "links": [{"defaults": {"in_port": 1, "out_port": 2, "input_freq_ghz": [33, 37],
                                    "input_power_dbm": -30,
                                    "lo": [{"freq_ghz": [29.1, 33.1], "power_dbm": 4}]},
                       "indicators": [{"name": "变频增益"}]}]}
    r = run_mixer_simulation(project=proj, out_dir=str(tmp_path / "out"))
    assert r["success"] is False and r["error_code"] == "FILE_NOT_FOUND", r
    assert "netlist.log" in r["message"], r

    # 补上网表后应卡在 component.json
    (tmp_path / "netlist.log").write_text("", encoding="utf-8")
    r = run_mixer_simulation(project=proj, out_dir=str(tmp_path / "out"))
    assert r["error_code"] == "FILE_NOT_FOUND" and "component.json" in r["message"], r


# ── 网表一致性预检：端口 / 开关实例 / LO 数量（2026-10-30 补）──────────────────
_NETLIST = """Options ResourceUsage=yes
TableMixer:MIX1 N__1 N__2 N__3 Bits="0"  Model="M"  Temp=25
LinearDevice:SW1 N__4 N__5 Bits="0"  Model="SW"  Temp=25
AmplifierDevice:AMP1 N__6 N__7 Bits="0"  Model="A"  Temp=25
Port:P1 N__1 0 Num=1 Z=50 Ohm
Port:P2 N__7 0 Num=2 Z=50 Ohm
Port:PLO N__3 0 Num=3 Z=50 Ohm
"""


def _netlist_file(tmp_path):
    f = tmp_path / "netlist.log"
    f.write_text(_NETLIST, encoding="utf-8")
    return f


def _project_with(**over):
    p = _project([{"name": "变频增益"}])
    p["links"][0]["defaults"].update(over)
    return p


def test_read_netlist_facts(tmp_path):
    facts = _read_netlist_facts(_netlist_file(tmp_path))
    assert facts["ports"] == {1, 2, 3}
    assert facts["switches"] == {"MIX1", "SW1", "AMP1"}   # 带 Bits= 的实例
    assert facts["mixers"] == 1


def test_port_not_found(tmp_path):
    r = _check_netlist_consistency(_project_with(in_port=9), _netlist_file(tmp_path))
    assert r["error_code"] == "PORT_NOT_FOUND", r
    assert "1, 2, 3" in r["hint"], r


def test_switch_not_found(tmp_path):
    p = _project_with()
    p["links"][0]["switch_bits"] = [{"instance": "NOPE", "bits": "1"}]
    r = _check_netlist_consistency(p, _netlist_file(tmp_path))
    assert r["error_code"] == "SWITCH_NOT_FOUND", r


def test_lo_count_exceeds_netlist_mixers(tmp_path):
    p = _project_with()
    p["links"][0]["defaults"]["lo"] = [{"freq_ghz": [1, 1], "power_dbm": 0}] * 2
    r = _check_netlist_consistency(p, _netlist_file(tmp_path))
    assert r["error_code"] == "LO_COUNT_MISMATCH", r


def test_valid_project_passes_netlist_check(tmp_path):
    assert _check_netlist_consistency(_project_with(), _netlist_file(tmp_path)) is None


def test_empty_links_rejected(tmp_path):
    """空 links 在入口就被拦（NO_LINKS），不用等 exe。"""
    epp = tmp_path / "p.epp"
    epp.write_text("<project/>", encoding="utf-8")
    p = {"name": "t", "epp": str(epp), "links": []}
    r = run_mixer_simulation(project=p, out_dir=str(tmp_path / "out"))
    assert r["error_code"] == "NO_LINKS", r


def test_netlist_check_collects_all_errors(tmp_path):
    """网表预检也要一次报全：端口 / 开关 / LO 数量三类错同时给。"""
    p = _project_with(in_port=9)
    p["links"][0]["switch_bits"] = [{"instance": "NOPE", "bits": "1"}]
    p["links"][0]["defaults"]["lo"] = [{"freq_ghz": [1, 1], "power_dbm": 0}] * 2
    r = _check_netlist_consistency(p, _netlist_file(tmp_path))
    assert r["error_code"] == "PORT_NOT_FOUND", r
    assert r["error_count"] >= 3, r
    codes = {e["error_code"] for e in r["all_errors"]}
    assert {"PORT_NOT_FOUND", "SWITCH_NOT_FOUND", "LO_COUNT_MISMATCH"} <= codes, codes


def test_in_port_equals_out_port_rejected():
    r = _check_project(_project_with(in_port=1, out_port=1))
    assert r["error_code"] == "LINK_JSON_INVALID", r
    assert "in_port" in r["message"], r


def test_missing_netlist_does_not_block(tmp_path):
    """网表读不了时跳过一致性检查（不因为解析失败误拦）。"""
    assert _check_netlist_consistency(_project_with(), tmp_path / "nope.log") is None


# ── 错误透传 / 结果自检（2026-10-30 补）──────────────────────────────────────
def test_fmt_errors_passes_location_through():
    msg = _fmt_errors([{"location": "links[0].defaults.input_freq_ghz", "reason": "必须是递增正频率区间"}])
    assert msg == "links[0].defaults.input_freq_ghz: 必须是递增正频率区间", msg


def test_fmt_errors_truncates_after_three():
    errs = [{"location": f"L{i}", "reason": "x"} for i in range(5)]
    assert _fmt_errors(errs).endswith("（另有 2 条）"), _fmt_errors(errs)


def test_noise_ssb_warning_detects_numeric_floor(tmp_path):
    d = tmp_path / "indicator_results"
    d.mkdir()
    (d / "08-噪声.json").write_text(
        json.dumps({"per_point": [
            {"noisefreq_ghz": 3.7, "nfssb": 437.1, "nfdsb": 5.19},
            {"noisefreq_ghz": 3.8, "nfssb": 5.30, "nfdsb": 5.20},
        ]}, ensure_ascii=False), encoding="utf-8")
    warn = _noise_ssb_warning(str(tmp_path))
    assert "SSB" in warn and "3.7" in warn, warn


def test_noise_ssb_warning_silent_when_normal(tmp_path):
    d = tmp_path / "indicator_results"
    d.mkdir()
    (d / "08-噪声.json").write_text(
        json.dumps({"per_point": [{"noisefreq_ghz": 3.8, "nfssb": 8.3, "nfdsb": 5.2}]},
                   ensure_ascii=False), encoding="utf-8")
    assert _noise_ssb_warning(str(tmp_path)) == ""


def test_imd_negative_warning():
    assert "压缩" in _imd_negative_warning([{"indicator": "交调抑制", "value": -8.25}])
    assert _imd_negative_warning([{"indicator": "交调抑制", "value": 13.4}]) == ""


def test_failure_message_priority():
    # errors[] 最可信
    assert _failure_message({}, [], [{"location": "a", "reason": "b"}], "") == "a: b"
    # 其次 failures[]
    msg = _failure_message({}, [{"id": "0:x:0", "indicator": "变频增益", "error": "boom"}], [], "")
    assert "boom" in msg
    # 空工况要给出可操作推断（exe 对这种失败不给 errors）
    assert "工况矩阵为空" in _failure_message({"condition_count": 0}, [], [], "")
    # 其它兜底
    assert "未给出" in _failure_message({"condition_count": 3}, [], [], "")


def test_missing_field_hint_carries_runnable_skeleton():
    """缺字段的 hint 要直接给可抄的最小骨架 —— 别再让调用方靠报错反推结构。

    2026-11-26：另一个会话为一张 5 字段的参数表试了 3 轮（38.5 万 tokens）才到 lo 那层。
    """
    r = _missing_field("links[0].defaults", "lo")
    assert r["error_code"] == "LINK_MISSING_FIELD" and r["missing_field"] == "lo"
    assert _MINIMAL_PROJECT in r["hint"]
    # 骨架本身必须是合法 JSON，且含 defaults 五件套
    import json as _json
    skeleton = _json.loads(_MINIMAL_PROJECT)
    assert {"in_port", "out_port", "input_freq_ghz", "input_power_dbm", "lo"} <= set(
        skeleton["links"][0]["defaults"])


def test_input_freq_equal_range_rejected_locally():
    """`input_freq_ghz:[2,2]` 要在本地就拦下（exe 只回一句"必须是递增正频率区间"）。

    本振允许 `[f,f]`（固定本振），输入频率不允许 —— 两者口径不同，2026-11-26 实测踩过。
    """
    proj = _link(defaults={"in_port": 1, "out_port": 2, "input_freq_ghz": [2, 2],
                           "input_power_dbm": -30,
                           "lo": [{"freq_ghz": [20, 20], "power_dbm": 10}]},
                 indicators=[{"name": "变频增益", "orders": {"FREQ": 5, "LO": 5}}])
    r = _check_project(proj)
    assert r and r["error_code"] == "LINK_JSON_INVALID"
    assert r["location"].endswith("defaults.input_freq_ghz")
    assert "min < max" in r["message"]


def test_orders_must_name_each_lo_on_multimixer_link(tmp_path):
    """双混频只写 {"FREQ","LO"} → 提前拦成 ORDERS_MISSING_LO（exe 会退回，2026-11-26 实测）。"""
    nl = tmp_path / "netlist.log"
    nl.write_text(
        "Mixer2:Mixer21 A B C\n"
        "TableMixer:HMC1 D E F\n"
        "Port:P1 X 0 Num=1 Z=50 Ohm\n"
        "Port:P2 Y 0 Num=2 Z=50 Ohm\n", encoding="utf-8")
    proj = _link(indicators=[{"name": "变频增益", "orders": {"FREQ": 5, "LO": 5}}])
    r = _check_netlist_consistency(proj, nl)
    assert r and r["error_code"] == "ORDERS_MISSING_LO", r
    assert "LO1" in r["message"] and "LO2" in r["message"]
    # 补齐后不再报
    ok = _link(indicators=[{"name": "变频增益", "orders": {"FREQ": 5, "LO1": 5, "LO2": 5}}])
    r2 = _check_netlist_consistency(ok, nl)
    assert r2 is None, r2


def test_numeric_floor_warning_explains_sideband_mismatch():
    """−939 dB 这种数值底 → 给"规划与混频器边带/接法不一致"的诊断，而不是只回 incomplete。"""
    from servers.simulation_tool.mixer_simulation import _numeric_floor_warning

    inds = [{"indicator": "变频增益", "status": "incomplete", "unit": "dB",
             "min": -939.0, "max": -939.0, "points": 100}]
    msg = _numeric_floor_warning(inds)
    assert "数值底" in msg and "边带" in msg and "run.json" in msg
    assert _numeric_floor_warning([{"indicator": "变频增益", "status": "ok", "value": 25.0}]) == ""
