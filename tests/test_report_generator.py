"""Tests for servers/report/generator.py — report tool validation."""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from pathlib import Path

from servers.report.generator import generate_simulation_report
from servers.utils import error_response as _error


_MOCK_OK = {
    "success": True, "file_path": "C:/test.pdf", "file_type": "pdf",
    "file_size": 1000, "sections": ["封面"],
    "chart_count": 0, "component_count": 0, "spec_row_count": 3,
    "created_at": "2026-08-03T15:30:00",
}


def _ok_path(tmp_path):
    """返回一个有效的输出路径（tmp_path 保证父目录存在）。"""
    return str(tmp_path / "report.pdf")


# ═══════════════════════════════════════════════════════════
# output_path
# ═══════════════════════════════════════════════════════════

class TestOutputPath:
    def test_empty(self):
        assert generate_simulation_report("", "OK")["error_code"] == "INVALID_OUTPUT_PATH"

    def test_relative(self):
        assert generate_simulation_report("test.pdf", "OK")["error_code"] == "INVALID_OUTPUT_PATH"

    def test_unc(self):
        r = generate_simulation_report(r"\\server\share\test.pdf", "OK")
        assert r["error_code"] == "INVALID_OUTPUT_PATH"

    def test_bad_extension(self, tmp_path):
        r = generate_simulation_report(str(tmp_path / "test.txt"), "OK")
        assert r["error_code"] == "INVALID_OUTPUT_PATH"

    def test_docx_ok(self, tmp_path):
        r = generate_simulation_report(str(tmp_path / "test.docx"), "OK")
        assert r.get("error_code") != "INVALID_OUTPUT_PATH"

    def test_parent_dir_missing(self):
        r = generate_simulation_report("C:/nonexistent_xyz_123/test.pdf", "OK")
        assert r["error_code"] == "OUTPUT_DIRECTORY_NOT_FOUND"

    def test_overwrite_check(self, tmp_path):
        f = tmp_path / "exists.pdf"
        f.write_text("old")
        r = generate_simulation_report(str(f), "OK")
        assert r["error_code"] == "OUTPUT_ALREADY_EXISTS"


# ═══════════════════════════════════════════════════════════
# model_name
# ═══════════════════════════════════════════════════════════

class TestModelName:
    def test_empty(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "")
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_too_long(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "A" * 201)
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_ok(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "ValidName")
        assert r.get("error_code") not in ("INVALID_REPORT_PARAMETERS", "INVALID_OUTPUT_PATH")  # passes validation


# ═══════════════════════════════════════════════════════════
# spec_table
# ═══════════════════════════════════════════════════════════

class TestSpecTable:
    def test_non_list(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table="bad")
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_non_2d(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table=["a"])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_not_7_cols(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK",
            spec_table=[["A", "B"]])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_inconsistent(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK",
            spec_table=[list("ABCDEFG"), ["X", "Y"]])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_null(self, tmp_path):
        row = ["X"] + [""] * 5 + [None]
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table=[list("ABCDEFG"), row])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_bool(self, tmp_path):
        row = ["X"] + [""] * 5 + [True]
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table=[list("ABCDEFG"), row])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_empty_skipped(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table=[])
        assert r.get("error_code") != "INVALID_REPORT_PARAMETERS"

    def test_none_skipped(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", spec_table=None)
        assert r.get("error_code") != "INVALID_REPORT_PARAMETERS"


# ═══════════════════════════════════════════════════════════
# charts
# ═══════════════════════════════════════════════════════════

class TestCharts:
    def test_non_list(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", charts="bad")
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_relative_path(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK",
            charts=[{"path": "img.png", "title": "t"}])
        assert r["error_code"] == "INVALID_CHART_PATH"

    def test_bad_extension(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK",
            charts=[{"path": "C:/img.txt", "title": "t"}])
        assert r["error_code"] == "INVALID_CHART_PATH"

    def test_empty_skipped(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", charts=[])
        assert r.get("error_code") != "INVALID_REPORT_PARAMETERS"


# ═══════════════════════════════════════════════════════════
# components
# ═══════════════════════════════════════════════════════════

class TestComponents:
    def test_non_list(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", components="bad")
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_non_string_field(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK",
            components=[{"type": 123, "model": "M", "manufacturer": "A", "specs": "S"}])
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"

    def test_empty_skipped(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", components=[])
        assert r.get("error_code") != "INVALID_REPORT_PARAMETERS"


# ═══════════════════════════════════════════════════════════
# timeout
# ═══════════════════════════════════════════════════════════

class TestTimeout:
    def test_bool_rejected(self, tmp_path):
        r = generate_simulation_report(_ok_path(tmp_path), "OK", timeout_seconds=True)
        assert r["error_code"] == "INVALID_REPORT_PARAMETERS"


