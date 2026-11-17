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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
