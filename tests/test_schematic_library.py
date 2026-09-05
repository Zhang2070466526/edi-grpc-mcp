"""原理图库 / 导出 gRPC 工具（枚举 36~40）的 payload 构造与校验回归测试。

覆盖 model_library.py 的 4 个原理图库工具（搜索公共/个人、创建/导入工程）与
design_export.py 的 export_schematic_components_to_csv。所有测试 mock gRPC 调用，
不访问真实 EDI。
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


# ── 原理图库搜索（model_library.py）──────────────────────────

def test_search_public_schematic_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.search_schematic_from_public_library("功放")
    assert calls[-1][0] == ecserver_pb2.SEARCH_SCHEMATIC_FROM_PUBLIC_LIBRARY
    assert calls[-1][1] == {"search_name": "功放"}


def test_search_public_schematic_empty(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.search_schematic_from_public_library("")
    assert calls[-1][0] == ecserver_pb2.SEARCH_SCHEMATIC_FROM_PUBLIC_LIBRARY
    assert calls[-1][1] == {"search_name": ""}


def test_search_personal_schematic_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.search_schematic_from_personal_library("低噪放")
    assert calls[-1][0] == ecserver_pb2.SEARCH_SCHEMATIC_FROM_PERSONAL_LIBRARY
    assert calls[-1][1] == {"search_name": "低噪放"}


# ── 用原理图库内容创建 / 导入（model_library.py）─────────────

def test_use_schematic_create_project_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.use_schematic_from_library_create_project("12345678-1234-4234-8234-123456789abc")
    assert calls[-1][0] == ecserver_pb2.USE_SCHEMATIC_FROM_LIBRARY_CREATE_PROJECT
    assert calls[-1][1] == {"file_uuid": "12345678-1234-4234-8234-123456789abc"}


def test_use_schematic_create_project_rejects_empty(monkeypatch):
    from servers.eda import model_library as ml
    monkeypatch.setattr(ml, "call_grpc", lambda *a, **k: {"success": True})
    r = ml.use_schematic_from_library_create_project("   ")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"


def test_use_schematic_create_project_rejects_invalid_uuid(monkeypatch):
    from servers.eda import model_library as ml
    monkeypatch.setattr(ml, "call_grpc", lambda *a, **k: {"success": True})
    r = ml.use_schematic_from_library_create_project("not-a-uuid")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"


def test_use_schematic_import_payload(monkeypatch):
    from servers.eda import model_library as ml
    ecserver_pb2, calls, _fake = _fake_caller()
    monkeypatch.setattr(ml, "validate_project_path", lambda p: p)
    monkeypatch.setattr(ml, "call_grpc", _fake)
    ml.use_schematic_from_library_import("12345678-1234-4234-8234-123456789abc", "C:/test.epp")
    assert calls[-1][0] == ecserver_pb2.USE_SCHEMATIC_FROM_LIBRARY_IMPORT
    assert calls[-1][1] == {"file_uuid": "12345678-1234-4234-8234-123456789abc", "project_path": "C:/test.epp"}


# ── 导出器件 CSV（design_export.py）──────────────────────────

def test_export_schematic_components_payload(monkeypatch):
    from servers.eda import design_export as de
    from proto import ecserver_pb2
    calls = []

    def _fake(task_type, project_path, timeout, **extras):
        calls.append((task_type, project_path, extras))
        return {"success": True, "status": "SUCCEEDED"}

    monkeypatch.setattr(de, "call_project_grpc", _fake)
    de.export_schematic_components_to_csv("C:/test.epp", "C:/out.csv")
    assert calls[-1][0] == ecserver_pb2.EXPORT_SCHEMATIC_COMPONENTS_TO_CSV
    assert calls[-1][1] == "C:/test.epp"
    assert calls[-1][2] == {"save_path": "C:/out.csv"}


def test_export_schematic_components_rejects_empty_save_path(monkeypatch):
    from servers.eda import design_export as de
    monkeypatch.setattr(de, "call_project_grpc", lambda *a, **k: {"success": True})
    r = de.export_schematic_components_to_csv("C:/test.epp", "   ")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
