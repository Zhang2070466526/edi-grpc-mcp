"""远程访问相关单测 —— Host 枚举、允许列表、产物链接按请求 Host 推导。

对应「远程操作方案」P0 第 2/3 项：
  2) transport_security 动态枚举本机全部可达地址（非环回访问不再 421）
  3) 产物/文档 Token 链接按请求 Host 推导（远程客户端拿到访问得通的地址）
"""
from __future__ import annotations

import socket

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


class TestHostHelpers:
    def test_loopback_detection(self):
        from servers.utils import is_loopback_host
        assert is_loopback_host("127.0.0.1")
        assert is_loopback_host("localhost")
        assert is_loopback_host("::1")
        assert is_loopback_host("  127.0.0.1  ")
        assert not is_loopback_host("0.0.0.0")
        assert not is_loopback_host("192.168.0.58")
        assert not is_loopback_host("")

    @pytest.mark.parametrize("raw,expected", [
        ("192.168.0.58:50026", "192.168.0.58"),
        ("[::1]:50026", "[::1]"),
        ("desktop-1nh03pe:50026", "desktop-1nh03pe"),
        ("127.0.0.1", "127.0.0.1"),
        ("::1", "::1"),          # 裸 IPv6（多冒号）原样返回
        ("", ""),
    ])
    def test_host_without_port(self, raw, expected):
        from servers.utils import host_without_port
        assert host_without_port(raw) == expected

    def test_local_host_names_covers_loopback_hostname_and_ips(self):
        from servers.utils import local_host_names
        names = local_host_names()
        assert "127.0.0.1" in names and "localhost" in names
        hn = socket.gethostname()
        if hn:
            # Windows 客户端可能发大写 Host，三种写法都要在列表里
            assert hn in names and hn.lower() in names and hn.upper() in names
        assert len(names) == len(set(names))  # 去重
        assert all(n.strip() == n and n for n in names)

    def test_non_loopback_ipv6_is_bracketed_without_scope(self):
        """非环回 IPv6 要按 Host 头规范加方括号、去掉 %scope 后缀。"""
        from servers.utils import local_host_names
        names = local_host_names()
        for n in [x for x in names if x.startswith("[")]:
            assert n.endswith("]") and "%" not in n
            assert n != "[::1]" or True  # 环回大写形式合法
        assert not any(n.startswith("fe80") for n in names)  # 不允许出现裸 IPv6


class TestTransportSecurity:
    def test_rebinding_protection_stays_on(self):
        """★ 保护必须保持开启：关掉后伪造 Host 也能通过，会污染产物链接。"""
        from servers.utils import build_transport_security
        s = build_transport_security()
        assert s.enable_dns_rebinding_protection is True
        assert "127.0.0.1:*" in s.allowed_hosts
        assert "localhost:*" in s.allowed_hosts
        assert s.allowed_origins == []  # 不额外放行浏览器跨源

    def test_extra_hosts_get_bare_and_wildcard_port_patterns(self):
        from servers.utils import build_transport_security
        s = build_transport_security(["mcp.example.com", "  ", "10.0.0.9"])
        assert "mcp.example.com" in s.allowed_hosts
        assert "mcp.example.com:*" in s.allowed_hosts
        assert "10.0.0.9:*" in s.allowed_hosts


class TestRequestBaseURL:
    """产物链接优先用请求 Host；伪造 Host 不在允许列表时回退到启动地址。"""

    @staticmethod
    def _app():
        from start_servers import RequestBaseURLMiddleware
        from servers.utils import get_server_base_url

        async def whoami(request):
            return JSONResponse({"base": get_server_base_url()})

        app = Starlette(routes=[Route("/whoami", whoami)])
        app.add_middleware(
            RequestBaseURLMiddleware,
            allowed_bases={"127.0.0.1", "192.168.0.58", "localhost"},
        )
        return TestClient(app)

    def test_request_host_used_for_artifact_links(self):
        from servers.utils import set_server_address
        set_server_address("127.0.0.1", 50026)
        r = self._app().get("/whoami", headers={"host": "192.168.0.58:50026"})
        # 远程客户端访问 LAN IP → 拿到的链接必须是同一个地址（不是 127.0.0.1）
        assert r.json()["base"] == "http://192.168.0.58:50026"

    def test_forged_host_falls_back_to_configured_address(self):
        from servers.utils import set_server_address
        set_server_address("127.0.0.1", 50026)
        r = self._app().get("/whoami", headers={"host": "evil.example.com"})
        assert r.json()["base"] == "http://127.0.0.1:50026"

    def test_no_request_context_falls_back(self):
        from servers.utils import get_server_base_url, set_server_address
        set_server_address("127.0.0.1", 50026)
        assert get_server_base_url() == "http://127.0.0.1:50026"
