"""laspcem_solve_query 的有界长轮询：早退、上限 clamp、注入时钟。"""
from servers.laspcem.async_solve import _bounded_wait


class _Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def test_no_wait_returns_immediately():
    calls = []

    def snap(tid):
        calls.append(tid)
        return {"finished_at": None}

    c = _Clock()
    _bounded_wait("t1", 0, snap, sleep=c.sleep, clock=c.now)
    assert calls == [] and c.sleeps == []


def test_finished_task_returns_without_sleeping():
    calls = []

    def snap(tid):
        calls.append(tid)
        return {"finished_at": 1.0}

    c = _Clock()
    _bounded_wait("t1", 30, snap, sleep=c.sleep, clock=c.now)
    assert len(calls) == 1 and c.sleeps == []


def test_missing_task_returns_immediately():
    c = _Clock()
    _bounded_wait("gone", 60, lambda tid: None, sleep=c.sleep, clock=c.now)
    assert c.sleeps == []


def test_running_task_wait_is_clamped_to_max():
    """wait_seconds 传 9999 也最多等 max_wait（默认 120 s），防止堵死单槽队列。"""
    c = _Clock()
    _bounded_wait("t1", 9999, lambda tid: {"finished_at": None},
                  sleep=c.sleep, clock=c.now, max_wait=120, interval=2.0)
    assert sum(c.sleeps) <= 120
    assert sum(c.sleeps) >= 118      # 一直 RUNNING 就等到上限
    assert c.t <= 122                # 探测本身不额外超时


def test_cancelled_task_returns_early():
    c = _Clock()
    seq = iter([{"finished_at": None}, {"finished_at": None, "status": "CANCELLED"}])
    _bounded_wait("t1", 60, lambda tid: next(seq, {"finished_at": None}),
                  sleep=c.sleep, clock=c.now)
    assert len(c.sleeps) == 1        # 第 2 次探测发现 CANCELLED 后立即退出，不再睡
