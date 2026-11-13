"""LASPCEM 异步任务层回归：求解任务 + 服务端串联的后处理（导出/建报告）。"""

from __future__ import annotations

import time

from servers.laspcem import async_solve as A


def _wait(tid: str, timeout: float = 5.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        snap = A._runner.snapshot(tid)
        if snap and snap.get("finished_at"):
            return snap
        time.sleep(0.02)
    return A._runner.snapshot(tid) or {}


def _sem(tmp_path, name: str = "p.sem") -> str:
    p = tmp_path / name
    p.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    return str(p)


# ── 提交阶段 ────────────────────────────────────────────────

def test_submit_returns_task_id_and_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve", lambda *a, **k: {"success": True})
    r = A.run_laspcem_solve_async(_sem(tmp_path), timeout_seconds=60)
    assert r["success"] is True
    assert r["status"] == "QUEUED" and r["task_id"]
    assert r["steps"] == ["solve"]
    _wait(r["task_id"])


def test_submit_with_then_export_declares_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve", lambda *a, **k: {"success": True})
    monkeypatch.setattr(A, "export_laspcem_snp", lambda *a, **k: {"success": True, "data_rows": 1})
    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True, then_report=True)
    assert r["steps"] == ["solve", "export", "report"]
    _wait(r["task_id"])


def test_submit_invalid_path(tmp_path):
    r = A.run_laspcem_solve_async(str(tmp_path / "nope.sem"))
    assert r["error_code"] == "INVALID_PATH"
    assert "task_id" not in r


def test_submit_rejects_bad_setup_before_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("坏 setup 不该进队列")))
    r = A.run_laspcem_solve_async(_sem(tmp_path), setup_name="NoSuchSetup")
    assert r["error_code"] == "SETUP_NOT_FOUND"


# ── 链式执行：求解 → 导出 → 报告 ─────────────────────────────

def test_chain_runs_export_after_solve(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: calls.append("solve") or {"success": True, "saw_solver": True})
    monkeypatch.setattr(A, "export_laspcem_snp",
                        lambda *a, **k: calls.append("export") or {"success": True, "data_rows": 91})

    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True)
    snap = _wait(r["task_id"])
    q = A.laspcem_solve_query(r["task_id"])

    assert calls == ["solve", "export"]                 # 顺序由服务端保证
    assert snap["status"] == "SUCCEEDED"
    assert q["success"] is True and q["completed"] is True
    assert q["solve_result"]["saw_solver"] is True
    assert q["export_result"]["data_rows"] == 91
    assert [s["status"] for s in q["steps"]] == ["SUCCEEDED", "SUCCEEDED"]


def test_chain_runs_report_after_export(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: calls.append("solve") or {"success": True})
    monkeypatch.setattr(A, "export_laspcem_snp",
                        lambda *a, **k: calls.append("export") or {"success": True, "data_rows": 2})
    monkeypatch.setattr(A, "build_laspcem_report",
                        lambda *a, **k: calls.append("report") or {"success": True, "verified": True})

    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True, then_report=True)
    _wait(r["task_id"])
    q = A.laspcem_solve_query(r["task_id"])
    assert calls == ["solve", "export", "report"]
    assert q["report_result"]["verified"] is True


def test_chain_stops_when_solve_fails(tmp_path, monkeypatch):
    """求解失败 ⇒ 不跑到导出，任务报求解的错误码。"""
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: {"success": False, "error_code": "SOLVE_STALLED",
                                        "message": "卡住"})
    monkeypatch.setattr(A, "export_laspcem_snp",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该导出")))
    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True)
    _wait(r["task_id"])
    q = A.laspcem_solve_query(r["task_id"])
    assert q["success"] is False and q["error_code"] == "SOLVE_STALLED"
    assert q["steps"][1]["status"] == "PENDING"
    assert "export_result" not in q


def test_chain_continues_when_solve_skipped_but_solved(tmp_path, monkeypatch):
    """LASPCEM 认为“已是最新、跳过重算”不算失败：工程里有解就继续导出。"""
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: {"success": False, "error_code": "SOLVE_NOT_TRIGGERED",
                                        "skipped_likely": True})
    called = {"n": 0}

    def fake_export(*a, **k):
        called["n"] += 1
        return {"success": True, "data_rows": 91}

    monkeypatch.setattr(A, "export_laspcem_snp", fake_export)
    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True)
    _wait(r["task_id"])
    q = A.laspcem_solve_query(r["task_id"])
    assert called["n"] == 1
    assert q["success"] is True and q["solve_skipped"] is True
    assert q["export_result"]["data_rows"] == 91


def test_chain_keeps_solve_result_when_export_fails(tmp_path, monkeypatch):
    """求解成功但导出失败：两者结果都要带回来，不能把已完成的求解丢掉。"""
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: {"success": True, "saw_solver": True, "solver_seconds": 12.3})
    monkeypatch.setattr(A, "export_laspcem_snp",
                        lambda *a, **k: {"success": False, "error_code": "EXPORT_NO_OUTPUT",
                                        "message": "没产物"})
    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True)
    _wait(r["task_id"])
    q = A.laspcem_solve_query(r["task_id"])
    assert q["success"] is False and q["error_code"] == "EXPORT_NO_OUTPUT"
    assert q["solve_result"]["solver_seconds"] == 12.3
    assert q["steps"][0]["status"] == "SUCCEEDED"
    assert q["steps"][1]["status"] == "FAILED"


# ── 进度 / 列表 / 取消 ─────────────────────────────────────

def test_query_reports_phase_and_progress(tmp_path, monkeypatch):
    sem = _sem(tmp_path)
    base = tmp_path / "p"
    (base / "1_Result").mkdir(parents=True)
    (base / "1_Result" / "Prog.log").write_text("Iter1 mesh\nIter6: Solving\n", encoding="utf-8")
    for i in (1, 2):
        (base / "1_Result" / f"HOFEM_adapt_itr{i}.log").write_text("x", encoding="utf-8")

    monkeypatch.setattr(A, "export_laspcem_snp", lambda *a, **k: {"success": True})
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: (time.sleep(1.5), {"success": True})[1])
    r = A.run_laspcem_solve_async(sem, then_export=True)
    # 单 worker 可能排队：轮询到 "solving" 那一刻取样（求解 1.5 s，足够取到）
    sample = None
    deadline = time.time() + 3
    while time.time() < deadline:
        q = A.laspcem_solve_query(r["task_id"])
        if q.get("phase") == "solving":
            sample = q
            break
        if q.get("completed"):
            break
        time.sleep(0.02)
    assert sample is not None, q
    q = sample
    assert q["completed"] is False
    assert q["phase"] == "solving"
    assert q["steps"][0]["name"] == "solve"
    assert q["adapt_iterations"] == 2
    assert q["last_log_line"] == "Iter6: Solving"
    assert "elapsed_seconds" in q and q["elapsed_seconds"] >= 0
    _wait(r["task_id"])


def test_task_list_marks_operation_and_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve", lambda *a, **k: {"success": True})
    monkeypatch.setattr(A, "export_laspcem_snp", lambda *a, **k: {"success": True})
    r = A.run_laspcem_solve_async(_sem(tmp_path), then_export=True)
    _wait(r["task_id"])
    item = next(t for t in A.laspcem_task_list()["tasks"] if t["task_id"] == r["task_id"])
    assert item["operation"] == "solve"
    assert item["steps"] == ["solve", "export"]


def test_cancel_running_task(tmp_path, monkeypatch):
    sem = _sem(tmp_path)
    monkeypatch.setattr(A, "run_laspcem_solve",
                        lambda *a, **k: (time.sleep(0.5), {"success": True})[1])
    killed = {}
    monkeypatch.setattr(A, "_kill_task", lambda h, s: killed.setdefault("sem", s))

    first = A.run_laspcem_solve_async(sem)
    second = A.run_laspcem_solve_async(sem)        # 单 worker ⇒ 排队
    c = A.laspcem_task_cancel(second["task_id"])
    assert c["status"] == "CANCELLED"
    assert A.laspcem_solve_query(second["task_id"])["error_code"] == "TASK_CANCELLED"
    assert killed or _wait(first["task_id"])    # 至少队列能继续推进


def test_cancel_finished_task_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve", lambda *a, **k: {"success": True})
    r = A.run_laspcem_solve_async(_sem(tmp_path))
    _wait(r["task_id"])
    c = A.laspcem_task_cancel(r["task_id"])
    assert c["error_code"] == "TASK_ALREADY_FINISHED"


def test_query_unknown_task():
    r = A.laspcem_solve_query("nope123456")
    assert r["error_code"] == "TASK_NOT_FOUND"


def test_reap_clears_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "run_laspcem_solve", lambda *a, **k: {"success": True})
    r = A.run_laspcem_solve_async(_sem(tmp_path))
    _wait(r["task_id"])
    A.laspcem_solve_query(r["task_id"])
    assert r["task_id"] not in A._cancel_events
    assert r["task_id"] not in A._handles
    assert r["task_id"] not in A._phases


# ── 队列约束 ────────────────────────────────────────────────

def test_queue_full_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(A._runner, "pending_count", lambda: 99)
    r = A.run_laspcem_solve_async(_sem(tmp_path))
    assert r["error_code"] == "LASPECM_QUEUE_FULL"
