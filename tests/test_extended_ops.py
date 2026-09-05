"""9 个新增 gRPC 工具（枚举 27~35）的 payload 构造与校验回归测试。

覆盖：
  workspace_ops.py   create_workspace / switch_workspace / get_current_workspace
  model_library.py   load_performance_component_from_mms / add_performance_component
  schematic_ops.py   list_ideal_components / add_ideal_component / clear_schematic / add_wire
所有测试 mock call_grpc，不访问真实 EDI。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _fake_caller():
    from proto import ecserver_pb2
    calls = []

    def _fake(task_type, payload, timeout, max_timeout_seconds=300):
        calls.append((task_type, payload))
        return {"success": True, "status": "SUCCEEDED"}

    return ecserver_pb2, calls, _fake


# ── 工作区 ──────────────────────────────────────────────────

def test_create_workspace_payload(monkeypatch):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.create_workspace("D:/EDI-Workspace-New")
    assert calls[-1][0] == ecserver_pb2.CREATE_WORKSPACE
    assert calls[-1][1] == {"path": "D:/EDI-Workspace-New"}


def test_create_workspace_rejects_empty(monkeypatch):
    from servers.eda import workspace_ops as wo
    monkeypatch.setattr(wo, "call_grpc", lambda *a, **k: {"success": True})
    r = wo.create_workspace("   ")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"


def test_switch_workspace_payload(monkeypatch):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.switch_workspace("D:/EDI-Workspace-New")
    assert calls[-1][0] == ecserver_pb2.SWITCH_WORKSPACE
    assert calls[-1][1] == {"path": "D:/EDI-Workspace-New"}


def test_get_current_workspace_payload(monkeypatch):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.get_current_workspace()
    assert calls[-1][0] == ecserver_pb2.GET_CURRENT_WORKSPACE
    assert calls[-1][1] == {}


# ── 模型库（MMS 导入 / 性能器件放置）──────────────────────────

def test_load_performance_component_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.load_performance_component_from_mms("12345678-1234-4234-8234-123456789abc")
    assert calls[-1][0] == ecserver_pb2.LOAD_PERFORMANCE_COMPONENT_FROM_MMS
    assert calls[-1][1] == {"original_uuid": "12345678-1234-4234-8234-123456789abc"}


def test_add_performance_component_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "validate_project_path", lambda p: p)
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.add_performance_component("C:/test.epp", "uuid-1", {"x": 0, "y": 0})
    assert calls[-1][0] == ecserver_pb2.ADD_PERFORMANCE_COMPONENT
    assert calls[-1][1] == {"project_path": "C:/test.epp", "component_uuid": "uuid-1",
                            "position": {"x": 0, "y": 0}}


def test_add_performance_component_rejects_bad_position(monkeypatch):
    from servers.eda import model_library as ml
    monkeypatch.setattr(ml, "validate_project_path", lambda p: p)
    monkeypatch.setattr(ml, "call_grpc", lambda *a, **k: {"success": True})
    for pos in (None, {"x": 1}, {"x": "1", "y": 2}, {"x": 1, "y": float("inf")}):
        r = ml.add_performance_component("C:/test.epp", "uuid-1", pos)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", pos


# ── 原理图扩展（内置器件 / 清空 / 连线）────────────────────────

def test_list_ideal_components_payload(monkeypatch):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.list_ideal_components()
    assert calls[-1][0] == ecserver_pb2.LIST_IDEAL_COMPONENTS
    assert calls[-1][1] == {}


def test_add_ideal_component_payload(monkeypatch):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(so, "validate_project_path", lambda p: p)
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.add_ideal_component("C:/test.epp", "R", {"x": 100, "y": 200})
    assert calls[-1][0] == ecserver_pb2.ADD_IDEAL_COMPONENT
    assert calls[-1][1] == {"project_path": "C:/test.epp", "component_type": "R",
                            "position": {"x": 100, "y": 200}}


def test_add_ideal_component_rejects_bad_position(monkeypatch):
    from servers.eda import schematic_ops as so
    monkeypatch.setattr(so, "validate_project_path", lambda p: p)
    monkeypatch.setattr(so, "call_grpc", lambda *a, **k: {"success": True})
    for pos in (None, "x=1", {"x": 1}, {"x": "1", "y": 2}, {"x": 1, "y": float("nan")}):
        r = so.add_ideal_component("C:/test.epp", "R", pos)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", pos


def test_clear_schematic_requires_confirm(monkeypatch):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(so, "validate_project_path", lambda p: p)
    monkeypatch.setattr(so, "call_grpc", _fake)
    # 未确认 → 拒绝，不调用 gRPC
    r = so.clear_schematic("C:/test.epp")
    assert r["success"] is False and r["error_code"] == "CLEAR_CONFIRMATION_REQUIRED"
    assert calls == []
    # 确认 → 调用 CLEAR_SCHEMATIC，payload 仅 project_path（confirm 不进 gRPC）
    so.clear_schematic("C:/test.epp", confirm_clear=True)
    assert calls[-1][0] == ecserver_pb2.CLEAR_SCHEMATIC
    assert calls[-1][1] == {"project_path": "C:/test.epp"}


def test_add_wire_payload(monkeypatch):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(so, "validate_project_path", lambda p: p)
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.add_wire("C:/test.epp", "R1", 0, "C1", 1)
    assert calls[-1][0] == ecserver_pb2.ADD_WIRE
    assert calls[-1][1] == {
        "project_path": "C:/test.epp",
        "first_instance_name": "R1", "first_pin_index": 0,
        "second_instance_name": "C1", "second_pin_index": 1,
    }


def test_add_wire_rejects_bad_pin_index(monkeypatch):
    from servers.eda import schematic_ops as so
    monkeypatch.setattr(so, "validate_project_path", lambda p: p)
    monkeypatch.setattr(so, "call_grpc", lambda *a, **k: {"success": True})
    for bad in (-1, 2147483648, 1.5, "0", True):
        r = so.add_wire("C:/test.epp", "R1", bad, "C1", 0)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", bad
