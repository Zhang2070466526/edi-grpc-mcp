"""`convert_ep_numeric` 的边界回归（ENV-2 修复的锁）。

exe 透传的 `electrical_parameters.minimum/typical/maximum` 是字符串
（实测 `"typical": "26.445…"`），agent 拿去做数值比较会 TypeError。
包装层转 float，但必须保留：空串 / None / 非数值串（如 `"±0.6"`）/ 非 dict 形状。
"""

from __future__ import annotations

import pytest

from servers.simulation_tool.config import convert_ep_numeric


def _one(ep: dict) -> list:
    return [{"link": "L1", "electrical_parameters": [ep]}]


def test_numeric_strings_become_floats():
    out = convert_ep_numeric(_one({"indicator": "增益", "minimum": "25.18",
                                    "typical": "26.445061785036003", "maximum": "-1e-3"}))
    ep = out[0]["electrical_parameters"][0]
    assert ep["minimum"] == pytest.approx(25.18)
    assert ep["typical"] == pytest.approx(26.445061785036003)
    assert ep["maximum"] == pytest.approx(-1e-3)
    assert all(isinstance(ep[k], float) for k in ("minimum", "typical", "maximum"))


def test_blank_and_none_are_preserved():
    out = convert_ep_numeric(_one({"minimum": "", "typical": None, "maximum": "  "}))
    ep = out[0]["electrical_parameters"][0]
    assert ep["minimum"] == ""
    assert ep["typical"] is None
    assert ep["maximum"] == "  "


def test_non_numeric_string_is_preserved():
    out = convert_ep_numeric(_one({"indicator": "平坦度", "minimum": "±0.6",
                                    "typical": "±0.6", "maximum": "±0.6"}))
    ep = out[0]["electrical_parameters"][0]
    assert ep["minimum"] == "±0.6" and ep["typical"] == "±0.6"


def test_passthrough_for_odd_shapes():
    """非 dict、缺 key、electrical_parameters 不是 list —— 一律原样透传。"""
    payload = [
        {"link": "L1"},
        "not-a-dict",
        {"electrical_parameters": "bad"},
        {"electrical_parameters": [None, "x"]},
    ]
    out = convert_ep_numeric(payload)
    assert out[0] == {"link": "L1"}
    assert out[1] == "not-a-dict"
    assert out[2] == {"electrical_parameters": "bad"}
    assert out[3]["electrical_parameters"] == [None, "x"]


def test_does_not_mutate_input():
    src = _one({"typical": "1.5"})
    snapshot = {"typical": "1.5"}
    convert_ep_numeric(src)
    assert src[0]["electrical_parameters"][0] == snapshot, "必须是拷贝，不能改入参"


def test_empty_list_ok():
    assert convert_ep_numeric([]) == []
