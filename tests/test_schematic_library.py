"""导出 gRPC 工具的 payload 构造与校验回归测试。

覆盖 design_export.py 的 export_schematic_components_to_csv。测试 mock gRPC 调用，
不访问真实 EDI。
"""


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


def test_export_schematic_components_rejects_empty_csv_path(monkeypatch):
    from servers.eda import design_export as de
    monkeypatch.setattr(de, "call_project_grpc", lambda *a, **k: {"success": True})
    r = de.export_schematic_components_to_csv("C:/test.epp", "   ")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
