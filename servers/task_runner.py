"""通用异步任务队列 —— 单 worker 串行执行，供 EDA / HFSS / CST 复用。

把「提交任务 → 后台执行 → 查询状态」的异步模式抽成单一职责的 TaskRunner 类，
避免各模块重复造轮子（现有 EDA 仿真 / HFSS 各自有一套，后续逐步迁移到本类）。

状态机：QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED，完成后超过 TTL 自动清理。
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_log = logging.getLogger("task_runner")


class QueueBusyError(RuntimeError):
    """队列忙 —— require_idle=True 时已有排队或执行中的任务（工具层据此立即回「忙」）。"""


class TaskRunner:
    """单 worker 异步任务队列。

    用法：
        runner = TaskRunner(name_prefix="cst")
        task_id = runner.submit(solve_model, model_path)
        snap = runner.snapshot(task_id)   # {"status": "RUNNING", ...}
    """

    def __init__(
        self,
        max_workers: int = 1,
        ttl_seconds: int = 7200,
        max_tasks: int = 50,
        max_run_seconds: float | None = None,
        name_prefix: str = "task",
    ) -> None:
        # 单 worker 线程池：重量级操作串行执行，避免并发抢占外部软件
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name_prefix)
        # 任务注册表 + 锁
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
        self._max_tasks = max_tasks
        self._max_run_seconds = max_run_seconds
        # 进程退出时调用 shutdown(wait=False)：worker 线程是非 daemon，仍在运行的任务会延迟进程退出
        atexit.register(self._shutdown)

    def _shutdown(self) -> None:
        """进程退出时释放 executor（wait=False 不等待；worker 线程非 daemon，未完成任务会阻塞解释器退出）。"""
        self._executor.shutdown(wait=False)

    # ── 提交 ──

    def submit(self, func: Callable[..., Any], *args, metadata: dict[str, Any] | None = None,
               require_idle: bool = False, **kwargs) -> str | None:
        """提交任务到队列，返回 task_id；任务数达到上限时返回 None。

        Args:
            func: 要异步执行的函数（在后台线程运行）。
            *args, **kwargs: 传给 func 的参数。
            metadata: 可选，随任务存储的额外元数据（如 project_name 等查询字段）。
            require_idle: 为 True 时仅当队列空闲才提交（原子「检查+提交」，避免 TOCTOU）。

        Returns:
            task_id 字符串；任务数达到上限或 require_idle 且非空闲时返回 None。
        """
        task_id = uuid.uuid4().hex[:12]
        task = {
            "task_id": task_id,
            "status": "QUEUED",
            "message": "等待执行",
            "result": None,
            "error": None,
            "metadata": dict(metadata or {}),
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
        }
        with self._lock:
            self._prune_locked()
            if require_idle and self._pending_locked() > 0:
                return None
            active = sum(1 for t in self._tasks.values() if t.get("finished_at") is None)
            if active >= self._max_tasks:
                return None
            self._tasks[task_id] = task
        try:
            self._executor.submit(self._run, task_id, func, args, kwargs)
        except Exception:
            # executor 已关闭（进程退出中）时提交失败，回滚任务避免永久 QUEUED
            with self._lock:
                self._tasks.pop(task_id, None)
            raise
        meta = dict(metadata or {})
        op = meta.get("operation", meta.get("project_name", ""))
        _log.info("task=%s status=QUEUED op=%s", task_id, op)
        return task_id

    # ── 查询 ──

    def snapshot(self, task_id: str) -> dict[str, Any] | None:
        """返回任务快照（浅拷贝，脱离锁）；任务不存在返回 None。

        查询时顺带清理过期任务，避免「已过期任务残留直到下次 submit」。
        """
        with self._lock:
            self._prune_locked()
            task = self._tasks.get(task_id)
            return dict(task) if task is not None else None

    def list(self) -> list[dict[str, Any]]:
        """返回所有未过期任务的快照列表（按创建时间排序，浅拷贝脱离锁）。"""
        with self._lock:
            self._prune_locked()
            return [dict(t) for t in sorted(self._tasks.values(), key=lambda t: t["created_at"])]

    def cancel(self, task_id: str, kill: Callable[[], None] | None = None) -> tuple[bool, str]:
        """取消任务，返回 (是否成功取消, 原因)。

        QUEUED 直接标记取消；RUNNING 标记取消并在持锁外调用 kill()（可选）终止底层
        子进程。运行中任务被 kill 后，worker 线程的 _run 因 status 已非 RUNNING 而
        不再覆盖结果，取消原因得以保留。终态为 CANCELLED，与真失败的 FAILED 区分。
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False, "任务不存在或已过期"
            if task.get("finished_at") is not None:
                return False, "任务已结束，无法取消"
            was_running = task["status"] == "RUNNING"
            msg = "任务已取消（执行中，已尝试终止）" if was_running else "任务已取消（尚未开始）"
            # 取消是独立终态：写成 FAILED 会让调用方分不清「被取消」和「真失败」
            # （2026-09-28 全量体检 GW-6）。原因仍写在 error 字段。
            task["status"] = "CANCELLED"
            task["error"] = msg
            task["finished_at"] = time.time()
        if was_running and kill is not None:
            kill()
        return True, msg

    def count(self) -> int:
        """当前任务总数（含已完成未过期的）。"""
        with self._lock:
            return len(self._tasks)

    def pending_count(self) -> int:
        """排队 + 运行中的任务数。"""
        with self._lock:
            return self._pending_locked()

    def _pending_locked(self) -> int:
        """（需持锁）排队 + 运行中的任务数。"""
        return sum(1 for t in self._tasks.values()
                   if t["status"] in ("QUEUED", "RUNNING"))

    # ── 清理 ──

    def prune(self) -> None:
        """清理过期的已完成/失败任务。"""
        with self._lock:
            self._prune_locked()

    def run_sync(self, func: Callable[..., Any], *args, metadata: dict[str, Any] | None = None,
                 poll_interval: float = 0.05, timeout: float | None = None,
                 require_idle: bool = False, kill: Callable[[], None] | None = None,
                 **kwargs) -> Any:
        """同步执行 func：提交后阻塞等待完成，返回结果；失败抛 RuntimeError。

        用于「必须串行化但希望同步拿到结果」的场景（如 cst_export_snp 无会话读结果，
        需与 cst_runner 里的求解会话串行，避免读到半写结果）。

        Args:
            timeout: 可选，最长等待秒数；超时抛 RuntimeError。None 表示无限等待。
            require_idle: True 时仅当队列空闲才提交（原子「检查+提交」），已有排队/
                执行中的任务时抛 QueueBusyError —— 让工具层立即回「忙」，而不是排在
                重量级任务后面傻等到超时。
            kill: 可选，超时撤销任务时用来终止底层子进程（如 cst_cancel_active）。
        """
        task_id = self.submit(func, *args, metadata=metadata, require_idle=require_idle, **kwargs)
        if task_id is None:
            if require_idle:
                raise QueueBusyError("队列忙：已有排队或执行中的任务")
            raise RuntimeError("任务队列已满")
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            snap = self.snapshot(task_id)
            if snap is None:
                raise RuntimeError("任务不存在或已过期")
            if snap.get("finished_at") is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                # 超时必须连任务一起撤销：只抛异常的话它仍排在单 worker 队列里，稍后
                # 照样执行 ⇒ 调用方已放弃、它却在后台跑（幽灵任务，2026-09-28 GW-1）。
                self.cancel(task_id, kill=kill)
                raise RuntimeError(f"任务执行超时（>{timeout:.0f}s，已自动撤销该任务）")
            time.sleep(poll_interval)
        if snap["status"] == "SUCCEEDED":
            return snap["result"]
        raise RuntimeError(snap.get("error") or "任务执行失败")

    # ── 内部 ──

    def _run(self, task_id: str, func: Callable, args: tuple, kwargs: dict) -> None:
        """后台线程执行体：执行 func，把结果/异常写回任务。"""
        with self._lock:
            task = self._tasks.get(task_id)
            # 已被取消 / 超时兜底 / 清理的任务不再执行：cancel() 只改状态，但本方法
            # 早已排在 executor 队列里 —— 少这道门，被取消的**排队**任务排到队后仍会
            # 真跑，并把状态从「已取消」改回 RUNNING→SUCCEEDED（2026-09-28 复核发现）。
            if task is None or task.get("finished_at") is not None:
                _log.info("task=%s skipped (cancelled/finished before start)", task_id)
                return
            task["status"] = "RUNNING"
            task["started_at"] = time.time()
        _log.info("task=%s status=RUNNING", task_id)
        started = time.time()
        try:
            result = func(*args, **kwargs)
            with self._lock:
                task = self._tasks.get(task_id)
                # 仅当仍为 RUNNING 才写回成功：若已被超时兜底标为 FAILED，不再覆盖
                if task is not None and task["status"] == "RUNNING":
                    task["status"] = "SUCCEEDED"
                    task["result"] = result
                    task["finished_at"] = time.time()
            _log.info("task=%s status=SUCCEEDED duration=%.1fs", task_id, time.time() - started)
        except Exception as exc:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None and task["status"] == "RUNNING":
                    task["status"] = "FAILED"
                    task["error"] = str(exc)
                    task["finished_at"] = time.time()
            _log.info("task=%s status=FAILED duration=%.1fs error=%s", task_id, time.time() - started, str(exc))

    def _prune_locked(self) -> None:
        """（需持锁）清理超过 TTL 的已完成/失败任务；RUNNING 超时兜底标记 FAILED。"""
        now = time.time()
        expired = [
            tid for tid, t in self._tasks.items()
            if t.get("finished_at") is not None and now - t["finished_at"] > self._ttl
        ]
        for tid in expired:
            del self._tasks[tid]

        # RUNNING 任务超时兜底：func 挂死时标记 FAILED，避免永久占用槽位
        if self._max_run_seconds is not None:
            stuck = [
                tid for tid, t in self._tasks.items()
                if t.get("status") == "RUNNING" and t.get("started_at") is not None
                and now - t["started_at"] > self._max_run_seconds
            ]
            for tid in stuck:
                self._tasks[tid]["status"] = "FAILED"
                self._tasks[tid]["error"] = f"执行超时（>{self._max_run_seconds:.0f}s）"
                self._tasks[tid]["finished_at"] = now
