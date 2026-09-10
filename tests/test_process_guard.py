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
    """白名单核心：路径型 + 关键词型精确匹配。"""
    from servers.process_guard import _matches_whitelist

    exe = r"C:\Users\JGL\AppData\Roaming\uv\python\cpython-3.11.15\python.exe"
    cmd = "python.exe -m hermes_cli.main gateway run"

    assert _matches_whitelist("hermes_cli.main", exe, cmd) is True   # 完整模块名命中
    assert _matches_whitelist("claude", exe, cmd) is False           # 其他 agent 不命中


def test_whitelist_precise_no_substring_false_positive():
    """短串 / 裸解释器名 / 前缀不误命中。"""
    from servers.process_guard import _matches_whitelist

    exe = r"C:\Python\python.exe"
    cmd = "python.exe -m myagent.main run"

    assert _matches_whitelist("myagent.main", exe, cmd) is True     # 完整模块名
    assert _matches_whitelist("python", exe, cmd) is False          # 裸解释器名不命中
    assert _matches_whitelist("python.exe", exe, cmd) is False      # 通用解释器硬编码排除
    assert _matches_whitelist("node.exe", exe, cmd) is False        # 其他通用解释器同样排除
    assert _matches_whitelist("r", exe, cmd) is False               # 单字母不命中
    assert _matches_whitelist("myagent", exe, cmd) is False         # 前缀不命中（须完整 token）


def test_whitelist_path_entry():
    """路径型条目：精确相等或目录前缀。"""
    from servers.process_guard import _matches_whitelist

    exe = r"C:\Tools\Hermes\Hermes.exe"
    assert _matches_whitelist(r"C:\Tools\Hermes\Hermes.exe", exe, "any") is True   # 完整路径
    assert _matches_whitelist(r"C:\Tools\Hermes", exe, "any") is True              # 目录前缀
    assert _matches_whitelist(r"C:\Tools\Other", exe, "any") is False              # 不同目录


def test_whitelist_bare_filename_matches_basename():
    """裸文件名（含 .exe）命中 exe 的 basename，即使 argv[0] 是完整路径。"""
    from servers.process_guard import _matches_whitelist

    exe = r"C:\Program Files (x86)\EDI\edi-agent\service\edi-agent-service.exe"
    cmd = r"C:\Program Files (x86)\EDI\edi-agent\service\edi-agent-service.exe serve --host 127.0.0.1 --port 0"

    assert _matches_whitelist("edi-agent-service.exe", exe, cmd) is True   # 裸文件名命中 basename
    assert _matches_whitelist("edi-agent", exe, cmd) is False              # 目录名不是 basename，不命中
    assert _matches_whitelist("serve", exe, cmd) is True                   # 完整 cmd token 命中


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
