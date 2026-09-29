"""测试通用异步任务队列 TaskRunner —— 生命周期、结果/异常写回、队列满、清理。

TaskRunner 是 CST 求解 / 远场导出（后续 EDA / HFSS 也会迁移）共用的基础组件，
这里用内存线程池验证其状态机与边界行为，不依赖任何外部软件。
"""
import threading
import time
from pathlib import Path

import pytest

from servers.task_runner import QueueBusyError, TaskRunner


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


def test_cancelled_queued_task_is_not_executed():
    """回归 F4：取消的**排队**任务排到队后也不得执行。

    旧实现里 `_run` 无条件把任务标回 RUNNING 并真跑 ⇒ 取消只改了状态，
    「取消了怎么还在跑」。
    """
    runner = TaskRunner(name_prefix="cancel-test", max_workers=1)
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow():
        started.set()
        release.wait(5)
        return "slow"

    def quick():
        calls.append("quick")
        return "quick"

    task1 = runner.submit(slow)
    assert started.wait(5)
    task2 = runner.submit(quick)
    ok, reason = runner.cancel(task2)
    assert ok is True
    assert "尚未开始" in reason
    release.set()
    deadline = time.time() + 5
    while time.time() < deadline and runner.pending_count() > 0:
        time.sleep(0.05)
    assert calls == [], "被取消的排队任务不得执行"
    assert runner.snapshot(task2)["status"] == "CANCELLED"""
    assert runner.snapshot(task1)["status"] == "SUCCEEDED"


class TestRunSync:
    """run_sync 的队列语义：忙时立即抛 QueueBusyError；超时连任务一起撤销。"""

    def test_require_idle_raises_queue_busy_when_task_pending(self):
        runner = TaskRunner(name_prefix="busy-test", max_workers=1)
        release = threading.Event()

        def slow():
            release.wait(5)
            return "slow"

        blocker = runner.submit(slow)
        try:
            with pytest.raises(QueueBusyError):
                runner.run_sync(lambda: "never", timeout=1, require_idle=True)
        finally:
            release.set()
            _wait_until_finished(runner, blocker)

    def test_timeout_cancels_task_instead_of_leaving_it_queued(self):
        """回归 GW-1：超时只抛异常的话，任务仍排在单 worker 队列里、稍后照样执行。"""
        runner = TaskRunner(name_prefix="timeout-test", max_workers=1)
        started, release = threading.Event(), threading.Event()
        calls = []

        def blocking():
            started.set()
            release.wait(5)
            return "blocking"

        def queued():
            calls.append("queued")
            return "queued"

        blocker = runner.submit(blocking)
        assert started.wait(5)
        try:
            with pytest.raises(RuntimeError) as exc:
                runner.run_sync(queued, timeout=0.2, poll_interval=0.01)
            assert "已自动撤销" in str(exc.value)
            assert "CANCELLED" in {t["status"] for t in runner.list()}
        finally:
            release.set()
            _wait_until_finished(runner, blocker)
        assert calls == [], "超时撤销的排队任务不得再执行"

    def test_success_still_returns_result(self):
        runner = TaskRunner(name_prefix="sync-test")
        assert runner.run_sync(lambda a, b: a * b, 6, 7, timeout=5) == 42


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
