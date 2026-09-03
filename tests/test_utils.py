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
