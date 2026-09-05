"""测试公共工具层 utils.py —— 文件校验、错误响应、地址管理、链接生成。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestValidateFile:
    def test_existing_file(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.cst"
        f.write_text("x")
        assert validate_file(str(f), (".cst",)) == str(f.resolve())

    def test_missing_file(self, tmp_path):
        from servers.utils import validate_file
        with pytest.raises(FileNotFoundError):
            validate_file(str(tmp_path / "missing.cst"))

    def test_wrong_extension(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.txt"
        f.write_text("x")
        with pytest.raises(ValueError):
            validate_file(str(f), (".cst",))

    def test_no_extension_filter(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.any"
        f.write_text("x")
        assert validate_file(str(f)) == str(f.resolve())

    def test_extension_case_insensitive(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.CST"
        f.write_text("x")
        assert validate_file(str(f), (".cst",)) == str(f.resolve())


class TestToolError:
    def test_basic(self):
        from servers.utils import error_response
        assert error_response("CODE", "msg") == {"success": False, "error_code": "CODE", "message": "msg"}

    def test_retryable(self):
        from servers.utils import error_response
        r = error_response("CODE", "msg", retryable=True)
        assert r["retryable"] is True

    def test_extra(self):
        from servers.utils import error_response
        r = error_response("CODE", "msg", detail="x")
        assert r["details"] == {"detail": "x"}


class TestRequirePosition:
    def test_valid(self):
        from servers.utils import require_position
        pos, err = require_position({"x": 100, "y": 200})
        assert err is None and pos == {"x": 100, "y": 200}

    def test_rejects_non_dict(self):
        from servers.utils import require_position
        for bad in (None, "x=1", [1, 2], 42):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_missing_axis(self):
        from servers.utils import require_position
        pos, err = require_position({"x": 1})
        assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_numeric(self):
        from servers.utils import require_position
        for bad in ({"x": "1", "y": 2}, {"x": 1, "y": "2"}, {"x": True, "y": 1}):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_finite(self):
        from servers.utils import require_position
        for bad in ({"x": float("nan"), "y": 1}, {"x": 1, "y": float("inf")}):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"


class TestRequireUuid:
    def test_valid(self):
        from servers.utils import require_uuid
        u = "12345678-1234-4234-8234-123456789abc"
        v, err = require_uuid(u)
        assert err is None and v == u

    def test_rejects_invalid(self):
        from servers.utils import require_uuid
        for bad in ("not-a-uuid", "12345", "uuid-1", ""):
            v, err = require_uuid(bad)
            assert v == "" and err["error_code"] == "INVALID_PARAMETERS"


class TestBuildFileLink:
    def test_markdown_link(self, tmp_path):
        from servers.utils import build_file_link
        f = tmp_path / "r.pdf"
        f.write_text("x")
        r = build_file_link(str(f), label="报告")
        assert r["file_uri"].startswith("file://")
        assert r["markdown_link"].startswith("[报告](file://")


class TestServerAddress:
    def test_localhost_base_url(self):
        from servers.utils import set_server_address, get_server_base_url
        set_server_address("127.0.0.1", 50026)
        assert get_server_base_url() == "http://127.0.0.1:50026"

    def test_wildcard_host_maps_to_localhost(self):
        from servers.utils import set_server_address, get_server_base_url
        set_server_address("0.0.0.0", 9000)
        assert get_server_base_url() == "http://127.0.0.1:9000"


class TestUptime:
    def test_non_negative(self):
        from servers.utils import server_uptime_seconds
        assert server_uptime_seconds() >= 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
