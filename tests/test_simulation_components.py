"""Tests for simulation_components.py — protocol v2.

Covers: catalog, parameter resolution, validation, wire conversion,
        component lookup, input edge cases.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Utilities under test
# ---------------------------------------------------------------------------
from servers.eda.simulation_components import (
    _load_catalog,
    _catalog_component,
    _resolve_parameter_schema,
    _prepare_parameters,
    _to_wire_parameters,
    _from_wire_parameters,
    _find_component_by_instance,
    _format_component_parameters,
    _find_sim_components,
    _COMPONENT_TYPES,
    _ACTIVE_STATES,
)


# ═══════════════════════════════════════════════════════════
# 1. Catalog structure
# ═══════════════════════════════════════════════════════════

class TestCatalog:
    def test_catalog_loads(self):
        cat = _load_catalog()
        assert cat, "Catalog should not be empty"
        assert cat.get("schema_version") == "2.0.0"
        assert cat.get("protocol_version") == "2"

    def test_all_types_present(self):
        for ct in _COMPONENT_TYPES:
            comp = _catalog_component(ct)
            assert comp, f"{ct} should be in catalog"
            assert "parameters" in comp

    def test_all_parameters_have_explicit_permissions(self):
        for ct in _COMPONENT_TYPES:
            comp = _catalog_component(ct)
            for pname, pschema in comp.get("parameters", {}).items():
                assert "create_allowed" in pschema, f"{ct}.{pname} missing create_allowed"
                assert "update_allowed" in pschema, f"{ct}.{pname} missing update_allowed"
                assert "permission_source" in pschema, f"{ct}.{pname} missing permission_source"

    def test_wire_names_unique(self):
        for ct in _COMPONENT_TYPES:
            comp = _catalog_component(ct)
            wires = [d.get("wire_name") for d in comp.get("parameters", {}).values()]
            assert len(wires) == len(set(wires)), f"{ct}: duplicate wire names in {wires}"

    def test_bandwidth_for_noise_fully_readonly(self):
        sp = _catalog_component("SParameter")
        bn = sp["parameters"]["BandwidthForNoise"]
        assert bn["create_allowed"] is False, "BandwidthForNoise should NOT be create_allowed"
        assert bn["update_allowed"] is False, "BandwidthForNoise should NOT be update_allowed"

    def test_examples_pass_own_validation(self):
        cat = _load_catalog()
        for ct in _COMPONENT_TYPES:
            example = cat["components"][ct]["example"]
            wire, error = _prepare_parameters(ct, example, "create", allow_empty=True)
            assert error is None, f"{ct} example fails validation: {error}"
            assert isinstance(wire, dict)


# ═══════════════════════════════════════════════════════════
# 2. Parameter resolution
# ═══════════════════════════════════════════════════════════

class TestResolveParameterSchema:
    def test_fixed_param(self):
        schema, wire = _resolve_parameter_schema("SParameter", "Start")
        assert schema is not None
        assert wire == "Start"
        assert schema["value_type"] == "number"

    def test_fixed_param_with_wire_alias(self):
        schema, wire = _resolve_parameter_schema("HarmonicBalance", "Freq")
        assert schema is not None
        assert wire == "Freq[1]", f"Freq shortcut should map to Freq[1], got {wire}"

        schema, wire = _resolve_parameter_schema("HarmonicBalance", "Order")
        assert schema is not None
        assert wire == "Order[1]"

    def test_dynamic_param(self):
        schema, wire = _resolve_parameter_schema("HarmonicBalance", "Freq[2]")
        assert schema is not None
        assert wire == "Freq[2]"

    def test_dynamic_param_out_of_range(self):
        schema, wire = _resolve_parameter_schema("HarmonicBalance", "Freq[99]")
        assert schema is None, "Freq[99] exceeds index_max 32"

    def test_dynamic_param_xdb(self):
        schema, wire = _resolve_parameter_schema("XDB", "Order[3]")
        assert schema is not None
        assert wire == "Order[3]"

    def test_unknown_param(self):
        schema, wire = _resolve_parameter_schema("SParameter", "FooBar")
        assert schema is None
        assert wire == ""

    def test_unknown_component_type(self):
        schema, wire = _resolve_parameter_schema("UnknownType", "Start")
        assert schema is None
        assert wire == ""


# ═══════════════════════════════════════════════════════════
# 3. _prepare_parameters — unified validation
# ═══════════════════════════════════════════════════════════

class TestPrepareParameters:
    def test_create_empty_allowed(self):
        wire, err = _prepare_parameters("SParameter", {}, "create", allow_empty=True)
        assert err is None
        assert wire == {}

    def test_update_empty_rejected(self):
        wire, err = _prepare_parameters("SParameter", {}, "update", allow_empty=False)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETERS"

    def test_not_a_dict(self):
        wire, err = _prepare_parameters("SParameter", [1, 2, 3], "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETERS"

    def test_null_value_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": None}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETER_VALUE"

    def test_array_value_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": [1, 2]}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETER_VALUE"

    def test_extra_field_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "1", "unit": "GHz", "foo": "bar"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETER_VALUE"

    def test_missing_value_field(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"unit": "GHz"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "MISSING_VALUE"

    def test_number_validation(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "1.0", "unit": "GHz"}}, "create", allow_empty=True)
        assert err is None
        assert wire["Start"]["value"] == "1.0"

    def test_nan_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "NaN", "unit": "GHz"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_VALUE"

    def test_infinity_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "Infinity", "unit": "GHz"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_VALUE"

    def test_integer_fraction_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"Pts": {"value": "1.5"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_VALUE"

    def test_integer_valid(self):
        wire, err = _prepare_parameters("SParameter",
            {"Pts": {"value": "101"}}, "create", allow_empty=True)
        assert err is None
        assert wire["Pts"]["value"] == "101"

    def test_enum_valid(self):
        wire, err = _prepare_parameters("SParameter",
            {"CalcS": {"value": "yes"}}, "create", allow_empty=True)
        assert err is None

    def test_enum_invalid(self):
        wire, err = _prepare_parameters("SParameter",
            {"CalcS": {"value": "maybe"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "INVALID_ENUM_VALUE"

    def test_missing_required_unit(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "1.0"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "MISSING_UNIT"

    def test_unwanted_unit(self):
        wire, err = _prepare_parameters("SParameter",
            {"Pts": {"value": "101", "unit": "Hz"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "UNSUPPORTED_UNIT"

    def test_wrong_unit(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "1.0", "unit": "kilometers"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "UNSUPPORTED_UNIT"

    def test_empty_unit_string(self):
        wire, err = _prepare_parameters("SParameter",
            {"Start": {"value": "1.0", "unit": ""}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "UNSUPPORTED_UNIT"

    def test_unsupported_param(self):
        wire, err = _prepare_parameters("SParameter",
            {"NoSuchParam": {"value": "1"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "UNSUPPORTED_PARAMETER"

    def test_bandwidth_for_noise_create_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"BandwidthForNoise": {"value": "1.0", "unit": "GHz"}}, "create", allow_empty=True)
        assert err is not None
        assert err["error_code"] == "CREATE_PARAMETER_NOT_ALLOWED"

    def test_bandwidth_for_noise_update_rejected(self):
        wire, err = _prepare_parameters("SParameter",
            {"BandwidthForNoise": {"value": "1.0", "unit": "GHz"}}, "update", allow_empty=False)
        assert err is not None
        assert err["error_code"] == "UPDATE_PARAMETER_NOT_ALLOWED"

    def test_hb_freq_wire_conversion(self):
        wire, err = _prepare_parameters("HarmonicBalance",
            {"Freq": {"value": "1", "unit": "GHz"}}, "create", allow_empty=True)
        assert err is None
        assert "Freq[1]" in wire, f"Freq should be converted to Freq[1], got {wire}"

    def test_hb_multi_tone(self):
        wire, err = _prepare_parameters("HarmonicBalance", {
            "Freq[1]": {"value": "1", "unit": "GHz"},
            "Order[1]": {"value": "5"},
            "Freq[2]": {"value": "2", "unit": "GHz"},
            "Order[2]": {"value": "3"},
        }, "create", allow_empty=True)
        assert err is None
        assert "Freq[2]" in wire
        assert "Order[2]" in wire

    def test_xdb_wire_conversion(self):
        wire, err = _prepare_parameters("XDB",
            {"Freq": {"value": "1.0", "unit": "GHz"}, "Order": {"value": "5"}},
            "create", allow_empty=True)
        assert err is None
        assert wire["Freq[1]"]["value"] == "1.0"
        assert wire["Order[1]"]["value"] == "5"


# ═══════════════════════════════════════════════════════════
# 4. Wire conversion
# ═══════════════════════════════════════════════════════════

class TestWireConversion:
    def test_to_wire_hb(self):
        wire = _to_wire_parameters("HarmonicBalance", {
            "Freq": {"value": "1", "unit": "GHz"},
            "Order": {"value": "5"},
        })
        assert "Freq[1]" in wire
        assert "Order[1]" in wire

    def test_to_wire_dynamic(self):
        wire = _to_wire_parameters("HarmonicBalance", {
            "Freq[3]": {"value": "3", "unit": "GHz"},
        })
        assert "Freq[3]" in wire

    def test_from_wire_fixed(self):
        public = _from_wire_parameters("HarmonicBalance", {
            "Freq[1]": {"value": "1", "unit": "GHz"},
            "Order[1]": {"value": "5"},
        })
        assert "Freq" in public
        assert "Order" in public

    def test_from_wire_dynamic(self):
        public = _from_wire_parameters("HarmonicBalance", {
            "Freq[2]": {"value": "2", "unit": "GHz"},
            "Order[2]": {"value": "3"},
        })
        assert "Freq[2]" in public
        assert "Order[2]" in public

    def test_from_wire_mixed(self):
        public = _from_wire_parameters("HarmonicBalance", {
            "Freq[1]": {"value": "1", "unit": "GHz"},
            "Order[1]": {"value": "5"},
            "Freq[2]": {"value": "2", "unit": "GHz"},
            "Order[2]": {"value": "3"},
        })
        assert "Freq" in public          # Freq[1] → Freq
        assert "Freq[2]" in public        # Freq[2] → Freq[2]
        assert "Order" in public          # Order[1] → Order
        assert "Order[2]" in public       # Order[2] → Order[2]


# ═══════════════════════════════════════════════════════════
# 5. Component lookup
# ═══════════════════════════════════════════════════════════

class TestFindComponentByInstance:
    def test_empty_instance_name(self):
        comp, error = _find_component_by_instance(
            "/fake/project.epp", ""
        )
        assert comp is None
        assert error["error_code"] == "EMPTY_INSTANCE_NAME"

    def test_whitespace_only(self):
        comp, error = _find_component_by_instance(
            "/fake/project.epp", "   "
        )
        assert comp is None
        assert error["error_code"] == "EMPTY_INSTANCE_NAME"

    def test_file_not_found(self):
        comp, error = _find_component_by_instance(
            "C:/nonexistent/project.epp", "R1"
        )
        assert comp is None
        assert error["error_code"] in ("COMPONENT_NOT_FOUND", "PROJECT_NOT_FOUND")


# ═══════════════════════════════════════════════════════════
# 6. Parameter formatting
# ═══════════════════════════════════════════════════════════

class TestFormatComponentParameters:
    def test_no_unit_on_unitless_param(self):
        result = _format_component_parameters("SParameter", {
            "Pts": {"value": "101"},
        })
        assert "Pts" in result
        assert "unit" not in result["Pts"], "Unitless param should not have unit key"

    def test_unit_on_unitful_param(self):
        result = _format_component_parameters("SParameter", {
            "Start": {"value": "1.0", "unit": "GHz"},
        })
        assert result["Start"]["unit"] == "GHz"

    def test_empty_result(self):
        result = _format_component_parameters("SParameter", {})
        assert result == {}

    def test_wire_to_public(self):
        result = _format_component_parameters("HarmonicBalance", {
            "Freq[1]": {"value": "1", "unit": "GHz"},
        })
        assert "Freq" in result
        assert result["Freq"]["value"] == "1"

    def test_dynamic_wire_to_public(self):
        result = _format_component_parameters("HarmonicBalance", {
            "Freq[2]": {"value": "2", "unit": "GHz"},
        })
        assert "Freq[2]" in result

    def test_full_metadata_preserved(self):
        result = _format_component_parameters("SParameter", {
            "Start": {"value": "1.0", "unit": "GHz", "default_unit": "GHz",
                      "tunable": False, "visible": True},
        })
        assert result["Start"]["default_unit"] == "GHz"
        assert result["Start"]["tunable"] is False
        assert result["Start"]["visible"] is True
        assert result["Start"]["initial"] == ""


# ═══════════════════════════════════════════════════════════
# 7. Active states
# ═══════════════════════════════════════════════════════════

class TestActiveStates:
    def test_valid_states(self):
        assert "NORMAL" in _ACTIVE_STATES
        assert "DISABLED" in _ACTIVE_STATES
        assert "SHORTED" in _ACTIVE_STATES
        assert len(_ACTIVE_STATES) == 3


# ═══════════════════════════════════════════════════════════
# 8. Schema tool
# ═══════════════════════════════════════════════════════════

class TestGetSchema:
    def test_sparameter_schema(self):
        from servers.eda.simulation_components import get_simulation_component_schema
        result = get_simulation_component_schema("SParameter")
        assert result["success"] is True
        assert result["component_type"] == "SParameter"
        assert result["schema_version"] == "2.0.0"
        assert result["protocol_version"] == "2"
        assert "parameters" in result
        assert "parameter_patterns" in result
        assert "example" in result

    def test_hb_schema_with_patterns(self):
        from servers.eda.simulation_components import get_simulation_component_schema
        result = get_simulation_component_schema("HarmonicBalance")
        assert result["success"] is True
        assert len(result["parameter_patterns"]) == 2

    def test_single_parameter(self):
        from servers.eda.simulation_components import get_simulation_component_schema
        result = get_simulation_component_schema("SParameter", "Start")
        assert result["success"] is True
        assert "Start" in result["parameters"]
        assert len(result["parameters"]) == 1

    def test_unknown_parameter(self):
        from servers.eda.simulation_components import get_simulation_component_schema
        result = get_simulation_component_schema("SParameter", "NoSuchParam")
        assert result["success"] is False
        assert result["error_code"] == "UNSUPPORTED_PARAMETER"

    def test_unknown_type(self):
        from servers.eda.simulation_components import get_simulation_component_schema
        result = get_simulation_component_schema("FooBar")
        assert result["success"] is False
        assert result["error_code"] == "UNSUPPORTED_COMPONENT_TYPE"


# ═══════════════════════════════════════════════════════════
# 9. 参数别名冲突检测
# ═══════════════════════════════════════════════════════════

class TestDuplicateParameterAlias:
    def test_freq_and_freq1_conflict(self):
        wire, err = _prepare_parameters("HarmonicBalance", {
            "Freq": {"value": "1", "unit": "GHz"},
            "Freq[1]": {"value": "2", "unit": "GHz"},
        }, "create", allow_empty=True)
        assert err is not None, "Freq + Freq[1] should be rejected"
        assert err["error_code"] == "DUPLICATE_PARAMETER_ALIAS"

    def test_order_and_order1_conflict(self):
        wire, err = _prepare_parameters("HarmonicBalance", {
            "Order": {"value": "5"},
            "Order[1]": {"value": "3"},
        }, "create", allow_empty=True)
        assert err is not None, "Order + Order[1] should be rejected"
        assert err["error_code"] == "DUPLICATE_PARAMETER_ALIAS"

    def test_freq_and_order_no_conflict(self):
        """Freq and Order are different wire params — no conflict."""
        wire, err = _prepare_parameters("HarmonicBalance", {
            "Freq": {"value": "1", "unit": "GHz"},
            "Order": {"value": "5"},
        }, "create", allow_empty=True)
        assert err is None

    def test_freq2_and_freq2_no_conflict(self):
        """Same dynamic param twice is impossible in a Python dict (keys unique).
        The real alias risk is Freq (→Freq[1]) + Freq[1] (→Freq[1]), covered above."""
        wire, err = _prepare_parameters("HarmonicBalance", {
            "Freq[2]": {"value": "2", "unit": "GHz"},
        }, "create", allow_empty=True)
        assert err is None, f"Single Freq[2] should be valid, got {err}"
        assert wire["Freq[2]"]["value"] == "2"


# ═══════════════════════════════════════════════════════════
# 10. Falsy parameters 检测
# ═══════════════════════════════════════════════════════════

class TestFalsyParametersRejection:
    def test_empty_list_parameters(self):
        """parameters=[] should fail type check."""
        wire, err = _prepare_parameters("SParameter", [], "create", allow_empty=False)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETERS"

    def test_empty_string_parameters(self):
        """parameters='' should fail type check."""
        wire, err = _prepare_parameters("SParameter", "", "create", allow_empty=False)
        assert err is not None
        assert err["error_code"] == "INVALID_PARAMETERS"

    def test_none_parameters_create(self):
        """None should be treated as empty (allowed for create)."""
        wire, err = _prepare_parameters("SParameter", None, "create", allow_empty=False)
        assert err is not None  # None is not a dict

    def test_zero_not_falsy(self):
        """parameters={'Pts': {'value': '0'}} is valid."""
        wire, err = _prepare_parameters("SParameter",
            {"Pts": {"value": "0"}}, "create", allow_empty=True)
        assert err is None


# ═══════════════════════════════════════════════════════════
# 11. gRPC 层行为验证
# ═══════════════════════════════════════════════════════════

class TestGrpcTerminalResult:
    def test_stream_disconnected_status(self):
        """Verify STREAM_DISCONNECTED constant is used for stream-end-without-terminal."""
        from servers.eda.grpc_client import _terminal_result
        result = _terminal_result(
            success=False, status="STREAM_DISCONNECTED",
            message="FetchEvent 流已结束但未收到终态事件",
            client_uuid="u1", task_id="t1",
            task_type_name="OPEN_PROJECT",
            project_path="/fake/p.epp", result_path="",
            ads_output="partial log", log_complete=False,
            latest_details={},
        )
        assert result["status"] == "STREAM_DISCONNECTED"
        assert result["log_complete"] is False
        assert result["ads_output"] == "partial log"

    def test_grpc_unavailable_result(self):
        """Verify GRPC_UNAVAILABLE result structure."""
        from servers.eda.grpc_client import _terminal_result
        result = _terminal_result(
            success=False, status="GRPC_UNAVAILABLE",
            message="无法连接 EDA gRPC",
            client_uuid="u1", task_id="t1",
            task_type_name="OPEN_PROJECT",
            project_path="", result_path="",
            ads_output="", log_complete=False,
            latest_details={},
        )
        assert result["status"] == "GRPC_UNAVAILABLE"
        assert result["success"] is False

    def test_success_message_is_generic(self):
        """Default message should be 'task completed' not 'simulation finished'."""
        from servers.eda.grpc_client import _terminal_result
        result = _terminal_result(
            success=True, status="SUCCEEDED",
            message="task completed",
            client_uuid="u1", task_id="t1",
            task_type_name="CREATE_SIMULATION_COMPONENT",
            project_path="", result_path="",
            ads_output="", log_complete=True,
            latest_details={},
        )
        assert "task completed" in result["message"]


# ═══════════════════════════════════════════════════════════
# 12. 工具注册数量
# ═══════════════════════════════════════════════════════════

class TestToolCount:
    def test_default_tool_count(self):
        from servers import mcp
        from servers.multimodal_vision import OPENCLAW_WORKSPACE_PATH
        tools = [t.name for t in mcp._tool_manager._tools.values()]
        # Base tools should be at least 33 (without workspace copy tool)
        if OPENCLAW_WORKSPACE_PATH:
            assert "copy_image_to_workspace" in tools
        else:
            assert "copy_image_to_workspace" not in tools
        # All 7 sim component tools should be present
        for name in ["create_simulation_component", "update_simulation_component",
                     "delete_simulation_component", "set_component_active_state",
                     "generate_schematic_from_netlist"]:
            assert name in tools, f"Missing tool: {name}"

    def test_no_upsert_registered(self):
        """Verify upsert_simulation_component is NOT in the tool registry."""
        from servers import mcp
        tools = [t.name for t in mcp._tool_manager._tools.values()]
        assert "upsert_simulation_component" not in tools


# ═══════════════════════════════════════════════════════════
# 13. gRPC payload integration (mock call_grpc)
# ═══════════════════════════════════════════════════════════

from unittest.mock import patch
from proto import ecserver_pb2


_MOCK_GRPC_OK = {
    "success": True, "completed": True, "status": "SUCCEEDED",
    "message": "task completed", "project_path": "", "result_path": "",
    "ads_output": "", "log_complete": True, "details": {},
}

_VALIDATE_PATCH = patch(
    "servers.eda.simulation_components.validate_project_path",
    return_value="C:/test.epp",
)
_CALL_GRPC_PATCH = patch(
    "servers.eda.simulation_components.call_grpc",
    return_value=_MOCK_GRPC_OK,
)
_NETLIST_ISFILE_PATCH = patch(
    "servers.eda.simulation_components.Path.is_file",
    return_value=True,
)
_DISK_LOOKUP_PATCH = patch(
    "servers.eda.simulation_components._find_component_by_instance",
    return_value=(None, None),
)


class TestGrpcPayloads:
    """验证 7 个工具的最终 gRPC 枚举值和 payload 字段。"""

    def test_create_enum_and_payload(self):
        from servers.eda.simulation_components import create_simulation_component
        with patch("servers.eda.simulation_components.call_project_grpc",
                   return_value=_MOCK_GRPC_OK) as mock:
            create_simulation_component("C:/test.epp", "HarmonicBalance")
            assert mock.call_args.args[0] == ecserver_pb2.CREATE_SIMULATION_COMPONENT
            assert mock.call_args.args[1] == "C:/test.epp"
            assert mock.call_args.kwargs["component_type"] == "HarmonicBalance"

    def test_create_empty_component_type_rejected(self):
        from servers.eda.simulation_components import create_simulation_component
        with _VALIDATE_PATCH:
            result = create_simulation_component("C:/test.epp", "")
            assert result["success"] is False

    def test_create_falsy_parameters_rejected(self):
        from servers.eda.simulation_components import update_simulation_component
        with _VALIDATE_PATCH:
            result = update_simulation_component("C:/test.epp", "R1", parameters=[])
            assert result["success"] is False

    def test_update_enum_and_payload(self):
        from servers.eda.simulation_components import update_simulation_component
        with _VALIDATE_PATCH, _CALL_GRPC_PATCH as mock, \
             patch("servers.eda.simulation_components._find_component_by_instance",
                   return_value=({"type": "HarmonicBalance", "name": "HB1"}, None)):
            update_simulation_component(
                "C:/test.epp", "HB1",
                {"Freq": {"value": "2", "unit": "GHz"}},
            )
            assert mock.call_args.args[0] == ecserver_pb2.UPDATE_SIMULATION_COMPONENT
            payload = mock.call_args.args[1]
            assert payload["instance_name"] == "HB1"
            assert "Freq[1]" in payload["parameters"]

    def test_update_explicit_type(self):
        from servers.eda.simulation_components import update_simulation_component
        with _VALIDATE_PATCH, _CALL_GRPC_PATCH as mock, _DISK_LOOKUP_PATCH:
            update_simulation_component(
                "C:/test.epp", "HB2",
                {"Freq": {"value": "3", "unit": "GHz"}},
                component_type="HarmonicBalance",
            )
            assert mock.call_args.args[0] == ecserver_pb2.UPDATE_SIMULATION_COMPONENT

    def test_update_type_mismatch(self):
        from servers.eda.simulation_components import update_simulation_component
        with _VALIDATE_PATCH, \
             patch("servers.eda.simulation_components._find_component_by_instance",
                   return_value=({"type": "HarmonicBalance", "name": "HB1"}, None)):
            result = update_simulation_component(
                "C:/test.epp", "HB1",
                {"Start": {"value": "1", "unit": "GHz"}},
                component_type="SParameter",
            )
            assert result["success"] is False
            assert result["error_code"] == "COMPONENT_TYPE_MISMATCH"

    def test_update_no_type_available(self):
        from servers.eda.simulation_components import update_simulation_component
        with _VALIDATE_PATCH, _DISK_LOOKUP_PATCH:
            result = update_simulation_component(
                "C:/test.epp", "Unknown1",
                {"Freq": {"value": "1", "unit": "GHz"}},
            )
            assert result["success"] is False
            assert result["error_code"] == "COMPONENT_TYPE_REQUIRED"

    def test_delete_enum_and_payload(self):
        from servers.eda.simulation_components import delete_simulation_component
        with patch("servers.eda.simulation_components.call_project_grpc",
                   return_value=_MOCK_GRPC_OK) as mock:
            delete_simulation_component("C:/test.epp", "R1")
            assert mock.call_args.args[0] == ecserver_pb2.DELETE_SIMULATION_COMPONENT
            assert mock.call_args.args[1] == "C:/test.epp"
            assert mock.call_args.kwargs["instance_name"] == "R1"

    def test_delete_empty_name_rejected(self):
        from servers.eda.simulation_components import delete_simulation_component
        with _VALIDATE_PATCH:
            result = delete_simulation_component("C:/test.epp", "")
            assert result["success"] is False
            assert result["error_code"] == "EMPTY_INSTANCE_NAME"

    def test_set_state_enum_and_payload(self):
        from servers.eda.simulation_components import set_component_active_state
        with patch("servers.eda.simulation_components.call_project_grpc",
                   return_value=_MOCK_GRPC_OK) as mock:
            set_component_active_state("C:/test.epp", "R1", "disabled")
            assert mock.call_args.args[0] == ecserver_pb2.SET_COMPONENT_ACTIVE_STATE
            assert mock.call_args.args[1] == "C:/test.epp"
            assert mock.call_args.kwargs["state"] == "DISABLED"
            assert mock.call_args.kwargs["instance_name"] == "R1"

    def test_set_state_invalid_rejected(self):
        from servers.eda.simulation_components import set_component_active_state
        with _VALIDATE_PATCH:
            result = set_component_active_state("C:/test.epp", "R1", "BROKEN")
            assert result["success"] is False
            assert result["error_code"] == "INVALID_ACTIVE_STATE"

    def test_generate_enum_confirm_clear_not_leaked(self):
        from servers.eda.simulation_components import generate_schematic_from_netlist
        with _VALIDATE_PATCH, _NETLIST_ISFILE_PATCH, _CALL_GRPC_PATCH as mock:
            generate_schematic_from_netlist(
                "C:/test.epp", "C:/test/netlist.log",
                clear_before_import=False,
            )
            assert mock.call_args.args[0] == ecserver_pb2.GENERATE_SCHEMATIC_FROM_NETLIST
            payload = mock.call_args.args[1]
            assert "confirm_clear" not in payload
            assert payload["clear_before_import"] is False

    def test_generate_clear_without_confirmation(self):
        from servers.eda.simulation_components import generate_schematic_from_netlist
        with _VALIDATE_PATCH, _NETLIST_ISFILE_PATCH:
            result = generate_schematic_from_netlist(
                "C:/test.epp", "C:/test/netlist.log",
                clear_before_import=True,
            )
            assert result["success"] is False
            assert result["error_code"] == "CLEAR_CONFIRMATION_REQUIRED"

    def test_generate_clear_confirmed(self):
        from servers.eda.simulation_components import generate_schematic_from_netlist
        with _VALIDATE_PATCH, _NETLIST_ISFILE_PATCH, _CALL_GRPC_PATCH as mock:
            generate_schematic_from_netlist(
                "C:/test.epp", "C:/test/netlist.log",
                clear_before_import=True, confirm_clear=True,
            )
            payload = mock.call_args.args[1]
            assert "confirm_clear" not in payload
            assert payload["clear_before_import"] is True


# ═══════════════════════════════════════════════════════════
# 10. _find_sim_components 过滤逻辑（include_hidden / schematic_name）
# ═══════════════════════════════════════════════════════════

class TestFindSimComponents:
    """测试 _find_sim_components 的过滤逻辑（此前无单测覆盖的盲区）。"""

    @staticmethod
    def _setup(monkeypatch, comps, schematics=None):
        """mock ProjectReader 和 parse_components，返回 simulation_components 模块。"""
        from servers.eda import simulation_components as sc

        class FakeReader:
            def __init__(self, path):
                self.path = path

            def list_schematics(self):
                return schematics or ["main"]

            def read_schematic(self, name):
                return f"fake:{name}"

        monkeypatch.setattr(sc, "ProjectReader", FakeReader)
        monkeypatch.setattr(sc, "parse_components", lambda raw: comps)
        return sc

    def test_include_hidden_filters_hidden_params(self, monkeypatch):
        """include_hidden=False（默认）过滤 visible=false 的隐藏参数；True 保留。"""
        comps = [{
            "name": "R1", "type": "R", "component_id": "uuid1", "model_id": "m1",
            "paramsinfo": {
                "R": {"value": "50", "unit": "Ohm", "visible": True},
                "Hidden": {"value": "x", "visible": False},
            },
            "pin_count": 2, "parameter_count": 2,
        }]
        sc = self._setup(monkeypatch, comps)

        # 默认过滤隐藏参数
        result = sc._find_sim_components("fake.epp")
        assert "R" in result[0]["parameters"]
        assert "Hidden" not in result[0]["parameters"]

        # include_hidden=True 保留隐藏参数
        result = sc._find_sim_components("fake.epp", include_hidden=True)
        assert "R" in result[0]["parameters"]
        assert "Hidden" in result[0]["parameters"]

    def test_include_hidden_sp_type(self, monkeypatch):
        """SP 类型走 wire→public 映射，同时过滤 visible=false。"""
        comps = [{
            "name": "SP1", "type": "SParameter", "component_id": "uuid2", "model_id": "m2",
            "paramsinfo": {
                "Start": {"value": "1", "unit": "GHz", "visible": True},
                "Hidden": {"value": "x", "visible": False},
            },
            "pin_count": 0, "parameter_count": 2,
        }]
        sc = self._setup(monkeypatch, comps)

        result = sc._find_sim_components("fake.epp")
        params = result[0]["parameters"]
        assert "Start" in params
        assert params["Start"]["value"] == "1"
        assert "Hidden" not in params

    def test_schematic_name_filters(self, monkeypatch):
        """schematic_name 指定时只读取匹配的原理图。"""
        from servers.eda import simulation_components as sc

        read_names = []

        class FakeReader:
            def __init__(self, path):
                pass

            def list_schematics(self):
                return ["main", "other"]

            def read_schematic(self, name):
                read_names.append(name)
                return f"fake:{name}"

        monkeypatch.setattr(sc, "ProjectReader", FakeReader)
        monkeypatch.setattr(sc, "parse_components", lambda raw: [{
            "name": "R1", "type": "R", "component_id": "u", "model_id": "m",
            "paramsinfo": {}, "pin_count": 2, "parameter_count": 0,
        }])

        # 指定 schematic_name="main" 只读 main
        result = sc._find_sim_components("fake.epp", schematic_name="main")
        assert read_names == ["main"]
        assert len(result) == 1

        # 不指定则读全部
        read_names.clear()
        result = sc._find_sim_components("fake.epp")
        assert read_names == ["main", "other"]
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════
# 10.5 list_simulation_components summary_only 裁剪
# ═══════════════════════════════════════════════════════════

class TestListSummaryOnly:
    """测试 list_simulation_components 的 summary_only 裁剪逻辑。"""

    def test_summary_only_strips_parameters(self, monkeypatch):
        from pathlib import Path
        from servers.eda import simulation_components as sc

        class FakeReader:
            def __init__(self, path):
                self.epp_path = Path(path)

            def list_schematics(self):
                return ["main"]

            def read_schematic(self, name):
                return f"fake:{name}"

        monkeypatch.setattr(sc, "ProjectReader", FakeReader)
        monkeypatch.setattr(sc, "parse_components", lambda raw: [{
            "name": "R1", "type": "R", "component_id": "u1", "model_id": "m1",
            "paramsinfo": {"R": {"value": "50", "unit": "Ohm"}},
            "pin_count": 2, "parameter_count": 1,
        }])

        # 默认返回完整字段（含 parameters）
        full = sc.list_simulation_components("fake.epp")
        assert "parameters" in full["components"][0]

        # summary_only=True 只保留 4 个核心字段，裁掉 parameters
        summary = sc.list_simulation_components("fake.epp", summary_only=True)
        comp = summary["components"][0]
        assert set(comp.keys()) == {"component_type", "instance_name", "model_id", "pin_count"}
        assert "parameters" not in comp


# ═══════════════════════════════════════════════════════════
# 11. replace_schematic_from_file 工具
# ═══════════════════════════════════════════════════════════

class TestLoadSchematicFromFile:
    """测试 replace_schematic_from_file 工具（参数校验 + payload）。"""

    def test_load_enum_and_payload(self):
        """正确传递 LOAD_SCHEMATIC_FROM_FILE 枚举和 payload。"""
        from servers.eda.simulation_components import replace_schematic_from_file
        with _VALIDATE_PATCH, _NETLIST_ISFILE_PATCH, _CALL_GRPC_PATCH as mock:
            replace_schematic_from_file("C:/test.epp", "C:/test/schematic.ep")
            args = mock.call_args.args
            assert args[0] == ecserver_pb2.LOAD_SCHEMATIC_FROM_FILE
            assert args[1]["project_path"] == "C:/test.epp"
            assert args[1]["schematic_path"].endswith("schematic.ep")

    def test_load_schematic_file_not_found(self):
        """schematic_path 不存在时返回 FILE_NOT_FOUND。"""
        from servers.eda.simulation_components import replace_schematic_from_file
        with _VALIDATE_PATCH, patch(
            "servers.eda.simulation_components.Path.is_file", return_value=False
        ):
            result = replace_schematic_from_file("C:/test.epp", "C:/test/missing.ep")
            assert result["success"] is False
            assert result["error_code"] == "FILE_NOT_FOUND"
