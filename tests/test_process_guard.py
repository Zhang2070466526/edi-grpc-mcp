"""进程白名单守卫测试 —— exe 路径 + 命令行反查与白名单匹配。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_norm():
    from servers.process_guard import _norm
    assert _norm(r"C:\Foo\Bar.exe") == "c:/foo/bar.exe"
    assert _norm("C:/Foo/Bar.EXE") == "c:/foo/bar.exe"


def test_find_client_process(monkeypatch):
    import psutil
    from servers import process_guard as pg

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def exe(self):
            return r"C:\Users\JGL\AppData\Roaming\uv\python\cpython-3.11.15\python.exe"

        def cmdline(self):
            return ["python.exe", "-m", "hermes_cli.main", "gateway", "run"]

    def _conn(raddr_port, laddr_port, pid, status="ESTABLISHED"):
        return SimpleNamespace(
            status=status,
            laddr=SimpleNamespace(port=laddr_port),
            raddr=SimpleNamespace(port=raddr_port),
            pid=pid,
        )

    monkeypatch.setattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED")
    monkeypatch.setattr(psutil, "net_connections",
                        lambda kind: [_conn(50026, 12345, 999)])
    monkeypatch.setattr(psutil, "Process", FakeProc)

    exe, cmd = pg.find_client_process(12345, 50026)
    assert "python.exe" in exe
    assert "hermes_cli.main" in cmd


def test_find_client_process_no_match(monkeypatch):
    import psutil
    from servers import process_guard as pg

    monkeypatch.setattr(psutil, "CONN_ESTABLISHED", "ESTABLISHED")
    monkeypatch.setattr(psutil, "net_connections", lambda kind: [])
    exe, cmd = pg.find_client_process(12345, 50026)
    assert exe == "" and cmd == ""


def test_whitelist_matching_logic():
    """白名单核心：exe + cmdline 子串匹配。"""
    exe = r"C:\Users\JGL\AppData\Roaming\uv\python\cpython-3.11.15\python.exe"
    cmd = "python.exe -m hermes_cli.main gateway run"

    def _matches(allowed):
        haystack = (exe + " " + cmd).replace("\\", "/").lower()
        return any(a in haystack for a in allowed)

    assert _matches(["hermes_cli"]) is True        # 命令行关键词命中 Hermes
    assert _matches(["claude"]) is False           # 其他 agent 不命中
    assert _matches(["python.exe"]) is True        # 宽松的 exe 名也会命中


def test_whitelist_path_exemption(monkeypatch):
    """受保护路径(/mcp)做白名单校验，诊断路径(/health /ready)放行。"""
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from servers import process_guard as pg

    # 让 resolve_client_process 返回「非白名单」进程
    monkeypatch.setattr(
        pg, "resolve_client_process",
        lambda client_port, server_port: (r"C:\Python\python.exe", "python smoke.py"),
    )

    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[
        Route("/health", ok),
        Route("/ready", ok),
        Route("/ui", ok),
        Route("/chat", ok),
        Route("/mcp", ok),
    ])
    app.add_middleware(pg.ProcessWhitelistMiddleware, server_port=50026, allowed=["edi-agent"])

    client = TestClient(app)
    # 诊断路径 + 浏览器相关路径放行
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/ui").status_code == 200
    assert client.get("/chat").status_code == 200
    # 受保护路径：非白名单进程拒绝
    assert client.get("/mcp").status_code == 403
