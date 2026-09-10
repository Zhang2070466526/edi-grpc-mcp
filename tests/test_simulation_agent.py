"""SimulationAgent 集成测试 —— 客户端 session/request_id/调用/错误映射 + 工具 payload + Resource。"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _resp(status_code, payload=None, text=""):
    return SimpleNamespace(status_code=status_code, text=text, json=lambda: payload)


# ---------------------------------------------------------------------------
# client：稳定 request_id / session 解析
# ---------------------------------------------------------------------------

def test_stable_request_id_deterministic():
    from servers.tr_simulation import client as tr_client
    a = {"epp_path": "D:/proj/a.epp", "indicator": "增益"}
    b = {"indicator": "增益", "epp_path": "D:/proj/a.epp"}  # 顺序不同
    assert tr_client._stable_request_id("tr_find_paths", a) == tr_client._stable_request_id("tr_find_paths", b)
    assert tr_client._stable_request_id("tr_find_paths", a) != tr_client._stable_request_id(
        "tr_find_paths", {"epp_path": "D:/proj/b.epp", "indicator": "增益"})
    # 不同工具名不同 id
    assert tr_client._stable_request_id("tr_find_paths", a) != tr_client._stable_request_id("tr_modify_netlist", a)


def test_session_resolution_by_epp_path(monkeypatch):
    from servers.tr_simulation import client as tr_client
    tr_client._sessions.clear()
    tr_client._current_session_id = None
    counter = {"n": 0}

    def fake_create():
        counter["n"] += 1
        return f"sess-{counter['n']}"

    monkeypatch.setattr(tr_client, "_create_session", fake_create)

    s1 = tr_client._resolve_session("D:/proj/a.epp")
    s2 = tr_client._resolve_session("D:/proj/a.epp")
    s3 = tr_client._resolve_session("d:/proj/a.epp")   # 归一化后同路径
    s4 = tr_client._resolve_session("D:/proj/b.epp")
    assert s1 == s2 == s3
    assert s4 != s1
    assert counter["n"] == 2  # 只创建了两个 session

    # 无 epp_path 复用当前 session
    s5 = tr_client._resolve_session(None)
    assert s5 == s4


# ---------------------------------------------------------------------------
# client：call_tool 调用 / 轮询 / 归一化 / 错误映射
# ---------------------------------------------------------------------------

def _patch_session(monkeypatch, tr_client):
    # 清空模块级 session 状态，避免跨测试泄漏
    tr_client._sessions.clear()
    tr_client._current_session_id = None
    monkeypatch.setattr(tr_client, "_create_session", lambda: "sess-fixed")


def test_call_tool_immediate_completed(monkeypatch):
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)

    captured = {}

    def fake_post(url, json_body):
        captured["url"] = url
        captured["body"] = json_body
        return _resp(200, {"status": "completed", "operation_id": "op1", "result": {"success": True, "paths": []}})

    monkeypatch.setattr(tr_client, "_post", fake_post)

    r = tr_client.call_tool("tr_find_paths", {"epp_path": "D:/proj/a.epp"})
    assert r["success"] is True
    assert r["status"] == "completed"
    assert "paths" in r["result"]

    # 请求体构造：session + 稳定 request_id + arguments
    assert captured["url"].endswith("/api/v1/integration/tools/tr_find_paths/invoke")
    assert captured["body"]["session_id"] == "sess-fixed"
    assert captured["body"]["request_id"].startswith("mcp-tr_find_paths-")
    assert captured["body"]["arguments"] == {"epp_path": "D:/proj/a.epp"}


def test_call_tool_background_polls(monkeypatch):
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)

    invoke_state = {"called": 0}

    def fake_post(url, json_body):
        invoke_state["called"] += 1
        return _resp(202, {"status": "queued", "operation_id": "op-background"})

    def fake_get(url):
        # 第一次轮询仍 running，第二次完成
        if "running" not in invoke_state:
            invoke_state["running"] = True
            return _resp(200, {"status": "running", "operation_id": "op-background"})
        return _resp(200, {"status": "completed", "operation_id": "op-background",
                           "result": {"success": True, "raw_path": "C:/x/result.raw"}})

    monkeypatch.setattr(tr_client, "_post", fake_post)
    monkeypatch.setattr(tr_client, "_get", fake_get)
    monkeypatch.setattr(tr_client, "_POLL_INTERVAL_SECONDS", 0.0)

    r = tr_client.call_tool("tr_run_simulation", {"netlist_path": "C:/x/netlist.log"})
    assert r["success"] is True
    assert r["status"] == "completed"
    assert r["result"]["raw_path"] == "C:/x/result.raw"


def test_call_tool_operation_failed(monkeypatch):
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)

    monkeypatch.setattr(tr_client, "_post", lambda url, json_body: _resp(
        200, {"status": "failed", "operation_id": "op1", "error": "ADS 仿真失败"}))
    r = tr_client.call_tool("tr_run_simulation", {"netlist_path": "C:/x/n.log"})
    assert r["success"] is False
    assert r["error_code"] == "TR_TOOL_FAILED"


def test_call_tool_backend_down(monkeypatch):
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)

    import httpx

    def raise_conn(url, json_body):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tr_client, "_post", raise_conn)
    r = tr_client.call_tool("tr_find_paths", {"epp_path": "D:/a.epp"})
    assert r["success"] is False
    assert r["error_code"] == "TR_SERVICE_UNAVAILABLE"
    assert r.get("retryable") is True


def test_call_tool_http_409(monkeypatch):
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)

    monkeypatch.setattr(tr_client, "_post", lambda url, json_body: _resp(409, {"detail": "缺少确认"}))
    r = tr_client.call_tool("tr_sync_project_components", {"epp_path": "D:/a.epp", "operations": []})
    assert r["success"] is False
    assert "409" in r["message"]


# ---------------------------------------------------------------------------
# 工具 payload：参数映射 + 确认门注入
# ---------------------------------------------------------------------------

def test_tool_forwards_arguments(monkeypatch):
    from servers.tr_simulation import tools as tr_tools
    captured = {}

    def fake_call_tool(tool, arguments, *, requires_confirmation=False):
        captured["tool"] = tool
        captured["arguments"] = arguments
        captured["confirm"] = requires_confirmation
        return {"success": True}

    monkeypatch.setattr(tr_tools, "call_tool", fake_call_tool)

    tr_tools.tr_find_paths("D:/proj/a.epp")
    assert captured["tool"] == "tr_find_paths"
    assert captured["arguments"] == {"epp_path": "D:/proj/a.epp"}
    assert captured["confirm"] is False


def test_tool_modify_netlist_full_payload(monkeypatch):
    from servers.tr_simulation import tools as tr_tools
    captured = {}

    def fake_call_tool(tool, arguments, *, requires_confirmation=False):
        captured["arguments"] = arguments
        return {"success": True}

    monkeypatch.setattr(tr_tools, "call_tool", fake_call_tool)

    tr_tools.tr_modify_netlist(
        epp_path="D:/a.epp", source_netlist_path="C:/n.log", input_port=1, output_port=2,
        indicator="增益", band_name="", min_freq=8.0, max_freq=10.0, phase_step=0.0,
        atten_step=0.0, phase_devices_json="[]", atten_devices_json="[]", freq_rx=0.0,
        pwr_rx=-20.0, freq_pout=0.0, pwr_pout=-20.0, replacements_json="[]",
    )
    assert captured["arguments"]["input_port"] == 1
    assert captured["arguments"]["output_port"] == 2
    assert captured["arguments"]["min_freq"] == 8.0
    # 17 个参数全量透传
    assert len(captured["arguments"]) == 17


def test_confirm_tools_inject_confirmation(monkeypatch):
    from servers.tr_simulation import tools as tr_tools
    confirm_flags = []

    def fake_call_tool(tool, arguments, *, requires_confirmation=False):
        confirm_flags.append((tool, requires_confirmation))
        return {"success": True}

    monkeypatch.setattr(tr_tools, "call_tool", fake_call_tool)

    tr_tools.tr_restore_schematic("D:/a.epp")
    tr_tools.tr_sync_project_components("D:/a.epp", [])
    tr_tools.tr_modify_netlist(
        epp_path="D:/a.epp", source_netlist_path="C:/n.log", input_port=1, output_port=2,
        indicator="增益", band_name="", min_freq=8.0, max_freq=10.0, phase_step=0.0,
        atten_step=0.0, phase_devices_json="[]", atten_devices_json="[]", freq_rx=0.0,
        pwr_rx=-20.0, freq_pout=0.0, pwr_pout=-20.0, replacements_json="[]",
    )

    by_tool = dict(confirm_flags)
    assert by_tool["tr_restore_schematic"] is True
    assert by_tool["tr_sync_project_components"] is True
    assert by_tool["tr_modify_netlist"] is False


def test_tool_defaults_applied(monkeypatch):
    """有默认值的参数在省略时被填上（对齐上游 default）。"""
    from servers.tr_simulation import tools as tr_tools
    captured = {}

    def fake_call_tool(tool, arguments, *, requires_confirmation=False):
        captured["arguments"] = arguments
        return {"success": True}

    monkeypatch.setattr(tr_tools, "call_tool", fake_call_tool)

    tr_tools.tr_read_guide("网表同步")
    assert captured["arguments"] == {"guide_name": "网表同步", "offset": 0, "limit": 12000}

    tr_tools.tr_query_schematic_components("D:/a.epp")
    assert captured["arguments"] == {"epp_path": "D:/a.epp", "instance_name": ""}

    tr_tools.tr_get_workflow_state()
    assert captured["arguments"] == {
        "project_id": "", "input_port": 0, "output_port": 0,
        "indicator": "", "include_attempts": False, "band_name": "",
    }


def test_confirmation_payload_injected(monkeypatch):
    """确认门工具在 HTTP 外层注入 confirmation。"""
    from servers.tr_simulation import client as tr_client
    _patch_session(monkeypatch, tr_client)
    captured = {}

    def fake_post(url, json_body):
        captured["body"] = json_body
        return _resp(202, {"status": "queued", "operation_id": "op-confirm"})

    monkeypatch.setattr(tr_client, "_post", fake_post)
    monkeypatch.setattr(tr_client, "_get", lambda url: _resp(
        200, {"status": "completed", "operation_id": "op-confirm", "result": {"success": True}}))
    monkeypatch.setattr(tr_client, "_POLL_INTERVAL_SECONDS", 0.0)

    tr_client.call_tool("tr_restore_schematic", {"epp_path": "D:/a.epp"}, requires_confirmation=True)
    conf = captured["body"]["confirmation"]
    assert conf["confirmed_by_user"] is True
    assert conf["confirmation_id"]


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

def test_workflow_resource_returns_markdown(monkeypatch):
    from servers.tr_simulation import resource as res
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, timeout: _resp(200, payload=None, text="# TR 工作流\n规则..."))
    out = res.resource_tr_workflow()
    assert out.startswith("# TR 工作流")


def test_workflow_resource_backend_down(monkeypatch):
    from servers.tr_simulation import resource as res
    import httpx

    def raise_conn(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", raise_conn)
    out = res.resource_tr_workflow()
    assert "无法连接" in out


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def test_prompt_run_tr_simulation(monkeypatch):
    from servers.tr_simulation import prompts as tr_prompts
    monkeypatch.setattr(tr_prompts, "fetch_workflow", lambda: "# TR 工作流\n规则示例")
    msgs = tr_prompts.prompt_run_tr_simulation("D:/proj/a.epp")
    assert msgs[0]["role"] == "user"
    content = msgs[0]["content"]
    assert "D:/proj/a.epp" in content
    assert "# TR 工作流" in content
    assert "tr_get_simulation_capabilities" in content
    assert "tr_find_paths" in content


def test_prompt_run_tr_simulation_without_epp(monkeypatch):
    from servers.tr_simulation import prompts as tr_prompts
    monkeypatch.setattr(tr_prompts, "fetch_workflow", lambda: "workflow-body")
    msgs = tr_prompts.prompt_run_tr_simulation()
    assert "目标工程" not in msgs[0]["content"]


# ---------------------------------------------------------------------------
# 17 个工具齐全（tr_open_document 已移除，改用 open_document）
# ---------------------------------------------------------------------------

def test_17_tr_tools_registered():
    from start_servers import mcp
    tools = set(t.name for t in mcp._tool_manager._tools.values())
    expected = {
        "tr_get_workflow_state", "tr_set_workflow_plan", "tr_get_simulation_capabilities",
        "tr_read_netlist", "tr_find_paths", "tr_restore_schematic", "tr_modify_netlist",
        "tr_execute_simulation_plan", "tr_run_simulation", "tr_parse_raw", "tr_read_guide",
        "tr_get_project_netlist", "tr_query_schematic_components", "tr_sync_project_components",
        "tr_prepare_report", "tr_generate_document", "tr_query_components",
    }
    missing = expected - tools
    assert not missing, f"缺少 TR 工具: {sorted(missing)}"
    assert "tr_open_document" not in tools, "tr_open_document 应已移除（改用 open_document）"
