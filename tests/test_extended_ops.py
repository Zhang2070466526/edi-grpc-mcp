"""9 个新增 gRPC 工具（枚举 27~35）的 payload 构造与校验回归测试。

覆盖：
  workspace_ops.py   create_workspace / switch_workspace / get_current_workspace
  model_library.py   add_performance_component
  schematic_ops.py   list_ideal_components / add_ideal_component / clear_schematic / add_wire
所有测试 mock call_grpc，不访问真实 EDI。
"""
from pathlib import Path





# ── 工作区 ──────────────────────────────────────────────────

def test_create_workspace_payload(monkeypatch, fake_caller):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.create_workspace("D:/EDI-Workspace-New")
    assert calls[-1][0] == ecserver_pb2.CREATE_WORKSPACE
    assert calls[-1][1] == {"path": "D:/EDI-Workspace-New"}


def test_create_workspace_rejects_empty(monkeypatch, fake_caller):
    from servers.eda import workspace_ops as wo
    monkeypatch.setattr(wo, "call_grpc", lambda *a, **k: {"success": True})
    r = wo.create_workspace("   ")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"


def test_switch_workspace_payload(monkeypatch, fake_caller, tmp_path):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.switch_workspace(str(tmp_path))
    assert calls[-1][0] == ecserver_pb2.SWITCH_WORKSPACE
    assert calls[-1][1] == {"path": str(tmp_path)}


def test_get_current_workspace_payload(monkeypatch, fake_caller):
    from servers.eda import workspace_ops as wo
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(wo, "call_grpc", _fake)
    wo.get_current_workspace()
    assert calls[-1][0] == ecserver_pb2.GET_CURRENT_WORKSPACE
    assert calls[-1][1] == {}


# ── 模型库（性能器件放置）──────────────────────────

def test_add_performance_component_payload(monkeypatch, fake_caller):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(ml, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.add_performance_component("C:/test.epp", "12345678-1234-4234-8234-123456789abc", {"x": 0, "y": 0})
    assert calls[-1][0] == ecserver_pb2.ADD_PERFORMANCE_COMPONENT
    assert calls[-1][1] == {"project_path": "C:/test.epp", "component_uuid": "12345678-1234-4234-8234-123456789abc",
                            "position": {"x": 0, "y": 0}}


def test_add_performance_component_rejects_bad_position(monkeypatch, fake_caller):
    from servers.eda import model_library as ml
    monkeypatch.setattr(ml, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(ml, "call_grpc", lambda *a, **k: {"success": True})
    for pos in (None, {"x": 1}, {"x": "1", "y": 2}, {"x": 1, "y": float("inf")}):
        r = ml.add_performance_component("C:/test.epp", "12345678-1234-4234-8234-123456789abc", pos)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", pos


# ── 原理图扩展（内置器件 / 清空 / 连线）────────────────────────

def test_list_ideal_components_payload(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.list_ideal_components()
    assert calls[-1][0] == ecserver_pb2.LIST_IDEAL_COMPONENTS
    assert calls[-1][1] == {}


def test_add_ideal_component_payload(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.add_ideal_component("C:/test.epp", "R", {"x": 100, "y": 200})
    assert calls[-1][0] == ecserver_pb2.ADD_IDEAL_COMPONENT
    assert calls[-1][1] == {"project_path": "C:/test.epp", "component_type": "R",
                            "position": {"x": 100, "y": 200}}


def test_add_ideal_component_rejects_bad_position(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", lambda *a, **k: {"success": True})
    for pos in (None, "x=1", {"x": 1}, {"x": "1", "y": 2}, {"x": 1, "y": float("nan")}):
        r = so.add_ideal_component("C:/test.epp", "R", pos)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", pos


def test_clear_schematic_requires_confirm(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", _fake)
    # 未确认 → 拒绝，不调用 gRPC
    r = so.clear_schematic("C:/test.epp")
    assert r["success"] is False and r["error_code"] == "CLEAR_CONFIRMATION_REQUIRED"
    assert calls == []
    # 确认 → 调用 CLEAR_SCHEMATIC，payload 仅 project_path（confirm 不进 gRPC）
    so.clear_schematic("C:/test.epp", confirm_clear=True)
    assert calls[-1][0] == ecserver_pb2.CLEAR_SCHEMATIC
    assert calls[-1][1] == {"project_path": "C:/test.epp"}


def test_add_wire_payload(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
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
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", lambda *a, **k: {"success": True})
    for bad in (-1, 2147483648, 1.5, "0", True):
        r = so.add_wire("C:/test.epp", "R1", bad, "C1", 0)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", bad


def test_attach_out_component_rejects_bool_pin_index(monkeypatch):
    from servers.eda import simulation_components as sc
    monkeypatch.setattr(sc, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(sc, "call_project_grpc", lambda *a, **k: {"success": True})
    r = sc.attach_out_component("C:/test.epp", "U1", pin_index=True)
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"


# ── 批量器件变换（枚举 64）──────────────────────────

def test_update_transform_payload(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.update_schematic_components_transform("C:/test.epp", [
        {"instance_name": "R1", "position": {"x": 100, "y": 200}, "rotation_deg": 90,
         "mirrored_horizontal": True},
        {"uuid": "11111111-1111-4111-8111-111111111111", "position": {"x": 300, "y": 200}},
    ])
    assert calls[-1][0] == ecserver_pb2.UPDATE_SCHEMATIC_COMPONENTS_TRANSFORM
    # payload 保持最小：不带顶层 sub_type / filters（2026-11-13 实测二者均非必填，
    # 曾经缺 sub_type 报错是 EDI 那次实例的问题，重启后消失）
    assert calls[-1][1] == {
        "project_path": "C:/test.epp",
        "components": [
            {"instance_name": "R1", "position": {"x": 100, "y": 200}, "rotation_deg": 90,
             "mirrored_horizontal": True},
            {"uuid": "11111111-1111-4111-8111-111111111111", "position": {"x": 300, "y": 200}},
        ],
    }


def test_update_transform_rejects_bad_input(monkeypatch):
    from servers.eda import schematic_ops as so
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", lambda *a, **k: {"success": True})
    bad_cases = [
        [],                                                  # 空数组
        [{"position": {"x": 1, "y": 2}}],                    # 缺 uuid/instance_name
        [{"instance_name": "R1"}],                           # 缺变换字段
        [{"uuid": "not-a-uuid", "position": {"x": 1, "y": 2}}],  # uuid 非法
        [{"instance_name": "R1", "rotation_deg": "90"}],     # rotation 非数值
        [{"instance_name": "R1", "mirrored_horizontal": "true"}],  # 镜像非布尔
        [{"instance_name": "R1", "position": {"x": 1}}],     # position 缺 y
        [{"instance_name": "R1", "position": {"x": 2e9, "y": 0}}],  # position 越界（>1e9）
    ]
    for components in bad_cases:
        r = so.update_schematic_components_transform("C:/test.epp", components)
        assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS", components


def test_update_transform_null_identifier_falls_back(monkeypatch, fake_caller):
    from servers.eda import schematic_ops as so
    ecserver_pb2, calls, _fake = fake_caller
    monkeypatch.setattr(so, "require_project_path", lambda p: (p, None))
    monkeypatch.setattr(so, "call_grpc", _fake)
    so.update_schematic_components_transform("C:/test.epp", [
        {"uuid": None, "instance_name": "R1", "position": {"x": 100, "y": 200}},
        {"uuid": "11111111-1111-4111-8111-111111111111", "instance_name": None,
         "rotation_deg": 90},
    ])
    comps = calls[-1][1]["components"]
    assert comps[0] == {"instance_name": "R1", "position": {"x": 100, "y": 200}}
    assert comps[1] == {"uuid": "11111111-1111-4111-8111-111111111111", "rotation_deg": 90}
