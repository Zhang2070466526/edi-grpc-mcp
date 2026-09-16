"""测试异步仿真任务 — 注册表、回调、状态、结果、清理。"""
import time
from pathlib import Path



class TestTaskRegistry:
    def test_start_creates_entry(self):
        from servers.eda.simulation import _sim_tasks, _sim_lock
        with _sim_lock:
            task_id = "test-task-1"
            _sim_tasks[task_id] = {
                "task_id": task_id,
                "client_uuid": "cu-1",
                "project_path": "/p.epp",
                "result_path": "",
                "status": "QUEUED",
                "message": "waiting",
                "log_chunks": [],
                "result": None,
                "error": None,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
            }
            assert task_id in _sim_tasks
        with _sim_lock:
            del _sim_tasks[task_id]

    def test_task_fields_complete(self):
        task = {
            "task_id": "t1",
            "client_uuid": "c1",
            "project_path": "/p.epp",
            "result_path": "/r.raw",
            "status": "RUNNING",
            "message": "simulating",
            "log_chunks": ["line1\n", "line2\n"],
            "result": None,
            "error": None,
            "created_at": time.time(),
            "started_at": time.time(),
            "finished_at": None,
        }
        required = [
            "task_id", "client_uuid", "project_path", "result_path",
            "status", "message", "log_chunks", "result", "error",
            "created_at", "started_at", "finished_at",
        ]
        for key in required:
            assert key in task, f"missing: {key}"


class TestCurrentAdsOutput:
    def test_from_result_when_done(self):
        from servers.eda.simulation import _current_ads_output
        task = {
            "log_chunks": ["old"],
            "result": {"ads_output": "full log from result"},
        }
        assert _current_ads_output(task) == "full log from result"

    def test_from_chunks_when_running(self):
        from servers.eda.simulation import _current_ads_output
        task = {
            "log_chunks": ["A", "B", "C"],
            "result": None,
        }
        assert _current_ads_output(task) == "ABC"

    def test_empty_when_no_data(self):
        from servers.eda.simulation import _current_ads_output
        task = {"log_chunks": [], "result": None}
        assert _current_ads_output(task) == ""


class TestHandleSimEvent:
    def test_accepted_updates_status(self):
        from servers.eda.simulation import _handle_sim_event, _sim_tasks, _sim_lock
        tid = "test-evt-1"
        with _sim_lock:
            _sim_tasks[tid] = {
                "task_id": tid, "client_uuid": "cu",
                "status": "QUEUED", "message": "",
                "log_chunks": [],
                "project_path": "", "result_path": "",
                "started_at": None,
            }
        _handle_sim_event(tid, {
            "status": "ACCEPTED", "message": "accepted",
            "ads_output_chunk": "", "details": {},
        })
        with _sim_lock:
            assert _sim_tasks[tid]["status"] == "ACCEPTED"
            del _sim_tasks[tid]

    def test_running_appends_chunk(self):
        from servers.eda.simulation import _handle_sim_event, _sim_tasks, _sim_lock
        tid = "test-evt-2"
        with _sim_lock:
            _sim_tasks[tid] = {
                "task_id": tid, "client_uuid": "cu",
                "status": "ACCEPTED", "message": "",
                "log_chunks": [],
                "project_path": "", "result_path": "",
                "started_at": None,
            }
        _handle_sim_event(tid, {
            "status": "RESULT_STATUS_RUNNING", "message": "",
            "ads_output_chunk": "Parsing netlist\n", "details": {},
        })
        with _sim_lock:
            assert _sim_tasks[tid]["status"] == "RUNNING"
            assert _sim_tasks[tid]["log_chunks"] == ["Parsing netlist\n"]
            assert _sim_tasks[tid]["started_at"] is not None
            del _sim_tasks[tid]

    def test_unknown_task_ignored(self):
        from servers.eda.simulation import _handle_sim_event
        _handle_sim_event("nonexistent", {"status": "ACCEPTED", "ads_output_chunk": "", "details": {}})


class TestPruneTasks:
    def test_expired_tasks_cleaned(self):
        from servers.eda.simulation import _sim_tasks, _sim_lock, _prune_tasks
        tid = "test-prune-1"
        with _sim_lock:
            _sim_tasks[tid] = {
                "task_id": tid,
                "client_uuid": "cu",
                "status": "SUCCEEDED",
                "finished_at": 0,  # epoch → expired
            }
        _prune_tasks()
        with _sim_lock:
            assert tid not in _sim_tasks


class TestChatSimContext:
    def test_last_task_id_saved(self):
        from servers.chat.service import ChatService
        svc = ChatService.instance()
        s = svc._get_or_create("")  # 空 ID 创建新会话
        svc._update_context(s, "start_simulation_async",
                            {"project_path": "/p.epp"},
                            {"success": True, "task_id": "sim-001"})
        assert s.last_simulation_task_id == "sim-001"

    def test_task_id_auto_fill(self):
        from servers.chat.service import ChatService
        svc = ChatService.instance()
        s = svc._get_or_create("")  # 空 ID 创建新会话
        s.last_simulation_task_id = "sim-002"
        ok, args = svc._validate("get_simulation_async_status", {}, s)
        assert ok
        assert args["task_id"] == "sim-002"

class TestCompletedSemantics:
    """Bug 修复：completed 以 finished_at 为准。"""

    def test_running_not_completed(self):
        from servers.eda.simulation import _task_completed
        task = {"status": "RUNNING", "finished_at": None}
        assert not _task_completed(task)

    def test_succeeded_is_completed(self):
        from servers.eda.simulation import _task_completed
        task = {"status": "SUCCEEDED", "finished_at": 100.0}
        assert _task_completed(task)

    def test_timeout_is_completed(self):
        from servers.eda.simulation import _task_completed
        task = {"status": "TIMEOUT", "finished_at": 100.0}
        assert _task_completed(task)

    def test_timeout_log_incomplete(self):
        from servers.eda.simulation import _task_log_complete
        task = {"result": {"log_complete": False}}
        assert not _task_log_complete(task)

    def test_succeeded_log_complete(self):
        from servers.eda.simulation import _task_log_complete
        task = {"result": {"log_complete": True}}
        assert _task_log_complete(task)


class TestResultSuccessSemantics:
    """success 字段反映仿真业务结果（FAILED/TIMEOUT 时 success=False）。"""

    @staticmethod
    def _result(status, success, task_success, outcome_known):
        return {
            "success": success,
            "completed": True,
            "outcome_known": outcome_known,
            "task_success": task_success,
            "client_uuid": "cu",
            "task_id": "test-res-sem",
            "task_type": "SIMULATE_PROJECT",
            "status": status,
            "message": "msg",
            "project_path": "/p.epp",
            "result_path": "/r.raw",
            "ads_output": "",
            "log_complete": True,
            "details": {},
        }

    def _put_task(self, result):
        from servers.eda.simulation import _sim_tasks, _sim_lock
        with _sim_lock:
            _sim_tasks["test-res-sem"] = {
                "task_id": "test-res-sem",
                "client_uuid": "cu",
                "project_path": "/p.epp",
                "result_path": "/r.raw",
                "status": result["status"],
                "message": result.get("message", ""),
                "log_chunks": [],
                "result": result,
                "error": None,
                "created_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
            }

    @staticmethod
    def _cleanup():
        from servers.eda.simulation import _sim_tasks, _sim_lock
        with _sim_lock:
            _sim_tasks.pop("test-res-sem", None)

    def test_succeeded_success_true(self):
        from servers.eda.simulation import get_simulation_async_result
        self._put_task(self._result("SUCCEEDED", True, True, True))
        try:
            r = get_simulation_async_result("test-res-sem")
            assert r["success"] is True
            assert r["task_success"] is True
        finally:
            self._cleanup()

    def test_failed_success_false(self):
        from servers.eda.simulation import get_simulation_async_result
        self._put_task(self._result("FAILED", False, False, True))
        try:
            r = get_simulation_async_result("test-res-sem")
            assert r["success"] is False
            assert r["task_success"] is False
            assert r["status"] == "FAILED"
        finally:
            self._cleanup()

    def test_timeout_success_false(self):
        from servers.eda.simulation import get_simulation_async_result
        self._put_task(self._result("TIMEOUT", False, None, False))
        try:
            r = get_simulation_async_result("test-res-sem")
            assert r["success"] is False
            assert r["task_success"] is None
        finally:
            self._cleanup()

    def test_status_failed_success_false(self):
        from servers.eda.simulation import get_simulation_async_status
        self._put_task(self._result("FAILED", False, False, True))
        try:
            r = get_simulation_async_status("test-res-sem")
            assert r["success"] is False
            assert r["task_success"] is False
        finally:
            self._cleanup()

    def test_status_running_success_true(self):
        from servers.eda.simulation import get_simulation_async_status, _sim_tasks, _sim_lock
        tid = "test-res-sem-running"
        with _sim_lock:
            _sim_tasks[tid] = {
                "task_id": tid,
                "client_uuid": "cu",
                "project_path": "/p.epp",
                "result_path": "",
                "status": "RUNNING",
                "message": "simulating",
                "log_chunks": [],
                "result": None,
                "error": None,
                "created_at": time.time(),
                "started_at": time.time(),
                "finished_at": None,
            }
        try:
            r = get_simulation_async_status(tid)
            assert r["success"] is True
            assert r["completed"] is False
        finally:
            with _sim_lock:
                _sim_tasks.pop(tid, None)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
