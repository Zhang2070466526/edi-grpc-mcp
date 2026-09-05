"""测试 Resources 和 Prompts 的功能正确性。

包括：
  - 直接调用验证（返回值结构、字段完整性、安全约束）
  - MCP协议快速冒烟测试（list/read/get 基本可用）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


# ═══════════════════════════════════════════════════════════
# 1. 直接调用测试 — 覆盖 Resources 与 Prompts 的返回值结构/字段/安全约束
# ═══════════════════════════════════════════════════════════

class TestResourcesDirect:
    def test_service_overview_structure(self):
        from servers.resources_prompts.resources_service import resource_service_overview
        data = resource_service_overview()
        assert data["server_name"] == "EDI gRPC MCP"
        assert data["protocol_version"] == "2"
        assert data.get("tool_api_version") == "3"
        assert "server_version" in data
        for ct in ["SParameter", "HarmonicBalance", "XDB"]:
            assert ct in data["simulation_components"]
        # 不应泄露敏感信息
        text = json.dumps(data)
        assert "sk-" not in text.lower()
        assert "API_KEY" not in text
        assert "OPENCLAW_WORKSPACE" not in text

    def test_simulation_components_matches_catalog(self):
        from servers.resources_prompts.resources_reference import resource_simulation_components
        from servers.eda.simulation_components import _load_catalog
        data = resource_simulation_components()
        cat = _load_catalog()
        assert data == cat
        assert data["schema_version"] == "2.0.0"

    def test_operation_guide_has_key_rules(self):
        from servers.resources_prompts.resources_reference import resource_operation_guide
        text = resource_operation_guide()
        assert isinstance(text, str)
        for keyword in ["TIMEOUT", "STREAM_DISCONNECTED",
                        "clear_before_import", "confirm_clear",
                        "show_image"]:
            assert keyword in text, f"Missing keyword: {keyword}"

    def test_projects_resource_uses_current_workspace(self, monkeypatch, tmp_path):
        """edi://projects 应通过 GET_CURRENT_WORKSPACE 获取工作区，而非本地猜测。"""
        from servers.resources_prompts.resources_service import resource_projects_directory
        ws = tmp_path / "EDI-Workspace"
        (ws / "projects").mkdir(parents=True)
        monkeypatch.setattr(
            "servers.resources_prompts.resources_service.get_current_workspace",
            lambda: {"success": True, "details": {"workspace_path": str(ws)}},
        )
        monkeypatch.setattr(
            "servers.resources_prompts.resources_service.list_epp_projects",
            lambda folder: {"success": True, "count": 1, "projects": [
                {"name": "demo", "path": str(ws / "projects" / "demo" / "demo.epp"), "size": 0}
            ]},
        )
        r = resource_projects_directory()
        assert r["workspace"] == str(ws)
        assert r["projects_dir"] == str(ws / "projects")
        assert r["count"] == 1
        assert r["projects"][0]["name"] == "demo"

    def test_projects_resource_no_workspace(self, monkeypatch):
        """GET_CURRENT_WORKSPACE 失败时返回空清单 + warning，不猜测目录。"""
        from servers.resources_prompts.resources_service import resource_projects_directory
        monkeypatch.setattr(
            "servers.resources_prompts.resources_service.get_current_workspace",
            lambda: {"success": False, "status": "GRPC_UNAVAILABLE"},
        )
        r = resource_projects_directory()
        assert r["workspace"] == ""
        assert r["count"] == 0
        assert "warning" in r


class TestPromptsDirect:
    def test_inspect_edi_project(self):
        from servers.resources_prompts.prompts_project import prompt_inspect_edi_project
        msgs = prompt_inspect_edi_project("C:/test.epp", "full")
        assert msgs[0]["role"] == "user"
        assert "get_project_summary" in msgs[0]["content"]
        assert "list_simulation_components" in msgs[0]["content"]
        assert "analyze_variables" in msgs[0]["content"]

    def test_run_and_review_async(self):
        from servers.resources_prompts.prompts_simulation import prompt_run_and_review_simulation
        msgs = prompt_run_and_review_simulation("C:/test.epp")
        assert "start_simulation_async" in msgs[0]["content"]
        assert "禁止自动重试" in msgs[0]["content"]
        assert "ads_output" in msgs[0]["content"]

    def test_run_and_review_sync(self):
        from servers.resources_prompts.prompts_simulation import prompt_run_and_review_simulation
        msgs = prompt_run_and_review_simulation(
            "C:/test.epp", execution_mode="sync", analyze_log=False)
        assert "simulate_project" in msgs[0]["content"]
        assert "start_simulation_async" not in msgs[0]["content"]

    def test_configure_create(self):
        from servers.resources_prompts.prompts_component import prompt_configure_simulation_component
        msgs = prompt_configure_simulation_component(
            "C:/test.epp", "create", "XDB", requirements="默认参数")
        text = msgs[0]["content"]
        assert "get_simulation_component_schema" in text
        assert "create_simulation_component" in text
        assert "不要编造参数名" in text
        assert "XDB" in text

    def test_configure_update(self):
        from servers.resources_prompts.prompts_component import prompt_configure_simulation_component
        msgs = prompt_configure_simulation_component(
            "C:/test.epp", "update", "HarmonicBalance",
            instance_name="HB1", requirements="基频改为 2GHz")
        text = msgs[0]["content"]
        assert "list_simulation_components" in text
        assert "update_simulation_component" in text
        assert "HB1" in text
        assert "HarmonicBalance" in text

    def test_configure_bad_action_rejected(self):
        """无效 action 必须拒绝，不能静默回退。"""
        from servers.resources_prompts.prompts_component import prompt_configure_simulation_component
        msgs = prompt_configure_simulation_component(
            "C:/test.epp", "nonexistent", "")
        assert "错误" in msgs[0]["content"]
        assert "nonexistent" in msgs[0]["content"]

    def test_assess_anti_burnout(self):
        from servers.resources_prompts.prompts_simulation import prompt_assess_anti_burnout
        msgs = prompt_assess_anti_burnout("C:/test.epp")
        text = msgs[0]["content"]
        assert "simulate_anti_burnout" in text
        assert "裕量" in text
        assert "max_input_power" in text

    def test_select_component(self):
        from servers.resources_prompts.prompts_component import prompt_select_component
        msgs = prompt_select_component("61", "低噪声放大器")
        text = msgs[0]["content"]
        assert "search_public_models" in text
        assert "search_personal_models" in text
        assert "replace_models_from_csv" in text
        assert "依次" in text
        assert "model_uuid" in text

    def test_analyze_signal_chain(self):
        from servers.resources_prompts.prompts_project import prompt_analyze_signal_chain
        msgs = prompt_analyze_signal_chain("C:/test.epp")
        text = msgs[0]["content"]
        assert "get_signal_chain" in text
        assert "export_project_netlist" in text


# ═══════════════════════════════════════════════════════════
# 2. MCP 协议快速冒烟测试
# ═══════════════════════════════════════════════════════════

import queue
import threading


def _drain_stderr(proc):
    """消费 stderr 避免管道写满阻塞主进程。"""
    try:
        while True:
            chunk = proc.stderr.read(4096)
            if not chunk:
                break
    except Exception:
        pass


def _jsonrpc(proc, method, params=None, timeout_s=10):
    """发送 JSON-RPC 请求并等待匹配的响应（非阻塞读取）。"""
    req_id = abs(hash(method)) % 10000
    req = json.dumps({
        "jsonrpc": "2.0", "id": req_id,
        "method": method, "params": params or {},
    }) + "\n"
    proc.stdin.write(req)
    proc.stdin.flush()

    result_queue: queue.Queue = queue.Queue()

    def reader():
        try:
            while True:
                line = proc.stdout.readline()
                if not line or not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if data.get("id") == req_id:
                        result_queue.put(data)
                        return
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        except Exception:
            result_queue.put({"error": "reader crashed"})

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        return result_queue.get(timeout=timeout_s)
    except queue.Empty:
        return {"error": f"timeout: {method}"}
    finally:
        t.join(timeout=1)


def _init_server():
    """启动并初始化一个 MCP stdio 服务进程。"""
    root = Path(__file__).parent.parent
    proc = subprocess.Popen(
        [sys.executable, str(root / "start_servers.py"), "--transport", "stdio"],
        cwd=str(root),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    # Drain stderr in background
    threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()

    resp = _jsonrpc(proc, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0.1"},
    })
    assert "result" in resp, f"initialize failed: {resp}"
    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()
    time.sleep(0.3)
    return proc


def _stop_server(proc):
    """安全停止服务进程。"""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class TestMcpProtocolSmoke:
    """冒烟测试：确认 MCP 协议层面的 list/read/get 可用。"""

    def test_resources_list(self):
        proc = _init_server()
        try:
            resp = _jsonrpc(proc, "resources/list")
            assert "result" in resp, f"resources/list failed: {resp}"
            uris = [r["uri"] for r in resp["result"]["resources"]]
            assert "edi://service/overview" in uris
            assert "edi://reference/simulation-components" in uris
            assert "edi://reference/operation-guide" in uris
        finally:
            _stop_server(proc)

    def test_resources_read(self):
        proc = _init_server()
        try:
            resp = _jsonrpc(proc, "resources/read",
                            {"uri": "edi://service/overview"})
            assert "result" in resp
            data = json.loads(resp["result"]["contents"][0]["text"])
            assert data["protocol_version"] == "2"
            assert "sk-" not in json.dumps(data).lower()
        finally:
            _stop_server(proc)

    def test_prompts_list(self):
        proc = _init_server()
        try:
            resp = _jsonrpc(proc, "prompts/list")
            assert "result" in resp, f"prompts/list failed: {resp}"
            names = [p["name"] for p in resp["result"]["prompts"]]
            assert "inspect_edi_project" in names
            assert "run_and_review_simulation" in names
            assert "configure_simulation_component" in names
            assert "create_simulation_report" in names
        finally:
            _stop_server(proc)

    def test_prompts_get(self):
        proc = _init_server()
        try:
            resp = _jsonrpc(proc, "prompts/get", {
                "name": "inspect_edi_project",
                "arguments": {"project_path": "C:/test.epp", "detail_level": "full"},
            })
            assert "result" in resp, f"prompts/get failed: {resp}"
            text = resp["result"]["messages"][0]["content"]["text"]
            assert "get_project_summary" in text
        finally:
            _stop_server(proc)


# ═══════════════════════════════════════════════════════════
# 3. get_model_category_params 的 categories_only 裁剪
# ═══════════════════════════════════════════════════════════

class TestModelSearchCategoriesOnly:
    """测试 get_model_category_params 的 categories_only 裁剪逻辑。"""

    def test_categories_only_strips_params(self, monkeypatch):
        from servers.eda import model_library as ms
        result = {
            "success": True,
            "details": {"data": [
                {"id": "61", "name": "低噪放", "params": [{"p": 1}, {"p": 2}]},
                {"id": "62", "name": "功放", "params": [{"p": 3}]},
            ]},
        }
        monkeypatch.setattr(ms, "call_grpc", lambda *a, **k: result)
        r = ms.get_model_category_params(categories_only=True)
        data = r["details"]["data"]
        assert set(data[0].keys()) == {"id", "name"}
        assert "params" not in data[0]

    def test_categories_only_off_returns_full(self, monkeypatch):
        from servers.eda import model_library as ms
        result = {
            "success": True,
            "details": {"data": [
                {"id": "61", "name": "低噪放", "params": [{"p": 1}]},
            ]},
        }
        monkeypatch.setattr(ms, "call_grpc", lambda *a, **k: result)
        r = ms.get_model_category_params()  # 默认 categories_only=False
        assert "params" in r["details"]["data"][0]
