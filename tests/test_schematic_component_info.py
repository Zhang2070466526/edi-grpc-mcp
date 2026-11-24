"""get_schematic_component_info 的入参分支回归：单名 / 批量列表 / 防御校验。

背景（2026-11-24）：instance_name 从 str 扩展到 str | list[str]。服务端接受数组，
返回 component_count + components（顺序与请求一致），任一实例不存在则整批失败。
这里只覆盖 MCP 层的构造与校验（mock call_project_grpc），不访问真实 EDI；
服务端行为见评审时的真机实测（TermG2/TermG3 批量、混不存在名字整批失败）。
"""
from __future__ import annotations

from proto import ecserver_pb2


def _capture(monkeypatch):
    """把 call_project_grpc 换成记录器，返回 (module, calls)。"""
    from servers.eda import project_manage as pm

    calls: list[tuple] = []

    def fake(task_type, project_path, timeout_seconds, *, max_timeout_seconds=300, **kwargs):
        calls.append((task_type, project_path, kwargs))
        return {"success": True, "status": "SUCCEEDED", "details": {}}

    monkeypatch.setattr(pm, "call_project_grpc", fake)
    return pm, calls


# ── 单名（原有行为，不能回归）────────────────────────────────

def test_single_name_passthrough(monkeypatch):
    pm, calls = _capture(monkeypatch)
    pm.get_schematic_component_info("C:/x.epp", "R1")
    assert calls[-1][0] == ecserver_pb2.GET_SCHEMATIC_COMPONENT_INFO
    assert calls[-1][1] == "C:/x.epp"
    assert calls[-1][2] == {"instance_name": "R1"}


def test_single_name_is_stripped(monkeypatch):
    pm, calls = _capture(monkeypatch)
    pm.get_schematic_component_info("C:/x.epp", "  R1 ")
    assert calls[-1][2] == {"instance_name": "R1"}


def test_blank_single_name_rejected(monkeypatch):
    pm, calls = _capture(monkeypatch)
    r = pm.get_schematic_component_info("C:/x.epp", "   ")
    assert r["success"] is False and r["error_code"] == "EMPTY_INSTANCE_NAME"
    assert calls == []


# ── 批量列表 ────────────────────────────────────────────────

def test_list_passthrough_keeps_order(monkeypatch):
    pm, calls = _capture(monkeypatch)
    pm.get_schematic_component_info("C:/x.epp", ["R1", "C1", "U2"])
    assert calls[-1][2] == {"instance_name": ["R1", "C1", "U2"]}


def test_list_items_are_stripped(monkeypatch):
    pm, calls = _capture(monkeypatch)
    pm.get_schematic_component_info("C:/x.epp", ["  R1  ", " C1"])
    assert calls[-1][2] == {"instance_name": ["R1", "C1"]}


def test_single_item_list_still_sends_array(monkeypatch):
    """["R1"] 走列表分支：服务端返回 components 数组，而不是单个 component。"""
    pm, calls = _capture(monkeypatch)
    pm.get_schematic_component_info("C:/x.epp", ["R1"])
    assert calls[-1][2] == {"instance_name": ["R1"]}


# ── 防御校验（都不得发 gRPC）────────────────────────────────

def test_empty_list_rejected(monkeypatch):
    pm, calls = _capture(monkeypatch)
    r = pm.get_schematic_component_info("C:/x.epp", [])
    assert r["success"] is False and r["error_code"] == "EMPTY_INSTANCE_NAME"
    assert calls == []


def test_blank_list_item_rejected(monkeypatch):
    pm, calls = _capture(monkeypatch)
    r = pm.get_schematic_component_info("C:/x.epp", ["R1", "   "])
    assert r["success"] is False and r["error_code"] == "EMPTY_INSTANCE_NAME"
    assert "空字符串" in r["message"]
    assert calls == []


def test_non_string_list_item_rejected(monkeypatch):
    pm, calls = _capture(monkeypatch)
    r = pm.get_schematic_component_info("C:/x.epp", ["R1", 123])
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
    assert calls == []


def test_wrong_type_rejected(monkeypatch):
    pm, calls = _capture(monkeypatch)
    for bad in (123, None, {"name": "R1"}):
        r = pm.get_schematic_component_info("C:/x.epp", bad)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", bad
    assert calls == []
