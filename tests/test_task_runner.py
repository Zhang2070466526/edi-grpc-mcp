"""测试通用异步任务队列 TaskRunner —— 生命周期、结果/异常写回、队列满、清理。

TaskRunner 是 CST 求解 / 远场导出（后续 EDA / HFSS 也会迁移）共用的基础组件，
这里用内存线程池验证其状态机与边界行为，不依赖任何外部软件。
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from servers.task_runner import TaskRunner


def _wait_until_finished(runner: TaskRunner, task_id: str, timeout: float = 5.0) -> dict:
    """轮询直到任务结束（finished_at 非空），返回最终快照。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = runner.snapshot(task_id)
        if snap is not None and snap.get("finished_at") is not None:
            return snap
        time.sleep(0.01)
    return runner.snapshot(task_id)


class TestLifecycle:
    def test_submit_returns_task_id(self):
        runner = TaskRunner(name_prefix="test")
        task_id = runner.submit(lambda: "done")
        assert isinstance(task_id, str) and task_id
        snap = _wait_until_finished(runner, task_id)
        assert snap["status"] == "SUCCEEDED"

    def test_success_writes_result(self):
        runner = TaskRunner(name_prefix="test")
        task_id = runner.submit(lambda: "ok-result")
        snap = _wait_until_finished(runner, task_id)
        assert snap["status"] == "SUCCEEDED"
        assert snap["result"] == "ok-result"
        assert snap["error"] is None

    def test_exception_writes_error(self):
        runner = TaskRunner(name_prefix="test")

        def boom():
            raise RuntimeError("boom")

        task_id = runner.submit(boom)
        snap = _wait_until_finished(runner, task_id)
        assert snap["status"] == "FAILED"
        assert "boom" in snap["error"]
        assert snap["result"] is None

    def test_args_passed_to_func(self):
        runner = TaskRunner(name_prefix="test")
        task_id = runner.submit(lambda a, b: a + b, 2, 3)
        snap = _wait_until_finished(runner, task_id)
        assert snap["result"] == 5


class TestSnapshot:
    def test_missing_returns_none(self):
        runner = TaskRunner(name_prefix="test")
        assert runner.snapshot("nonexistent") is None

    def test_running_has_started_at(self):
        runner = TaskRunner(name_prefix="test")
        release = threading.Event()

        def slow():
            release.wait(timeout=5)
            return "done"

        task_id = runner.submit(slow)
        try:
            snap = runner.snapshot(task_id)
            assert snap["status"] in ("QUEUED", "RUNNING")
        finally:
            release.set()
            _wait_until_finished(runner, task_id)


class TestQueueLimit:
    def test_full_returns_none(self):
        runner = TaskRunner(max_tasks=1, name_prefix="test")
        release = threading.Event()

        def slow():
            release.wait(timeout=5)
            return "done"

        first = runner.submit(slow)
        assert first is not None
        try:
            # 第一个任务占用唯一名额，第二个应被拒绝
            assert runner.submit(lambda: "never") is None
        finally:
            release.set()
            _wait_until_finished(runner, first)


class TestPrune:
    def test_prune_clears_expired(self):
        runner = TaskRunner(ttl_seconds=100, name_prefix="test")
        task_id = runner.submit(lambda: "x")
        _wait_until_finished(runner, task_id)
        assert runner.snapshot(task_id) is not None  # 完成但未过期
        # 手动把 finished_at 设为 200 秒前，使其超过 ttl=100
        with runner._lock:
            runner._tasks[task_id]["finished_at"] = time.time() - 200
        runner.prune()
        assert runner.snapshot(task_id) is None  # 过期任务被清理

    def test_pending_count(self):
        runner = TaskRunner(name_prefix="test")
        release = threading.Event()

        def slow():
            release.wait(timeout=5)
            return "done"

        task_id = runner.submit(slow)
        try:
            assert runner.pending_count() == 1
            assert runner.count() == 1
        finally:
            release.set()
            _wait_until_finished(runner, task_id)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
