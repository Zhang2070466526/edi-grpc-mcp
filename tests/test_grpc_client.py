"""测试 gRPC 通信层 — parse、emit、返回结构。"""
import json
from pathlib import Path



class TestParsePayloadJson:
    def test_valid_json(self):
        from servers.eda.grpc_client import _parse_payload_json
        d, err = _parse_payload_json('{"ads_output":"log line"}')
        assert err is None
        assert d == {"ads_output": "log line"}

    def test_empty_string(self):
        from servers.eda.grpc_client import _parse_payload_json
        d, err = _parse_payload_json("")
        assert err is None
        assert d == {}

    def test_invalid_json(self):
        from servers.eda.grpc_client import _parse_payload_json
        d, err = _parse_payload_json("not json")
        assert err is not None
        assert d == {}  # 失败时返回空 dict，不泄漏原始 payload

    def test_array_not_dict(self):
        from servers.eda.grpc_client import _parse_payload_json
        d, err = _parse_payload_json("[1,2,3]")
        assert err is not None

    def test_none(self):
        from servers.eda.grpc_client import _parse_payload_json
        d, err = _parse_payload_json("null")
        assert err is not None


class TestEmitEvent:
    def test_none_callback(self):
        from servers.eda.grpc_client import _emit_event
        _emit_event(None, {})

    def test_callback_called(self):
        from servers.eda.grpc_client import _emit_event
        received = []
        _emit_event(lambda u: received.append(u), {"key": "val"})
        assert len(received) == 1
        assert received[0]["key"] == "val"

    def test_callback_exception_not_raised(self):
        from servers.eda.grpc_client import _emit_event
        def bad(_):
            raise RuntimeError("boom")
        _emit_event(bad, {})  # 不应抛异常


# ═══════════════════════════════════════════════════════════
# gRPC stub mock tests
# ═══════════════════════════════════════════════════════════

from unittest.mock import patch, MagicMock
from proto import ecserver_pb2


class TestGrpcStubBehavior:
    def test_fetch_event_fails_returns_grpc_unavailable(self):
        from servers.eda.grpc_client import call_grpc
        import grpc
        with patch("servers.eda.grpc_client.grpc.insecure_channel") as mock_ch:
            mock_ch.return_value.__enter__.return_value = MagicMock()
            with patch("servers.eda.grpc_client.ecserver_pb2_grpc.ExternalCallStub") as mock_stub:
                stub = MagicMock()
                # Use grpc.RpcError — plain Exception propagates (by design)
                error = grpc.RpcError()
                error.code = lambda: grpc.StatusCode.UNAVAILABLE
                error.details = lambda: "connection refused"
                stub.FetchEvent.side_effect = error
                mock_stub.return_value = stub
                r = call_grpc(ecserver_pb2.OPEN_PROJECT,
                              {"project_path": "C:/test.epp"}, timeout_seconds=5)
                assert r["success"] is False
                assert r["status"] == "GRPC_UNAVAILABLE"

    def test_stream_disconnected_results(self):
        from servers.eda.grpc_client import _terminal_result
        r = _terminal_result(False, "STREAM_DISCONNECTED",
                             "stream ended", "u1", "t1", "OPEN_PROJECT",
                             "C:/test.epp", "", "", False, {})
        assert r["status"] == "STREAM_DISCONNECTED"
        assert r["log_complete"] is False
        assert r["success"] is False

    def test_timeout_results(self):
        from servers.eda.grpc_client import _terminal_result
        r = _terminal_result(False, "TIMEOUT", "timeout", "u1", "t1",
                             "SIMULATE_PROJECT", "C:/test.epp", "C:/result.raw",
                             "partial log", False, {})
        assert r["status"] == "TIMEOUT"
        assert r["ads_output"] == "partial log"
        assert r["log_complete"] is False

    def test_protocol_mismatch_results(self):
        from servers.eda.grpc_client import _terminal_result
        r = _terminal_result(False, "PROTOCOL_MISMATCH",
                             "payload parse error", "u1", "t1",
                             "CREATE_SIMULATION_COMPONENT", "C:/test.epp",
                             "", "", True, {})
        assert r["status"] == "PROTOCOL_MISMATCH"
        assert r["success"] is False

    def test_hint_field_from_status(self):
        from servers.eda.grpc_client import _terminal_result
        expected = {
            "SUCCEEDED": "ok",
            "FAILED": "task_failed",
            "TIMEOUT": "outcome_unknown",
            "STREAM_DISCONNECTED": "outcome_unknown",
            "GRPC_UNAVAILABLE": "service_unavailable",
            "QUEUE_TIMEOUT": "busy",
            "REJECTED": "rejected",
            "PROTOCOL_MISMATCH": "protocol_error",
            "PAYLOAD_TOO_LARGE": "too_large",
        }
        for status, hint in expected.items():
            r = _terminal_result(status == "SUCCEEDED", status, "m", "u1", "t1", "OPEN_PROJECT")
            assert r["hint"] == hint, status

    def test_success_message_is_task_completed_not_simulation(self):
        from servers.eda.grpc_client import _terminal_result
        for ttype in ("OPEN_PROJECT", "CREATE_SIMULATION_COMPONENT",
                       "GENERATE_SCHEMATIC_FROM_NETLIST"):
            r = _terminal_result(True, "SUCCEEDED", "task completed",
                                 "u1", "t1", ttype, "", "", "", True, {})
            assert "simulation" not in r["message"].lower()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
