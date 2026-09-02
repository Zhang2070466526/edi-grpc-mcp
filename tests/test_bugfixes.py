"""针对代码审查发现的中危 bug 修复的回归测试。

覆盖：
  1. start_simulation_async 超时范围校验
  2. update_simulation_component 丢弃查找错误（空实例名 / 同名歧义）
  3. launch_edi(wait_for_grpc=False) 成功语义
  4. _read_curve_csv_xy 的 x/y 列表错位
  5. compare interpolation 对所有文件校验单调
  6. close_hfss_project 关闭活动项目
  7. HFSS/CST runner 运行超时兜底
  8. Chat _prune 跳过持锁会话 + /upload 大小限制
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── 1. 异步仿真超时校验 ──────────────────────────────────────────

def test_start_simulation_async_rejects_invalid_timeout(monkeypatch):
    from servers.eda import simulation as sim
    monkeypatch.setattr(sim, "validate_project_path", lambda p: p)
    for bad in (0, -5, 3601, 99999):
        r = sim.start_simulation_async("C:/test.epp", timeout_seconds=bad)
        assert r["success"] is False, f"timeout_seconds={bad} 应被拒绝"
        assert r["error_code"] == "INVALID_PARAMETERS"


# ── 2. update 器件查找错误 ────────────────────────────────────────

def test_update_rejects_empty_instance_name(monkeypatch):
    from servers.eda import simulation_components as sc
    monkeypatch.setattr(sc, "validate_project_path", lambda p: p)
    r = sc.update_simulation_component(
        "C:/test.epp", "   ", {"Freq": {"value": "1", "unit": "GHz"}})
    assert r["success"] is False
    assert r["error_code"] == "EMPTY_INSTANCE_NAME"


def test_update_rejects_ambiguous_instance_name(monkeypatch):
    from servers.eda import simulation_components as sc
    from servers.utils import tool_error
    monkeypatch.setattr(sc, "validate_project_path", lambda p: p)
    monkeypatch.setattr(sc, "_find_component_by_instance",
                        lambda p, n: (None, tool_error("AMBIGUOUS_INSTANCE_NAME", "发现多个同名器件")))
    r = sc.update_simulation_component(
        "C:/test.epp", "R1", {"Freq": {"value": "1", "unit": "GHz"}})
    assert r["success"] is False
    assert r["error_code"] == "AMBIGUOUS_INSTANCE_NAME"


# ── 3. launch_edi 成功语义 ───────────────────────────────────────

def test_launch_edi_no_wait_returns_success(tmp_path, monkeypatch):
    from servers.eda import edi_launcher
    exe = tmp_path / "EDI.exe"
    exe.write_text("")
    monkeypatch.setattr(edi_launcher, "EDI_PATH", str(exe))

    def _conn_fail(*a, **k):
        raise OSError("not running")

    monkeypatch.setattr(edi_launcher.socket, "create_connection", _conn_fail)
    monkeypatch.setattr(edi_launcher.subprocess, "Popen", lambda *a, **k: None)

    r = edi_launcher.launch_edi(wait_for_grpc=False)
    assert r["success"] is True
    assert r["process_started"] is True
    assert r["grpc_ready"] is False


# ── 4. CSV 解析 x/y 错位 ─────────────────────────────────────────

def test_read_curve_csv_xy_skips_bad_y_row(tmp_path):
    from servers.turbocharts import compare_results as cr
    csv = tmp_path / "c.csv"
    csv.write_text("x,y\n1.0,10.0\n2.0,bad\n3.0,30.0\n", encoding="utf-8")
    xv, yv = cr._read_curve_csv_xy(str(csv))
    assert len(xv) == len(yv) == 2, "x/y 列表必须长度一致"
    assert xv == [1.0, 3.0]
    assert yv == [10.0, 30.0]


# ── 5. 插值模式校验所有文件单调 ───────────────────────────────────

def test_compare_interpolation_checks_all_files(tmp_path, monkeypatch):
    from servers.turbocharts import compare_results as cr
    tc = tmp_path / "tc.exe"
    tc.write_text("")
    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", str(tc))
    raw_a = tmp_path / "a.raw"
    raw_b = tmp_path / "b.raw"
    raw_a.write_text("")
    raw_b.write_text("")
    monkeypatch.setattr(cr, "run_turbocharts",
                        lambda cmd, timeout_seconds=60: SimpleNamespace(returncode=0, stderr=""))

    def _read(path):
        # 参考文件（a）单调递增，非参考文件（b）降序
        if "a.raw" in str(path):
            return ([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
        return ([3.0, 2.0, 1.0], [30.0, 20.0, 10.0])

    monkeypatch.setattr(cr, "_read_curve_csv_xy", _read)
    img = tmp_path / "cmp.png"
    result = cr.compare_simulation_results(
        result_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]", img_path=str(img),
        alignment="interpolation", reference_index=0)
    assert result["success"] is False
    assert result["error_code"] == "INVALID_RAW_DATA"


# ── 6. close_hfss_project 关闭活动项目 ───────────────────────────

def test_close_hfss_project_uses_active_project():
    from servers.ansys import project_manage
    desktop = MagicMock()
    desktop.GetProjectList.side_effect = [["ProjectA", "ProjectB"], ["ProjectA"]]
    active = MagicMock()
    active.GetName.return_value = "ProjectB"
    desktop.GetActiveProject.return_value = active
    with patch("servers.ansys.project_manage.aedt_is_running", return_value=True), \
         patch("servers.ansys.project_manage._attach_aedt", return_value=(None, desktop)), \
         patch("servers.ansys.project_manage.pythoncom.CoInitialize"), \
         patch("servers.ansys.project_manage.pythoncom.CoUninitialize"):
        r = project_manage.close_hfss_project()
    assert r["success"] is True
    desktop.CloseProject.assert_called_once_with("ProjectB")


# ── 7. HFSS/CST runner 运行超时兜底 ───────────────────────────────

def test_hfss_cst_runners_have_run_timeout():
    from servers.ansys.run_analysis import hfss_runner
    from servers.cst.cst_api import cst_runner
    assert hfss_runner._max_run_seconds == 7200
    assert cst_runner._max_run_seconds == 7200


# ── 8a. Chat _prune 跳过持锁会话 ─────────────────────────────────

def test_prune_skips_locked_sessions():
    from servers.chat.service import ChatService
    cs = ChatService()
    cs._sessions.clear()
    cs._last_prune = 0
    now = time.time()

    # s0 持锁且最旧（远超 TTL，但持锁应被 TTL 分支跳过、也不被超上限分支误删）
    s0 = MagicMock()
    s0.updated_at = now - 100000
    s0.chat_lock = MagicMock()
    s0.chat_lock.locked.return_value = True
    cs._sessions["s0"] = s0
    for i in range(1, 101):
        s = MagicMock()
        s.updated_at = now
        s.chat_lock = MagicMock()
        s.chat_lock.locked.return_value = False
        cs._sessions[f"s{i}"] = s

    cs._prune()

    assert "s0" in cs._sessions, "持锁的活跃会话不应被淘汰"
    assert len(cs._sessions) == 100


# ── 8b. /upload 大小限制 ─────────────────────────────────────────

def test_upload_rejects_oversized_file(monkeypatch):
    from servers.chat import routes
    monkeypatch.setattr(routes, "_MAX_UPLOAD_BYTES", 10)

    class _FakeFile:
        def __init__(self, data):
            self._data = data
            self._pos = 0

        def read(self, n=-1):
            if self._pos >= len(self._data):
                return b""
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

    class _FakeUploaded:
        filename = "test.bin"
        file = _FakeFile(b"x" * 20)

    class _FakeForm:
        def get(self, name):
            return _FakeUploaded() if name == "file" else None

    class _FakeRequest:
        async def form(self):
            return _FakeForm()

    result = asyncio.run(routes.upload_file(_FakeRequest()))
    assert result.status_code == 413


# ── 低危修复回归 ────────────────────────────────────────────────

def test_register_document_url_rejects_invalid_path(tmp_path):
    from servers.multimodal_vision import document
    with pytest.raises(FileNotFoundError):
        document.register_document_url(str(tmp_path / "nope.pdf"))
    with pytest.raises(PermissionError):
        document.register_document_url("\\\\server\\share\\file.pdf")


def test_vswr_split_removes_consecutive_ampersand_and_passes_ac(tmp_path, monkeypatch):
    from servers.turbocharts import convert_raw as cr
    raw = tmp_path / "a.raw"
    raw.write_text("")
    monkeypatch.setattr(cr, "validate_file", lambda p, *a, **k: p)
    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", "tc.exe")
    commands = []

    def _run(cmd, timeout_seconds=60):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cr, "run_turbocharts", _run)
    cr.turbocharts_convert(
        raw_path=str(raw), img_path=str(tmp_path / "out.png"), chart_type="SP",
        csv_path=str(tmp_path / "out.csv"),
        linename="DB_S[2,1]&VSWR_S[1,1]&VSWR_S[2,2]&DB_S[1,2]",
        ac_config="phase#3#S[2,1]#fv#0.1")

    non_vswr_cmd = None
    for cmd in commands:
        if "--linename" in cmd:
            ln = cmd[cmd.index("--linename") + 1]
            if ln == "DB_S[2,1]&DB_S[1,2]":
                non_vswr_cmd = cmd
                break
    assert non_vswr_cmd is not None, "非 VSWR 导出应无连续 && 残留"
    assert "--ac" in non_vswr_cmd
    assert "phase#3#S[2,1]#fv#0.1" in non_vswr_cmd


def test_export_snp_rejects_invalid_port_count():
    from servers.cst import result_export as re
    exporter = re.CstResultExporter.__new__(re.CstResultExporter)
    exporter._s_param_indices = lambda item: (1, 1)
    result_3d = MagicMock()
    result_3d.get_tree_items.return_value = ["S-Parameters S[1,1]"]
    with pytest.raises(RuntimeError):
        exporter._export_snp_from_3d("C:/m.cst", "", 0, result_3d)


def test_tool_args_summary_redacts_forward_slash_path():
    from servers.chat.service import _tool_args_summary
    result = _tool_args_summary("open_edi_project", {"project_path": "C:/Users/John/test.epp"})
    assert "test.epp" in result
    assert "John" not in result


def test_async_result_success_means_query_success(monkeypatch):
    from servers.eda import simulation as sim
    failed_result = {
        "success": False, "completed": True, "outcome_known": True,
        "task_success": False, "status": "FAILED", "message": "failed",
        "project_path": "C:/test.epp", "result_path": "",
        "ads_output": "", "log_complete": True,
    }
    task = {
        "task_id": "abc", "client_uuid": "uuid", "status": "FAILED",
        "message": "failed", "project_path": "C:/test.epp",
        "result_path": "", "result": failed_result,
        "log_chunks": [], "created_at": 0.0, "started_at": 0.0, "finished_at": 0.0,
    }
    monkeypatch.setattr(sim, "_get_task_snapshot", lambda tid, *, prune=False: task)
    r = sim.get_simulation_async_result("abc")
    assert r["success"] is True
    assert r["task_success"] is False
    assert r["status"] == "FAILED"


def test_compare_rejects_missing_output_dir(tmp_path, monkeypatch):
    from servers.turbocharts import compare_results as cr
    tc = tmp_path / "tc.exe"
    tc.write_text("")
    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", str(tc))
    raw_a = tmp_path / "a.raw"
    raw_b = tmp_path / "b.raw"
    raw_a.write_text("")
    raw_b.write_text("")
    img = tmp_path / "nonexistent" / "cmp.png"  # 父目录不存在
    result = cr.compare_simulation_results(
        result_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]", img_path=str(img))
    assert result["success"] is False
    assert result["error_code"] == "OUTPUT_DIRECTORY_NOT_FOUND"


def test_analyze_image_max_tokens_non_int(monkeypatch):
    from servers.multimodal_vision import vision_analyzer as va
    monkeypatch.setattr(va, "VISION_API_KEY", "k")
    monkeypatch.setattr(va, "VISION_BASE_URL", "http://x")
    monkeypatch.setattr(va, "VISION_MODEL", "m")
    monkeypatch.setattr(va, "_validate",
                        lambda p: (None, {"success": False, "error_code": "IMAGE_NOT_FOUND", "message": "x"}))
    r = va.analyze_image("C:/x.png", max_tokens="abc")
    assert r["success"] is False
    assert r["error_code"] == "IMAGE_NOT_FOUND"


# ── GET_COMPONENTS_STATIC_PARAMS 新工具 ─────────────────────────

def test_get_components_static_params_validation(monkeypatch):
    from servers.eda import project_manage as pm
    # 同时提供 original_uuids 与 original_uuid → 拒绝
    r = pm.get_components_static_params(original_uuids=["u1"], original_uuid="u2")
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
    # 都不提供 → 拒绝
    r = pm.get_components_static_params()
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
    # 空字符串数组元素 → 拒绝
    r = pm.get_components_static_params(original_uuids=["  "])
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
    # 空数组 → 拒绝，报错信息精确
    r = pm.get_components_static_params(original_uuids=[])
    assert r["success"] is False and r["error_code"] == "INVALID_PARAMETERS"
    assert "空数组" in r["message"]


def test_get_components_static_params_payload(monkeypatch):
    from servers.eda import project_manage as pm
    from proto import ecserver_pb2
    calls = []

    def _fake(task_type, payload, timeout, max_timeout_seconds=300):
        calls.append((task_type, payload))
        return {"success": True, "status": "SUCCEEDED"}

    monkeypatch.setattr(pm, "call_grpc", _fake)
    # 批量查询
    pm.get_components_static_params(original_uuids=["u1", "u2"])
    assert calls[-1][0] == ecserver_pb2.GET_COMPONENTS_STATIC_PARAMS
    assert calls[-1][1] == {"original_uuids": ["u1", "u2"]}
    # 单条查询
    pm.get_components_static_params(original_uuid="u3")
    assert calls[-1][0] == ecserver_pb2.GET_COMPONENTS_STATIC_PARAMS
    assert calls[-1][1] == {"original_uuid": "u3"}


# ── 端口占用自动清理 ────────────────────────────────────────────

def test_find_port_pid(monkeypatch):
    import psutil
    import start_servers as ss
    fake = SimpleNamespace(
        status=psutil.CONN_LISTEN,
        laddr=SimpleNamespace(port=50026),
        pid=1234,
    )
    monkeypatch.setattr(psutil, "net_connections", lambda kind: [fake])
    assert ss._find_port_pid(50026) == 1234
    monkeypatch.setattr(psutil, "net_connections", lambda kind: [])
    assert ss._find_port_pid(50026) is None


def test_kill_port_process(monkeypatch):
    import psutil
    import start_servers as ss
    monkeypatch.setattr(ss, "_find_port_pid", lambda port: 1234)
    fake_proc = MagicMock()
    fake_proc.name.return_value = "python.exe"
    monkeypatch.setattr(psutil, "Process", lambda pid: fake_proc)
    assert ss._kill_port_process(50026) is True
    fake_proc.terminate.assert_called_once()
    fake_proc.wait.assert_called()
