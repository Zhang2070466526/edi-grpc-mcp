"""测试配置加载 settings.py —— 环境变量读取、类型转换、范围校验、启动校验。"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSettingsRead:
    """测试 Settings 从环境变量读取 + 类型转换（BaseSettings）。"""

    def test_defaults(self):
        from servers.settings import Settings
        with patch.dict("os.environ", {}, clear=True):
            s = Settings(_env_file=None)
            assert s.mcp_port == 50026
            assert s.eda_grpc_server == "127.0.0.1:50055"
            assert s.mcp_stateless_http is True
            assert s.mcp_api_key == ""

    def test_env_override(self):
        from servers.settings import Settings
        with patch.dict("os.environ", {"MCP_PORT": "9000", "MCP_STATELESS_HTTP": "false"}, clear=True):
            s = Settings(_env_file=None)
            assert s.mcp_port == 9000
            assert s.mcp_stateless_http is False

    def test_bool_variants(self):
        from servers.settings import Settings
        for v in ("true", "yes", "1", "on"):
            with patch.dict("os.environ", {"MCP_STATELESS_HTTP": v}, clear=True):
                assert Settings(_env_file=None).mcp_stateless_http is True

    def test_int_out_of_range_rejected(self):
        from servers.settings import Settings
        with patch.dict("os.environ", {"MCP_PORT": "999999"}, clear=True):
            with pytest.raises(ValidationError):
                Settings(_env_file=None)

    def test_frozen(self):
        from servers.settings import Settings
        s = Settings(_env_file=None)
        with pytest.raises(ValidationError):
            s.mcp_port = 9999


class TestSettingsValidate:
    """测试 validate() 业务级格式校验（非阻断，仅返回问题列表）。"""

    def test_valid_defaults(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="127.0.0.1:50055", mcp_transport="streamable-http",
                     mcp_port=50026, _env_file=None)
        assert s.validate() == []

    def test_invalid_grpc_address(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="no-port", _env_file=None)
        assert any("EDA_GRPC_SERVER" in i for i in s.validate())

    def test_invalid_grpc_port(self):
        from servers.settings import Settings
        s = Settings(eda_grpc_server="127.0.0.1:99999", _env_file=None)
        assert any("EDA_GRPC_SERVER" in i for i in s.validate())

    def test_invalid_transport(self):
        from servers.settings import Settings
        s = Settings(mcp_transport="sse", _env_file=None)
        assert any("MCP_TRANSPORT" in i for i in s.validate())


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
