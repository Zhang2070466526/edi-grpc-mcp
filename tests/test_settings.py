"""测试配置加载 settings.py —— 环境变量读取、范围限制、启动校验。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestReaders:
    def test_read_str_default(self):
        from servers.settings import _read_str
        with patch.dict("os.environ", {}, clear=True):
            assert _read_str("NOPE", "dflt") == "dflt"

    def test_read_str_trims(self):
        from servers.settings import _read_str
        with patch.dict("os.environ", {"X": "  abc  "}, clear=True):
            assert _read_str("X", "") == "abc"

    def test_read_bool_true_variants(self):
        from servers.settings import _read_bool
        for v in ("1", "true", "yes", "on"):
            with patch.dict("os.environ", {"X": v}, clear=True):
                assert _read_bool("X", False) is True

    def test_read_bool_default(self):
        from servers.settings import _read_bool
        with patch.dict("os.environ", {}, clear=True):
            assert _read_bool("X", True) is True

    def test_read_int_clamps(self):
        from servers.settings import _read_int
        with patch.dict("os.environ", {"X": "999999"}, clear=True):
            assert _read_int("X", 5, 1, 100) == 100

    def test_read_int_invalid_falls_back(self):
        from servers.settings import _read_int
        with patch.dict("os.environ", {"X": "abc"}, clear=True):
            assert _read_int("X", 42, 1, 100) == 42


class TestSettingsValidate:
    def test_valid_defaults(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="127.0.0.1:50055", mcp_transport="streamable-http", mcp_port=50026)
        assert s.validate() == []

    def test_invalid_grpc_address(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="no-port")
        assert any("EDA_GRPC_SERVER" in i for i in s.validate())

    def test_invalid_grpc_port(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="127.0.0.1:99999")
        assert any("EDA_GRPC_SERVER" in i for i in s.validate())

    def test_invalid_transport(self):
        from servers.settings import Settings
        s = Settings(mcp_transport="sse")
        assert any("MCP_TRANSPORT" in i for i in s.validate())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
