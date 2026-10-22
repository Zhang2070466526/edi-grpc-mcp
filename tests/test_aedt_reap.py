"""AEDT 自启动进程登记与回收（R46-②）。

背景：`open_hfss_project` / `launch_aedt` 用裸 `subprocess.Popen` 拉起 AEDT，
只记 `_LAST_PID`（int）不持有句柄 ⇒ 服务退出后变孤儿进程、继续占软件与锁。
现在 `_register_launched()` 持有句柄（并顺手清理已退出的），`atexit` 时 terminate。
"""

from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

from servers.ansys import project_manage as PM


@pytest.fixture(autouse=True)
def _clean_registry():
    PM._LAUNCHED.clear()
    yield
    PM._LAUNCHED.clear()


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def test_reaps_live_child():
    proc = _spawn_sleeper()
    PM._register_launched(proc)

    PM._terminate_launched()

    assert proc.poll() is not None, "存活子进程必须被 terminate/kill 回收"


def test_already_exited_child_is_skipped():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=15)
    PM._LAUNCHED.append(proc)

    PM._terminate_launched()  # 不应抛异常

    assert proc.returncode is not None


def test_empty_registry_is_noop():
    PM._terminate_launched()  # 不抛即可


def test_terminate_is_idempotent():
    proc = _spawn_sleeper()
    PM._register_launched(proc)

    PM._terminate_launched()
    PM._terminate_launched()

    assert proc.poll() is not None


def test_register_purges_exited_entries():
    """已退出的进程不应无限累积在 _LAUNCHED 里。"""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=15)
    PM._LAUNCHED.append(dead)
    assert len(PM._LAUNCHED) == 1

    live = _spawn_sleeper()
    PM._register_launched(live)

    assert PM._LAUNCHED == [live], f"已退出项应被剔除，实际: {PM._LAUNCHED}"
    live.terminate()
    live.wait(timeout=10)


def test_launch_sites_use_register_helper():
    """两处 Popen 都必须走 _register_launched（裸 append 会跳过清理）。"""
    src = inspect.getsource(PM)
    assert src.count("_register_launched(proc)") == 2, "两处 Popen 都要登记"
    assert src.count("_LAUNCHED.append(proc)") == 1, "只有 _register_launched 内部允许 append"
    assert "atexit.register(_terminate_launched)" in src
