"""LASPCEM 工程占用登记表 —— 同一个 .sem 同一时刻只允许一个「会打开它」的操作。

为什么需要它（2026-11-13 审查）：

`launcher._project_in_use()` 靠 LASPCEM 进程的 cmdline 反查工程路径，但**求解实例是
`laspcem.exe /session <临时脚本>`，cmdline 里不含 .sem**（实测见 `async_solve._related_pids`
注释）。于是「异步求解正在跑」时，同步 `export_laspcem_snp` / `build_laspcem_report`
的占用检查拿到的是空列表 → 起第二个 LASPCEM 实例读写同一个 `.sem` / `1_Result`：
Touchstone 半写、报告建在过期解上、求解作废。

这张表只解决**同一服务进程内**的互斥（跨进程仍靠 `_project_in_use` + LASPCEM 自己的
`~$<stem>.lock`）。进程重启后表自然清空，不需要持久化。

语义：
- `acquire(key, owner)` 成功返回 True；已被**别的** owner 占用返回 False + 当前持有者。
- 同一 owner 重入计数（then_export / then_report 在求解任务线程里复用同一 owner）。
- `conflict(key)` 查冲突，**自动豁免当前线程绑定的 owner**（后处理链路不该被自己拦住）。
"""

from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.RLock()
#: 归一化工程路径 -> {"owner": str, "kind": str, "count": int, "since": float}
_HOLDERS: dict[str, dict[str, Any]] = {}
#: 线程 -> 当前 owner（异步任务线程内设，后处理调用据此豁免自己）
_TLS = threading.local()


def canon(sem_path: str) -> str:
    """工程路径归一化（大小写/斜杠统一），作为登记表键。"""
    from pathlib import Path

    raw = (sem_path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).resolve()).lower()
    except OSError:
        return raw.replace("\\", "/").lower()


def bind_thread(owner: str) -> None:
    """把当前线程绑定到某个 owner（异步任务线程入口调用）。"""
    _TLS.owner = owner or ""


def thread_owner() -> str:
    """当前线程绑定的 owner；空串表示不是后台任务线程。"""
    return getattr(_TLS, "owner", "") or ""


def acquire(sem_path: str, owner: str, kind: str = "") -> tuple[bool, dict[str, Any] | None]:
    """占用工程。已被别的 owner 占用时返回 (False, 当前持有者信息)。"""
    key = canon(sem_path)
    if not key or not owner:
        return True, None
    with _LOCK:
        held = _HOLDERS.get(key)
        if held and held["owner"] != owner:
            return False, dict(held)
        if held:
            held["count"] += 1
            if kind:
                held["kind"] = kind
            return True, None
        _HOLDERS[key] = {"owner": owner, "kind": kind, "count": 1, "since": time.time(),
                         "sem_path": sem_path}
        return True, None


def release(sem_path: str, owner: str) -> None:
    """释放占用（重入计数减一，归零才真正移除）。"""
    key = canon(sem_path)
    if not key or not owner:
        return
    with _LOCK:
        held = _HOLDERS.get(key)
        if not held or held["owner"] != owner:
            return
        held["count"] -= 1
        if held["count"] <= 0:
            _HOLDERS.pop(key, None)


def clear_owner(owner: str) -> int:
    """兜底释放：把该 owner 持有的所有工程一次清掉（任务终态/异常退出时用）。返回清理条数。"""
    if not owner:
        return 0
    with _LOCK:
        keys = [k for k, v in _HOLDERS.items() if v["owner"] == owner]
        for key in keys:
            _HOLDERS.pop(key, None)
        return len(keys)


def holder(sem_path: str) -> dict[str, Any] | None:
    """不做豁免的原始查询（排查/测试用）。"""
    key = canon(sem_path)
    with _LOCK:
        held = _HOLDERS.get(key)
        return dict(held) if held else None


def conflict(sem_path: str) -> dict[str, Any] | None:
    """查冲突：返回占用者信息；当前线程自己持有则视为无冲突（后处理重入）。"""
    held = holder(sem_path)
    if held is None:
        return None
    if held["owner"] == thread_owner():
        return None
    return held


def snapshot() -> list[dict[str, Any]]:
    """当前所有占用（排查用：谁在动哪个工程、占了多久）。"""
    with _LOCK:
        now = time.time()
        return [
            {"sem_path": v.get("sem_path") or key, "owner": v["owner"], "kind": v["kind"],
             "count": v["count"], "held_seconds": round(now - v["since"], 1)}
            for key, v in _HOLDERS.items()
        ]
