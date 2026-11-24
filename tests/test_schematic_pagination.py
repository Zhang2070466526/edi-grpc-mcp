# -*- coding: utf-8 -*-
"""list_schematic_components 分页（offset/limit/total/truncated）回归。

背景（2026-11-17，ENV-1）：原来只有 limit、没有 offset —— 器件超过 50 个时第 51 个以后
根本取不到（要么 limit=0 全量返回把上下文挤爆）。补 offset 后必须保证 total/truncated
口径正确，否则调用方无法判断还有没有下一页。
"""
from __future__ import annotations

import pytest


def _stub(monkeypatch, count: int):
    """让 call_project_grpc 返回 count 个假器件，不碰真实 EDI。"""
    from servers.eda import project_manage as pm

    comps = [{"instance_name": f"C{i:03d}", "component_type": "R", "active_state": 0,
              "state": "NORMAL", "parameters": {"v": i}} for i in range(count)]

    def fake_call(*a, **k):
        return {"success": True, "status": "SUCCEEDED", "message": "",
                "details": {"component_count": count, "components": [dict(c) for c in comps]}}

    monkeypatch.setattr(pm, "call_project_grpc", fake_call)
    return pm


def test_first_page(monkeypatch):
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp", limit=20, offset=0)["details"]
    assert d["total"] == 60 and d["component_count"] == 60
    assert d["offset"] == 0 and d["limit"] == 20 and d["returned"] == 20
    assert d["truncated"] is True
    assert d["components"][0]["instance_name"] == "C000"


def test_second_page(monkeypatch):
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp", limit=20, offset=20)["details"]
    assert d["offset"] == 20 and d["returned"] == 20
    assert d["components"][0]["instance_name"] == "C020"
    assert d["truncated"] is True


def test_last_partial_page(monkeypatch):
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp", limit=20, offset=55)["details"]
    assert d["returned"] == 5 and d["truncated"] is False
    assert d["components"][-1]["instance_name"] == "C059"


def test_offset_beyond_total_returns_empty(monkeypatch):
    pm = _stub(monkeypatch, 10)
    d = pm.list_schematic_components("x.epp", limit=20, offset=99)["details"]
    assert d["returned"] == 0 and d["components"] == [] and d["truncated"] is False
    assert d["total"] == 10


def test_limit_zero_returns_all(monkeypatch):
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp", limit=0)["details"]
    assert d["returned"] == 60 and d["truncated"] is False


def test_default_page_size_is_50(monkeypatch):
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp")["details"]
    assert d["limit"] == 50 and d["returned"] == 50 and d["truncated"] is True


def test_negative_offset_treated_as_zero(monkeypatch):
    pm = _stub(monkeypatch, 5)
    d = pm.list_schematic_components("x.epp", limit=2, offset=-10)["details"]
    assert d["offset"] == 0 and d["components"][0]["instance_name"] == "C000"


def test_summary_only_still_paginates(monkeypatch):
    """summary_only 只裁字段，不影响分页口径。"""
    pm = _stub(monkeypatch, 60)
    d = pm.list_schematic_components("x.epp", limit=10, offset=50, summary_only=True)["details"]
    assert d["total"] == 60 and d["returned"] == 10 and d["truncated"] is False
    assert "parameters" not in d["components"][0]


def _stub_with_geom(monkeypatch, count: int):
    """桩数据带几何（position/rotation_deg/mirrored_*/pins），模拟服务端 LIST 的同步返回。"""
    from servers.eda import project_manage as pm

    comps = [{
        "instance_name": f"R{i:03d}", "component_type": "R",
        "active_state": 0, "state": "NORMAL",
        "parameters": {"r": i},
        "position": {"x": i * 2.5, "y": -7.5},
        "rotation_deg": 90 if i % 2 else 0,
        "mirrored_horizontal": False, "mirrored_vertical": bool(i % 3),
        "pins": [{"index": 0, "local_position": {"x": 0, "y": 0},
                  "position": {"x": i * 2.5, "y": -7.5}, "uuid": f"pin-{i}"}],
    } for i in range(count)]

    def fake_call(*a, **k):
        return {"success": True, "status": "SUCCEEDED", "message": "",
                "details": {"component_count": count, "components": [dict(c) for c in comps]}}

    monkeypatch.setattr(pm, "call_project_grpc", fake_call)
    return pm


def test_geometry_survives_paging(monkeypatch):
    """几何来自同一次 gRPC（不再逐器件补），分页截断后本页每项仍须带几何。"""
    pm = _stub_with_geom(monkeypatch, 40)
    d = pm.list_schematic_components("x.epp", limit=5, offset=35)["details"]
    assert d["returned"] == 5 and d["truncated"] is False
    for c in d["components"]:
        assert isinstance(c["position"], dict) and "x" in c["position"]
        assert isinstance(c["pins"], list) and c["pins"]
        assert c["rotation_deg"] in (0, 90)


def test_summary_only_drops_position_and_pins(monkeypatch):
    """钉住现状（2026-11-24 决定不改白名单）：summary_only 裁 parameters + position/pins，
    只保留 rotation_deg / mirrored_* 这类标量；要坐标或引脚必须走 limit 分页。
    若哪天把 summary_only 改成保留几何，本用例应同步改（这是行为变更，不是修 bug）。"""
    pm = _stub_with_geom(monkeypatch, 3)
    c = pm.list_schematic_components("x.epp", limit=1, summary_only=True)["details"]["components"][0]
    assert "parameters" not in c and "position" not in c and "pins" not in c
    assert c["rotation_deg"] in (0, 90) and "mirrored_vertical" in c


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
