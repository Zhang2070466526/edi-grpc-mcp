"""LASPCEM 工具 — 路径解析、参数校验、脚本生成、setup/解名探测、求解与导出编排的回归。"""
import tempfile
from pathlib import Path

from servers.laspcem.config import _first_quoted_arg
from servers.laspcem.launcher import (
    build_laspcem_report,
    export_laspcem_snp,
    get_laspcem_setup,
    open_laspcem_project,
    run_laspcem_solve,
)
from servers.laspcem.script_gen import (
    build_export_snp_script,
    build_import_aedt_script,
    build_report_script,
    build_solve_script,
)
from servers.laspcem.setup_reader import (
    _extract_value,
    detect_setup_name,
    detect_setup_names,
    detect_solution_name,
    detect_solution_names,
    extract_design_variables,
    is_solved,
    read_setup_report,
    split_setup_name,
)


def test_parse_exe_from_open_command():
    assert _first_quoted_arg(r'"D:\LASPCEM_2026\bin\laspcem.exe" "%1"') == r"D:\LASPCEM_2026\bin\laspcem.exe"
    assert _first_quoted_arg(r'  "C:\Program Files\X\a.exe"  %1') == r"C:\Program Files\X\a.exe"
    assert _first_quoted_arg("noquotes.exe %1") == ""
    assert _first_quoted_arg("") == ""


def test_open_laspcem_project_rejects_bad_path():
    r = open_laspcem_project("Z:/nonexistent/foo.sem")
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


def test_open_laspcem_project_rejects_bad_extension(tmp_path):
    p = tmp_path / "foo.txt"
    p.write_text("x", encoding="utf-8")
    r = open_laspcem_project(str(p))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


# --------------------------------------------------------------------------
# 脚本生成：实测约束（首行版本头 / setup 用显示名 / 求解脚本不 SaveAs 不 Exit）
# --------------------------------------------------------------------------
def test_build_solve_script_matches_recording():
    """与 GUI 录制脚本一致：Open + SetSelectionMode + FEMAnalyze，且不 SaveAs、不 Exit。"""
    s = build_solve_script(r"C:\proj\CPW.sem", setup_name="Setup2(FEM)")
    assert s.splitlines()[0].startswith("#Version Information:")   # 宿主靠这行选版本
    assert 'app.Open("C:/proj/CPW.sem")' in s                      # 反斜杠转正斜杠
    assert s.count("C:/proj/CPW.sem") == 1                          # 只有 Open，没有 SaveAs
    assert "cem.FEMAnalyze('Setup2'," in s                          # 用显示名，不能带 (FEM)
    assert "Setup2(FEM)" not in s
    assert "app.SaveAs" not in s                                    # SaveAs 会掐死异步求解器
    assert "app.Exit" not in s                                      # Exit 同理


def test_build_solve_script_has_no_cleanup():
    """不提供强制重解：脚本里不该出现 CleanUpFEMSolution（实测不可靠且破坏性）。"""
    s = build_solve_script(r"C:\proj\CPW.sem", setup_name="Setup1(FEM)")
    assert "CleanUpFEMSolution" not in s
    assert "app.SaveAs" not in s
    assert "app.Exit" not in s


def test_build_export_snp_script_no_analyze():
    """导出脚本只打开工程 + ExportSnpFile（求解必须由 run_laspcem_solve 先跑完）。"""
    s = build_export_snp_script(r"C:\proj\CPW.sem", r"C:\proj\CPW.sNp", solution_name="FEM_2")
    assert "report.ExportSnpFile" in s
    assert "cem.FEMAnalyze" not in s
    assert "'Solution Name': 'FEM_2'" in s
    assert "C:/proj/CPW.sNp" in s
    assert s.rstrip().endswith("pass") or "app.Exit()" in s         # 导出脚本会自己退出


def test_build_report_script():
    s = build_report_script(r"C:\proj\CPW.sem", report_name="S1", solution_name="FEM_1")
    assert "report.CreateNetworkPlot" in s
    assert "'Name': 'S1'" in s
    assert "'Solution Name': 'FEM_1'" in s
    assert "app.SaveAs" in s                                        # 报告要存回工程


def test_builders_default_solution_name_is_algo_setup():
    """解名缺省 = 算法_基名（Setup1(FEM) → FEM_Setup1；无括号按 FEM）。"""
    s = build_export_snp_script(r"C:\proj\CPW.sem", r"C:\proj\CPW.sNp", setup_name="Setup2(FEM)")
    assert "'Solution Name': 'FEM_Setup2'" in s
    s2 = build_report_script(r"C:\proj\CPW.sem", setup_name="Setup3")
    assert "'Solution Name': 'FEM_Setup3'" in s2


# --------------------------------------------------------------------------
# setup / 解名 / 产物探测
# --------------------------------------------------------------------------
def test_resolve_solution_name_default_and_override(tmp_path):
    from servers.laspcem.launcher import _resolve_solution_name

    sem = tmp_path / "s.sem"
    sem.write_bytes(b"FEM_1 FEM_2")
    assert _resolve_solution_name(str(sem), "Setup1(FEM)", "") == ("FEM_Setup1", ["FEM_1", "FEM_2"])
    assert _resolve_solution_name(str(sem), "Setup2(MoM)", "")[0] == "MoM_Setup2"
    assert _resolve_solution_name(str(sem), "Setup1(FEM)", "FEM_1")[0] == "FEM_1"


def test_detect_setup_name_prefers_algorithm_suffix(tmp_path):
    sem = tmp_path / "s.sem"
    sem.write_bytes(b"Setups1 Setups Setup Setup1Q Setup1(FEM) " * 3 + b"Setup1")
    assert detect_setup_name(str(sem)) == "Setup1(FEM)"
    assert "Setup1(FEM)" in detect_setup_names(str(sem))


def test_split_setup_name():
    assert split_setup_name("Setup1(FEM)") == ("Setup1", "FEM")
    assert split_setup_name("Setup_20261106_151752(MoM)") == ("Setup_20261106_151752", "MoM")
    assert split_setup_name("Setup1") == ("Setup1", "FEM")


def test_detect_solution_names_ignores_binary_noise(tmp_path):
    """只认 FEM_/MoM_/FDTD_/PO_<序号>（实测曾误取 A_7）。"""
    sem = tmp_path / "s.sem"
    sem.write_bytes(b"A_7 Q_0 x_7 N_57 ParametricSetup_20260713 FEM_1 XFEM_9 FEM_2")
    assert detect_solution_names(str(sem)) == ["FEM_1", "FEM_2"]
    assert detect_solution_name(str(sem)) == "FEM_2"
    sem.write_bytes(b"A_7 Q_0 ParametricSetup_20260713")
    assert detect_solution_names(str(sem)) == []
    assert detect_solution_name(str(sem)) == ""


def test_resolve_snp_output_accepts_port_suffix(tmp_path):
    """请求 .sNp，实际落地 .s2p（端口数决定）⇒ 必须认出来。"""
    from servers.laspcem.launcher import _resolve_snp_output

    (tmp_path / "a.s2p").write_text("! touchstone\n1.0 2.0 3.0\n", encoding="utf-8")
    got = _resolve_snp_output(str(tmp_path / "a.sNp"))
    assert got is not None and got.endswith("a.s2p")
    assert _resolve_snp_output(str(tmp_path / "b.s3p")) is None


def test_new_solutions_diff():
    from servers.laspcem.launcher import _new_solutions

    assert _new_solutions(["FEM_1", "FEM_2"], ["FEM_1", "FEM_2"]) == []   # 同 setup 重解=覆盖
    assert _new_solutions(["FEM_1"], ["FEM_1", "FEM_2"]) == ["FEM_2"]


def test_solve_hint_branches(tmp_path):
    from servers.laspcem.launcher import _solve_hint

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")
    h = _solve_hint("Setup1", "saw_solver=False\ntimed_out=False\n", str(sem))
    assert "mpiexec" in h and "Setup1(FEM)" in h
    h2 = _solve_hint("Setup1", "saw_solver=True\ntimed_out=True\n", str(sem))
    assert "超时" in h2
    h3 = _solve_hint("Setup1", "", str(sem))
    assert "script_log_tail" in h3


# --------------------------------------------------------------------------
# 求解编排：判据是「出现过 mpiexec/phofem」，不是解序号
# --------------------------------------------------------------------------
def test_run_laspcem_solve_not_performed(tmp_path, monkeypatch):
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")          # 没有任何解记录 = 从没解过

    class _P:
        def poll(self):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "_spawn_script", lambda exe, s: (_P(), "tmp.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": False, "timed_out": False, "solver_seconds": 1.0,
        "solver_processes": [], "sem_changed": False})
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_NOT_TRIGGERED"
    assert r["saw_solver"] is False
    assert r["skipped_likely"] is False


def test_run_laspcem_solve_reports_not_triggered_with_existing_solutions(tmp_path, monkeypatch):
    """已有解但没起求解器（LASPCEM 认为已是最新）⇒ 不假报成功，标记 skipped_likely。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 FEM_2 Setup1(FEM)")

    class _P:
        def poll(self):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "_spawn_script", lambda exe, s: (_P(), "tmp.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": False, "timed_out": False, "solver_seconds": 6.0,
        "solver_processes": [], "sem_changed": False})
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_NOT_TRIGGERED"
    assert r["skipped_likely"] is True
    assert "发生变化" in r["hint"]


def test_wait_for_artifact_rejects_stale_file(tmp_path):
    """目录里留着上次的 .s2p 时，不能把旧文件当成本次导出（实测 0.1s 假成功）。"""
    import servers.laspcem.launcher as L

    art = tmp_path / "a.s2p"
    art.write_text("! old", encoding="utf-8")
    before = L._file_sig(str(art))
    # 文件没被改写 → 必须返回 None（不等进程、立刻超时）
    assert L._wait_for_artifact(str(tmp_path / "a.sNp"), None, 1, before_sig=before, poll=0.01) is None
    # 文件被改写（mtime/大小变化）→ 才认
    art.write_text("! new and longer", encoding="utf-8")
    got = L._wait_for_artifact(str(tmp_path / "a.sNp"), None, 1, before_sig=before, poll=0.01)
    assert got is not None and got.endswith("a.s2p")



def test_run_laspcem_solve_success_when_solver_seen(tmp_path, monkeypatch):
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 FEM_2 Setup1(FEM)")
    calls = {"terminated": False}

    class _P:
        def poll(self):
            return None

        def terminate(self):
            calls["terminated"] = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def _fake_wait(path, timeout, **kwargs):
        sem.write_bytes(b"SolutionId FEM_1 FEM_2 Setup1(FEM)" + b"\0" * 500)   # 结果写回
        return {"saw_solver": True, "timed_out": False, "solver_seconds": 128.4,
                "solver_processes": [{"pid": 1, "name": "mpiexec.exe"}], "sem_changed": True}

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "_spawn_script", lambda exe, s: (_P(), "tmp.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", _fake_wait)
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is True and r["solved"] is True
    assert r["saw_solver"] is True and r["solver_seconds"] == 128.4
    assert r["sem_size_after"] > r["sem_size_before"]
    assert calls["terminated"] is True                    # close_gui 默认 True


def test_wait_for_solve_detects_solver_then_stable(tmp_path, monkeypatch):
    """求解器出现过、随后消失且 .sem 稳定 → 判完成；solver_seconds 有值。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"x" * 100)
    seq = [
        [{"pid": 1, "name": "mpiexec.exe"}],
        [{"pid": 1, "name": "mpiexec.exe"}, {"pid": 2, "name": "phofem.exe"}],
        [],
        [],
        [],
    ]

    def _fake_procs():
        return seq.pop(0) if seq else []

    monkeypatch.setattr(L, "_solver_processes", _fake_procs)
    r = L._wait_for_solve(str(sem), timeout_seconds=10, poll=0.01, quiet_seconds=0.02)
    assert r["saw_solver"] is True
    assert r["timed_out"] is False
    assert r["solver_seconds"] >= 0


def test_run_laspcem_solve_rejects_bad_path(tmp_path):
    r = run_laspcem_solve(str(tmp_path / "nope.sem"))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


# --------------------------------------------------------------------------
# 导出编排：已求解 → 只导出；未求解 → 先求解；产物按端口数认
# --------------------------------------------------------------------------
def test_export_uses_port_suffixed_artifact(tmp_path, monkeypatch):
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 FEM_2 Setup1(FEM)")

    def _no_solve(*a, **k):
        raise AssertionError("已求解的工程不应再求解")

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "run_laspcem_solve", _no_solve)
    monkeypatch.setattr(L, "_spawn_script", lambda exe, s: (None, "tmp.py", None))
    monkeypatch.setattr(L, "_results_present", lambda p: True)      # 结果目录不在本用例范围
    art = tmp_path / "p.s2p"
    art.write_text("! touchstone\n1.0 2.0 3.0\n", encoding="utf-8")
    monkeypatch.setattr(L, "_wait_for_artifact", lambda out, proc, t, before_sig=None: str(art))
    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is True
    assert r["output_path"].endswith("p.s2p")
    assert r["requested_output_path"].endswith("p.sNp")
    assert r["setup_name"] == "Setup1(FEM)"
    assert r["solution_name"] == "FEM_Setup1"
    assert r["solved_first"] is False


def test_export_unsolved_returns_solve_required(tmp_path, monkeypatch):
    """未求解工程：导出立刻报 SOLVE_REQUIRED（不起 GUI、不偷偷求解），并给出建议任务。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")            # 没有 SolutionId 记录
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "run_laspcem_solve",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("导出里不允许偷偷求解")))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_ for _ in ()).throw(
        AssertionError("未求解时不该启动 LASPCEM")))

    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_REQUIRED"
    assert r["suggested"]["tool"] == "run_laspcem_solve_async"
    assert r["suggested"]["arguments"]["then_export"] is True
    assert r["running_solve_task"] is None


def test_export_unsolved_with_stale_ids_still_requires_solve(tmp_path, monkeypatch):
    """未求解工程的 .sem 里仍可能残留 FEM_1/FEM_2 字符串（实测），不能因此当"已求解"。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"FEM_1 FEM_2 Setup1(FEM)")
    assert L.detect_solution_names(str(sem)) == ["FEM_1", "FEM_2"]
    assert L.is_solved(str(sem)) is False

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_ for _ in ()).throw(
        AssertionError("残留 id 不该让导出启动 LASPCEM")))

    r = L.export_laspcem_snp(str(sem))
    assert r["error_code"] == "SOLVE_REQUIRED"


def test_export_required_solve_reports_running_task(tmp_path, monkeypatch):
    """已有同工程的求解任务在跑时，SOLVE_REQUIRED 要回传 task_id，避免重复提交。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_running_solve_task_for", lambda p: "abc123")

    r = L.export_laspcem_snp(str(sem))
    assert r["error_code"] == "SOLVE_REQUIRED"
    assert r["running_solve_task"] == "abc123"


def test_report_unsolved_returns_solve_required(tmp_path, monkeypatch):
    """未求解工程建报告同样立刻报 SOLVE_REQUIRED，并建议 then_report。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "r.sem"
    sem.write_bytes(b"Setup1(FEM)")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_ for _ in ()).throw(
        AssertionError("未求解时不该启动 LASPCEM")))

    r = L.build_laspcem_report(str(sem))
    assert r["error_code"] == "SOLVE_REQUIRED"
    assert r["suggested"]["arguments"]["then_report"] is True


def test_explicit_wrong_setup_rejected_before_running(tmp_path, monkeypatch):
    """显式传错 setup 名 ⇒ 调用前就报 SETUP_NOT_FOUND，不再白等（消掉与"已最新跳过"的歧义）。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setups Setup1(FEM) Setup2(MoM)")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_ for _ in ()).throw(
        AssertionError("名字不对时不该启动 LASPCEM")))

    r = L.run_laspcem_solve(str(sem), setup_name="NoSuchSetup")
    assert r["success"] is False
    assert r["error_code"] == "SETUP_NOT_FOUND"
    assert "Setup1(FEM)" in r["details"]["setup_candidates"]

    # 正确名字（带/不带算法后缀、大小写不同）都不该被拦
    assert L._check_explicit_setup(str(sem), "Setup1") is None
    assert L._check_explicit_setup(str(sem), "Setup1(FEM)") is None
    assert L._check_explicit_setup(str(sem), "setup2") is None
    assert L._check_explicit_setup(str(sem), "") is None        # 不传 ⇒ 自动探测，不校验


def test_results_present_handles_nested_result_dirs(tmp_path):
    """实测结构：<stem>/1_Result/1_Result/Design_FEM.log 与 _Solver*.log 并存，要认出来。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    assert L._results_present(str(sem)) is False          # 还没有任何结果目录
    nested = tmp_path / "p" / "1_Result" / "1_Result"
    nested.mkdir(parents=True)
    (nested / "Design_FEM.log").write_text("Job is successfully finished !", encoding="utf-8")
    assert L._results_present(str(sem)) is True           # 深一层也要认
    (nested / "Design_FEM.log").unlink()
    assert L._results_present(str(sem)) is False


def test_solve_log_verdict(tmp_path):
    """求解日志里有没有 successfully finished 决定 verdict。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1")
    assert L._solve_log_verdict(str(sem)) == (None, "")   # 没日志
    d = tmp_path / "p" / "1_Result" / "1_Result"
    d.mkdir(parents=True)
    log = d / "Design_FEM.log"
    log.write_text("...\nJob is successfully finished !\n", encoding="utf-8")
    ok, path = L._solve_log_verdict(str(sem))
    assert ok is True and path.endswith("Design_FEM.log")
    log.write_text("...\nFATAL: diverged\n", encoding="utf-8")
    ok2, _ = L._solve_log_verdict(str(sem))
    assert ok2 is False


def test_snp_data_rows():
    import servers.laspcem.launcher as L

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "a.s2p"
        p.write_text("! header only\n# comment\n", encoding="utf-8")
        assert L._snp_data_rows(str(p)) == 0
        p.write_text("! h\n1.0 2.0 3.0 4.0\n2.0 3.0 4.0 5.0\n", encoding="utf-8")
        assert L._snp_data_rows(str(p)) == 2


def test_export_empty_artifact_rejected(tmp_path, monkeypatch):
    """产物只有表头（0 数据行）⇒ EXPORT_EMPTY，不报成功。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    art = tmp_path / "p.s2p"
    art.write_text("! touchstone only\n", encoding="utf-8")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_results_present", lambda p: True)
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (None, "t.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact", lambda o, p, t, before_sig=None: str(art))
    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "EXPORT_EMPTY"


def test_solve_incomplete_when_log_has_no_finished(tmp_path, monkeypatch):
    """求解器跑过但日志没有 finished ⇒ SOLVE_INCOMPLETE，不报 success。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")

    class _P:
        def poll(self):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": True, "timed_out": False, "solver_seconds": 100.0,
        "solver_processes": [{"pid": 1, "name": "mpiexec.exe"}], "sem_changed": True})
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (False, "x/Design_FEM.log"))
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_INCOMPLETE"


def test_build_import_aedt_script_matches_recording():
    """逐字照抄录制（1112.py）：New → 关 Untitled 0 → aedt.Import。"""
    s = build_import_aedt_script(r"C:\Users\JGL\Desktop\CPWG-1.aedt", r"C:\Users\JGL\Desktop")
    lines = [ln.strip() for ln in s.splitlines()]
    assert lines[0].startswith("#Version Information:")
    assert "app.New(0)" in lines
    assert 'guiframe3.CloseFile("Untitled 0")' in lines
    assert "'SourceFile':'C:/Users/JGL/Desktop/CPWG-1.aedt'," in lines
    assert "'OutputDir':'C:/Users/JGL/Desktop'," in lines
    assert "'AnsysEM':'2020.2'," in lines
    assert "'Background':True," in lines
    assert "'Compatible':True" in lines
    assert "app.SaveAs" not in s and "FEMAnalyze" not in s


def test_build_import_aedt_script_options():
    s = build_import_aedt_script(r"C:\d\a.aedt", ansys_version="2023.1",
                                 with_geometry_parameters=False,
                                 delete_operand_parts=False,
                                 delete_coordinate_systems=True,
                                 background=False, compatible=False)
    assert "'AnsysEM':'2023.1'," in s
    assert "'WithGeometryParameters':False," in s
    assert "'DeleteOperandParts':False," in s
    assert "'DeleteCoordinateSystems':True," in s
    assert "'Background':False," in s
    assert "'Compatible':False" in s


def test_import_aedt_reports_output(tmp_path, monkeypatch):
    """导入产出 .sem ⇒ success + sem_path + is_solved=False。"""
    import servers.laspcem.launcher as L

    aedt = tmp_path / "m.aedt"
    aedt.write_bytes(b"fake aedt")
    out = tmp_path / "out"
    out.mkdir()
    produced = out / "m-HFSSDesign1.sem"
    produced.write_bytes(b"Setup1")

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_new_sem", lambda d, b, p, t, **k: str(produced))
    monkeypatch.setattr(L, "is_solved", lambda p: False)
    r = L.import_aedt_as_sem(str(aedt), str(out))
    assert r["success"] is True
    assert r["sem_path"].endswith("m-HFSSDesign1.sem")
    assert r["is_solved"] is False
    assert r["sem_size"] > 0


def test_import_aedt_no_output(tmp_path, monkeypatch):
    import servers.laspcem.launcher as L

    aedt = tmp_path / "m.aedt"
    aedt.write_bytes(b"x")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (None, "t.py", None))
    monkeypatch.setattr(L, "_wait_for_new_sem", lambda d, b, p, t, **k: None)
    r = L.import_aedt_as_sem(str(aedt))
    assert r["success"] is False
    assert r["error_code"] == "IMPORT_NO_OUTPUT"


def test_import_aedt_ignores_preexisting_sem(tmp_path, monkeypatch):
    """OutputDir 里已有的旧 .sem 不能被当成本次导入产物。"""
    import servers.laspcem.launcher as L

    aedt = tmp_path / "m.aedt"
    aedt.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    (out / "old.sem").write_bytes(b"old")

    before = L._sem_snapshot(out)
    assert str(out / "old.sem") in before
    assert L._wait_for_new_sem(str(out), before, None, 0.05, poll=0.01) is None


def test_lock_path_and_cleanup(tmp_path, monkeypatch):
    """`~$<stem>.lock` 残留锁：无实例持有时删除；有实例持有时不动。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    lock = tmp_path / "~$p.lock"
    assert L._lock_path(str(sem)) == lock
    assert L._clear_stale_lock(str(sem)) is None          # 没有锁 ⇒ 什么都不做

    lock.write_bytes(b"")
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    assert L._clear_stale_lock(str(sem)) == str(lock)     # 无实例持有 ⇒ 删
    assert not lock.exists()

    lock.write_bytes(b"")
    monkeypatch.setattr(L, "_project_in_use", lambda p: [{"pid": 1}])
    assert L._clear_stale_lock(str(sem)) is None          # 有实例开着 ⇒ 保留
    assert lock.exists()


def test_solve_and_export_report_lock_cleanup(tmp_path, monkeypatch):
    """求解/导出返回里要能看出清掉了哪些残留锁。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    lock = tmp_path / "~$p.lock"
    lock.write_bytes(b"")

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": True, "timed_out": False, "solver_seconds": 5.0,
        "solver_processes": [], "sem_changed": True})
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (True, "x/Design_FEM.log"))
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is True
    assert r["stale_lock_removed"] and r["stale_lock_removed"][0].endswith("~$p.lock")
    assert not lock.exists()


def test_solve_persists_when_top_level_not_solved(tmp_path, monkeypatch):
    """求解后顶层 .sem 仍无解 ⇒ 补一次 SaveAs；补完 is_solved=True 才算成功。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")          # 顶层没有 SolutionId

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    state = {"solved": False}
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": True, "timed_out": False, "solver_seconds": 100.0,
        "solver_processes": [], "sem_changed": True})
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (None, ""))
    monkeypatch.setattr(L, "is_solved", lambda p: state["solved"])

    def _persist(path, exe, timeout_seconds=180):
        state["solved"] = True                # 模拟 SaveAs 把结果合并回去
        return {"saved": True, "is_solved": True, "elapsed_seconds": 4.2}

    monkeypatch.setattr(L, "_persist_solution", _persist)
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is True
    assert r["persist"]["saved"] is True


def test_solve_not_saved_when_persist_fails(tmp_path, monkeypatch):
    """补了 SaveAs 顶层仍无解 ⇒ SOLVE_NOT_SAVED，不报成功。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_solve", lambda p, t, **k: {
        "saw_solver": True, "timed_out": False, "solver_seconds": 100.0,
        "solver_processes": [], "sem_changed": True})
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (None, ""))
    monkeypatch.setattr(L, "is_solved", lambda p: False)
    monkeypatch.setattr(L, "_persist_solution",
                        lambda path, exe, timeout_seconds=180: {"saved": False})
    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_NOT_SAVED"


def test_build_save_script():
    """保存脚本：Open + SaveAs 回原路径，不含 Analyze/Exit。"""
    import servers.laspcem.launcher as L  # noqa: F401 — 保持与其它用例风格一致
    from servers.laspcem.script_gen import build_save_script

    s = build_save_script(r"C:\proj\x.sem")
    lines = [ln.strip() for ln in s.splitlines()]
    assert lines[0].startswith("#Version Information:")
    assert 'app.Open("C:/proj/x.sem")' in lines
    assert 'app.SaveAs("C:/proj/x.sem")' in lines
    assert "FEMAnalyze" not in s and "app.Exit" not in s


def test_extract_design_variables_two_layouts(tmp_path):
    """变量记录两种布局都要认（实测 TSV_SL 用 VariableName，CPW_SL 用 Variable）。"""
    from servers.laspcem.setup_reader import extract_design_variables

    def _rec(key: str, name: str) -> bytes:
        nb = name.encode()
        return (len(key).to_bytes(4, "little") + key.encode()
                + (2).to_bytes(4, "little")          # 类型
                + (26).to_bytes(4, "little")         # 记录长
                + len(nb).to_bytes(4, "little")      # 字符串长
                + nb)

    new_layout = tmp_path / "new.sem"
    new_layout.write_bytes(b"header" + _rec("VariableName", "R_TSV") + _rec("VariableName", "L1")
                           + _rec("VariableValue", "ignored"))
    assert extract_design_variables(str(new_layout)) == ["R_TSV", "L1"]

    old_layout = tmp_path / "old.sem"
    old_layout.write_bytes(b"header" + _rec("Variable", "L_CPW") + _rec("Variable", "t"))
    assert extract_design_variables(str(old_layout)) == ["L_CPW", "t"]

    empty = tmp_path / "empty.sem"
    empty.write_bytes(b"no variables here")
    assert extract_design_variables(str(empty)) == []


def test_import_aedt_removes_generated_py(tmp_path, monkeypatch):
    """导入后自动生成的同名建模脚本 `<stem>.py` 要删掉；用户原有同名脚本不动。"""
    import servers.laspcem.launcher as L

    aedt = tmp_path / "m.aedt"
    aedt.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_wait(d, before, proc, timeout, **kw):
        sem = Path(d) / "m-HFSSDesign1.sem"
        sem.write_bytes(b"Setup1")
        (Path(d) / "m-HFSSDesign1.py").write_text("# generated model script", encoding="utf-8")
        return str(sem)

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_new_sem", fake_wait)
    monkeypatch.setattr(L, "is_solved", lambda p: False)

    r = L.import_aedt_as_sem(str(aedt), str(out))
    assert r["success"] is True
    assert r["removed_scripts"] and r["removed_scripts"][0].endswith("m-HFSSDesign1.py")
    assert not (out / "m-HFSSDesign1.py").exists()          # 生成的脚本已删
    assert (out / "m-HFSSDesign1.sem").exists()             # .sem 保留


def test_import_aedt_keeps_preexisting_user_script(tmp_path, monkeypatch):
    """输出目录里本来就有的、与导入无关的 .py 不能被误删。"""
    import servers.laspcem.launcher as L

    aedt = tmp_path / "m.aedt"
    aedt.write_bytes(b"x")
    out = tmp_path / "out"
    out.mkdir()
    mine = out / "my_postprocess.py"
    mine.write_text("# 用户自己的脚本", encoding="utf-8")

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_wait(d, before, proc, timeout, **kw):
        sem = Path(d) / "m-HFSSDesign1.sem"
        sem.write_bytes(b"Setup1")
        return str(sem)

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "t.py", None))
    monkeypatch.setattr(L, "_wait_for_new_sem", fake_wait)
    monkeypatch.setattr(L, "is_solved", lambda p: False)

    r = L.import_aedt_as_sem(str(aedt), str(out))
    assert r["success"] is True
    assert r["removed_scripts"] == []
    assert mine.exists()


def test_export_proceeds_even_if_results_dir_missing(tmp_path, monkeypatch):
    """实测（BGA 工程）：结果目录被删也能导出 —— .sem 自带数据、打开时目录会重建。

    所以「结果目录缺失」只记录（`results_present_before=False`），不再拦导出。
    """
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    art = tmp_path / "p.s2p"
    spawned = {"n": 0}

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_spawn(exe, script):
        spawned["n"] += 1
        return _P(), "s.py", None

    def fake_wait(out, proc, timeout, before_sig=None, poll=1.0):
        art.write_text("! header\n1.0 2.0 3.0\n", encoding="utf-8")
        return str(art)

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_results_present", lambda p: False)
    monkeypatch.setattr(L, "_spawn_script", fake_spawn)
    monkeypatch.setattr(L, "_wait_for_artifact", fake_wait)

    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is True, r.get("message")
    assert spawned["n"] == 1                      # 确实去导出了
    assert r["results_present_before"] is False   # 事实被记录下来
    assert r["data_rows"] == 1


def test_project_in_use_blocks_solve(tmp_path, monkeypatch):
    """同一工程已被别的 LASPCEM 实例打开 ⇒ PROJECT_IN_USE，不再起第二个实例。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"Setup1(FEM)")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [{"pid": 4242, "project": str(p)}])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_ for _ in ()).throw(
        AssertionError("工程被占用时不应启动")))

    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "PROJECT_IN_USE"
    assert r["details"]["pids"] == [4242]


def test_export_reports_no_output(tmp_path, monkeypatch):
    """脚本跑完但没产物 ⇒ EXPORT_NO_OUTPUT，并带上脚本日志。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "_spawn_script", lambda exe, s: (None, "tmp.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact", lambda out, proc, t, before_sig=None: None)
    monkeypatch.setattr(L, "_results_present", lambda p: True)
    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "EXPORT_NO_OUTPUT"
    assert "script_log" in r.get("details", {})


def test_export_laspcem_snp_rejects_bad_extension(tmp_path):
    p = tmp_path / "foo.txt"
    p.write_text("x", encoding="utf-8")
    r = export_laspcem_snp(str(p))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


def test_build_laspcem_report_rejects_bad_path(tmp_path):
    r = build_laspcem_report(str(tmp_path / "nope.sem"))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


# --------------------------------------------------------------------------
# .sem 记录解析
# --------------------------------------------------------------------------
def test_is_solved(tmp_path):
    sem = tmp_path / "s.sem"
    sem.write_bytes(b"xxSolutionIdyy")
    assert is_solved(str(sem)) is True
    sem.write_bytes(b"hello world")
    assert is_solved(str(sem)) is False


def test_extract_design_variables(tmp_path):
    sem = tmp_path / "s.sem"
    sem.write_bytes(_mk("Variable", 2, b"L1") + _mk("Variable", 2, b"L2") + _mk("Variable", 2, b"L1"))
    assert extract_design_variables(str(sem)) == ["L1", "L2"]


# --------------------------------------------------------------------------
# .sem 记录格式：key_len(4) + key + type(4) + 0x18(4) + len(4) + value
# --------------------------------------------------------------------------
def _mk(key: str, vtype: int, value: bytes) -> bytes:
    kb = key.encode()
    return (len(kb).to_bytes(4, "little") + kb + vtype.to_bytes(4, "little")
            + (0x18).to_bytes(4, "little") + len(value).to_bytes(4, "little") + value)


def _mk_double(key: str, ref: int) -> bytes:
    kb = key.encode()
    return (len(kb).to_bytes(4, "little") + kb + (4).to_bytes(4, "little")
            + (0x18).to_bytes(4, "little") + b"\xff\xff\xff\xff"
            + b"\xff\xff\xff\x7f" + ref.to_bytes(4, "little") + b"\x00" * 12)


def test_extract_value_string_and_bool():
    data = _mk("ErrorTolerance", 1, b"0.5") + b"\x00" * 8 + _mk("Algorithm", 2, b"FEM")
    assert _extract_value(data, "ErrorTolerance") == "0.5"
    assert _extract_value(data, "Algorithm") == "FEM"


def test_extract_value_requires_length_prefix():
    """短键不得命中长键的子串（Frequency 曾从 SolutionFrequency 里误取值）。"""
    data = _mk("SolutionFrequency", 0, b"Points")
    assert _extract_value(data, "SolutionFrequency") == "Points"
    assert _extract_value(data, "Frequency") is None


def test_read_setup_report_flags_double_as_unresolved(tmp_path):
    """double 字段必须返回 null + 列入 unresolved_double_keys，不能返回次正规垃圾数。"""
    sem = tmp_path / "s.sem"
    sem.write_bytes(_mk("SweepType", 2, b"Interpolating") + _mk("RangeCount", 0, b"10")
                    + _mk_double("RangeStart", 92) + _mk_double("RangeEnd", 91)
                    + _mk("RangeType", 2, b"LinearStep"))
    r = read_setup_report(str(sem))
    assert r["setup"]["SweepType"] == "Interpolating"
    assert r["setup"]["RangeStart"] is None
    assert set(r["unresolved_double_keys"]) == {"RangeStart", "RangeEnd"}
    assert r["resolved_count"] == r["count"] - 2


def test_get_laspcem_setup_rejects_bad_path(tmp_path):
    r = get_laspcem_setup(str(tmp_path / "nope.sem"))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


# --------------------------------------------------------------------------
# open_laspcem_project：LASPCEM 无进程间投递 → 同工程去重
# --------------------------------------------------------------------------
def test_open_laspcem_project_reuses_instance_with_same_project(tmp_path, monkeypatch):
    sem = tmp_path / "demo.sem"
    sem.write_text("x", encoding="utf-8")
    import servers.laspcem.launcher as L

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "find_laspcem_processes", lambda: [
        {"pid": 4242, "exe": r"D:\fake\laspcem.exe", "project": str(sem), "cmdline": ""},
    ])

    def _boom(*a, **k):
        raise AssertionError("该工程已打开，不应再启动新实例")

    monkeypatch.setattr(L.subprocess, "Popen", _boom)
    r = L.open_laspcem_project(str(sem))
    assert r["success"] is True
    assert r["reused_existing"] is True
    assert r["pid"] == 4242


def test_open_laspcem_project_starts_new_instance_for_unopened_project(tmp_path, monkeypatch):
    sem = tmp_path / "demo.sem"
    sem.write_text("x", encoding="utf-8")
    import servers.laspcem.launcher as L

    class _Child:
        pid = 7777

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "find_laspcem_processes", lambda: [
        {"pid": 111, "exe": r"D:\fake\laspcem.exe", "project": "", "cmdline": ""},
    ])
    starts = []

    def _popen(args, **k):
        starts.append(args)
        return _Child()

    monkeypatch.setattr(L.subprocess, "Popen", _popen)
    r = L.open_laspcem_project(str(sem))
    assert r["success"] is True
    assert r["new_instance"] is True
    assert starts and starts[0][1] == str(sem)


def test_open_laspcem_project_force_new_instance(tmp_path, monkeypatch):
    sem = tmp_path / "demo.sem"
    sem.write_text("x", encoding="utf-8")
    import servers.laspcem.launcher as L

    class _Child:
        pid = 8888

    monkeypatch.setattr(L, "_resolve_exe", lambda p: (r"D:\fake\laspcem.exe", None))
    monkeypatch.setattr(L, "find_laspcem_processes", lambda: [
        {"pid": 4242, "exe": r"D:\fake\laspcem.exe", "project": str(sem), "cmdline": ""},
    ])
    monkeypatch.setattr(L.subprocess, "Popen", lambda args, **k: _Child())
    r = L.open_laspcem_project(str(sem), force_new_instance=True)
    assert r["new_instance"] is True
    assert r["pid"] == 8888


def test_wait_for_solve_times_out(tmp_path, monkeypatch):
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"x")
    monkeypatch.setattr(L, "_solver_processes", list)
    r = L._wait_for_solve(str(sem), timeout_seconds=0, poll=0.01)
    assert r["timed_out"] is True
    assert r["saw_solver"] is False


def test_detect_port_count_from_touchstone(tmp_path):
    """端口数从同目录 Touchstone 后缀推断；没有产物时兜底 2。"""
    from servers.laspcem.setup_reader import detect_port_count

    sem = tmp_path / "x.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    assert detect_port_count(str(sem)) == 2          # 无产物 ⇒ 兜底 2

    (tmp_path / "x.s4p").write_text("! 4 port\n", encoding="utf-8")
    assert detect_port_count(str(sem)) == 4

    (tmp_path / "x.s2p").write_text("! 2 port\n", encoding="utf-8")
    assert detect_port_count(str(sem)) == 4          # 取最大，不被旧的 2 端口产物拉低


def test_report_script_quantities_follow_port_count():
    """端口数决定 Quantity：2 端口保持原样，4 端口出 16 项（不能写死）。"""
    import re

    from servers.laspcem.script_gen import build_report_script

    def quantities(n):
        sc = build_report_script("C:/x/a.sem", setup_name="Setup1",
                                 solution_name="FEM_Setup1", port_count=n)
        body = re.search(r"'Quantity': \[(.*?)\]", sc, re.DOTALL).group(1)
        return re.findall(r"'([^']+)'", body)

    assert quantities(2) == ["S(1:1,1:1)", "S(2:1,1:1)", "S(1:1,2:1)", "S(2:1,2:1)"]
    q4 = quantities(4)
    assert len(q4) == 16
    assert q4[0] == "S(1:1,1:1)" and q4[3] == "S(4:1,1:1)" and q4[-1] == "S(4:1,4:1)"


def test_report_passes_port_count(tmp_path, monkeypatch):
    """build_laspcem_report 把端口数传给脚本生成，并在返回体里回传。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "r.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    seen = {}

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_build(sem_path, **kw):
        seen.update(kw)
        return "script"

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "build_report_script", fake_build)
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact", lambda *a, **k: None)

    r = L.build_laspcem_report(str(sem), port_count=4)
    assert seen.get("port_count") == 4
    # verified=False（脚本没让工程改写）现在如实报错，port_count 放在 details 里供排查
    assert r.get("error_code") == "REPORT_NOT_VERIFIED", r
    assert (r.get("details") or {}).get("port_count") == 4, r


def test_wait_for_solve_detects_stall(tmp_path, monkeypatch):
    """求解器还在但日志/工程长时间没进展 ⇒ stalled（不再干等到 timeout）。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "s.sem"
    sem.write_bytes(b"SolutionId FEM_1")
    monkeypatch.setattr(L, "_solver_processes",
                        lambda: [{"pid": 1, "name": "phofem.exe", "cmdline": "x"}])
    monkeypatch.setattr(L, "_progress_sig", lambda p: (100, (5, 1.0)))

    out = L._wait_for_solve(str(sem), timeout_seconds=60, poll=0.01, stall_seconds=0.2)
    assert out["stalled"] is True
    assert out["timed_out"] is False
    assert out["stalled_seconds"] > 0


def test_stall_disabled_still_times_out(tmp_path, monkeypatch):
    """stall_seconds=0 ⇒ 关闭卡死检测，按原逻辑等 timeout。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "s2.sem"
    sem.write_bytes(b"SolutionId FEM_1")
    monkeypatch.setattr(L, "_solver_processes",
                        lambda: [{"pid": 1, "name": "phofem.exe", "cmdline": "x"}])
    monkeypatch.setattr(L, "_progress_sig", lambda p: (100, (5, 1.0)))

    out = L._wait_for_solve(str(sem), timeout_seconds=0.3, poll=0.01, stall_seconds=0)
    assert out["timed_out"] is True
    assert not out.get("stalled")


def test_solve_reports_stalled_and_kills_solvers(tmp_path, monkeypatch):
    """卡死时返回 SOLVE_STALLED，并清理求解器进程。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "s3.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    killed = {}

    class _P:
        returncode = 0

        def poll(self):
            return 0

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_solve",
                        lambda *a, **k: {"saw_solver": True, "timed_out": False,
                                         "stalled": True, "stalled_seconds": 901.0,
                                         "solver_seconds": 950.0, "solver_processes": [],
                                         "sem_changed": False})
    monkeypatch.setattr(L, "_kill_solver_procs",
                        lambda p, pids=None: killed.setdefault("pids", pids or [111, 222]))
    monkeypatch.setattr(L, "_kill_orphan_mpi_since", lambda since: [])
    monkeypatch.setattr(L, "_solve_log_verdict", lambda p: (None, ""))

    r = L.run_laspcem_solve(str(sem))
    assert r["success"] is False
    assert r["error_code"] == "SOLVE_STALLED"
    assert killed.get("pids") == [111, 222]


def test_stall_ignores_running_cpu(tmp_path, monkeypatch):
    """求解器 CPU 时间在涨（插值扫频阶段不写日志）⇒ 不算卡死。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "cpu.sem"
    sem.write_bytes(b"SolutionId FEM_1")
    monkeypatch.setattr(L, "_solver_processes",
                        lambda: [{"pid": 1, "name": "phofem.exe", "cmdline": "x"}])
    monkeypatch.setattr(L, "_progress_sig", lambda p: (100, (5, 1.0)))

    ticks = {"n": 0.0}

    def rising_cpu(_solvers):
        ticks["n"] += 5.0          # 每次采样 CPU 涨 5s ⇒ 一直在算
        return ticks["n"]

    monkeypatch.setattr(L, "_solver_cpu_total", rising_cpu)

    out = L._wait_for_solve(str(sem), timeout_seconds=0.8, poll=0.01, stall_seconds=0.2)
    assert not out.get("stalled"), out
    assert out["timed_out"] is True


def test_stall_triggers_when_cpu_idle(tmp_path, monkeypatch):
    """日志不动 + CPU 不涨 ⇒ 判卡死。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "idle.sem"
    sem.write_bytes(b"SolutionId FEM_1")
    monkeypatch.setattr(L, "_solver_processes",
                        lambda: [{"pid": 1, "name": "phofem.exe", "cmdline": "x"}])
    monkeypatch.setattr(L, "_progress_sig", lambda p: (100, (5, 1.0)))
    monkeypatch.setattr(L, "_solver_cpu_total", lambda solvers: 42.0)   # 恒定

    out = L._wait_for_solve(str(sem), timeout_seconds=30, poll=0.01, stall_seconds=0.2)
    assert out.get("stalled") is True


def _fake_proc():
    class _P:
        returncode = 1
        def poll(self): return 1
        def terminate(self): pass
        def wait(self, timeout=None): return 1
    return _P()


def test_export_short_waits_when_results_missing(tmp_path, monkeypatch):
    """有解记录但结果目录空 ⇒ 最多等 30 s（不白等满 timeout），并给出"结果失效"归因。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    seen = {}

    def fake_wait(out, proc, timeout, before_sig=None, poll=1.0):
        seen["timeout"] = timeout

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_results_present", lambda p: False)      # 结果目录空
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_fake_proc(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact", fake_wait)

    r = L.export_laspcem_snp(str(sem), timeout_seconds=600)
    assert r["error_code"] == "EXPORT_NO_OUTPUT"
    assert seen["timeout"] == 30                    # 缩到 30 s
    assert r["waited_seconds"] == 30
    assert "结果已失效" in r["message"] or "error 892" in r["message"]
    assert "892" in r["hint"] and "167" in r["hint"]     # 2026-11-13 实测为 892，旧版 167


def test_export_uses_full_timeout_when_results_present(tmp_path, monkeypatch):
    """结果目录正常时仍按调用方给的 timeout 等（不误缩）。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "q.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    seen = {}

    def fake_wait(out, proc, timeout, before_sig=None, poll=1.0):
        seen["timeout"] = timeout

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_results_present", lambda p: True)
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_fake_proc(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact", fake_wait)

    r = L.export_laspcem_snp(str(sem), timeout_seconds=600)
    assert r["error_code"] == "EXPORT_NO_OUTPUT"
    assert seen["timeout"] == 600


def test_dir_stat_counts_files_and_bytes(tmp_path):
    """_dir_stat 只读统计：存在性/文件数/字节数，且会按深度截断。"""
    import servers.laspcem.launcher as L

    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.log").write_bytes(b"x" * 10)
    (d / "sub").mkdir()
    (d / "sub" / "b.log").write_bytes(b"y" * 20)

    st = L._dir_stat(d)
    assert st["exists"] is True and st["files"] == 2 and st["bytes"] == 30

    missing = L._dir_stat(tmp_path / "nope")
    assert missing == {"exists": False, "files": 0, "bytes": 0}

    deep = L._dir_stat(d, max_depth=1)          # 只统计根一层
    assert deep["files"] == 1

    capped = L._dir_stat(d, cap_files=1)
    assert capped["files"] == 1 and capped.get("truncated") is True


def test_results_dir_points_to_sibling_folder(tmp_path):
    import servers.laspcem.launcher as L

    sem = tmp_path / "x.sem"
    sem.write_bytes(b"Sol")
    assert L._results_dir(str(sem)) == tmp_path / "x"


def test_project_state_three_cases(tmp_path):
    """三态判定：unsolved / results_missing / solved_ok + 各自的 next_action。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "p.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")

    assert L._project_state(str(sem), results_present=False)["state"] == "results_missing"
    assert L._project_state(str(sem), results_present=True)["state"] == "solved_ok"
    assert L._project_state(str(sem), results_present=True)["next_action"] == "export_laspcem_snp"

    unsolved = tmp_path / "u.sem"
    unsolved.write_bytes(b"Setup1(FEM)")        # 没有 SolutionId
    st = L._project_state(str(unsolved), results_present=False)
    assert st["state"] == "unsolved" and st["next_action"] == "run_laspcem_solve_async"


def test_get_setup_reports_state_and_results_dir(tmp_path):
    """get_laspcem_setup 要提前暴露 results_present / state / results_dir。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "g.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    r = L.get_laspcem_setup(str(sem))
    assert r["success"] is True
    assert r["state"] in ("solved_ok", "results_missing")
    assert "results_present" in r and "results_dir" in r
    assert r["results_dir"]["exists"] is False         # 没建目录
    assert r["next_action"] in ("export_laspcem_snp", "run_laspcem_solve_async")


def test_export_reports_results_dir_audit(tmp_path, monkeypatch):
    """导出返回体带结果目录前后快照，能证明我们没删/没建。"""
    import servers.laspcem.launcher as L

    sem = tmp_path / "a.sem"
    sem.write_bytes(b"SolutionId FEM_1 Setup1(FEM)")
    art = tmp_path / "a.s2p"

    class _P:
        returncode = 0
        def poll(self): return 0
        def terminate(self): pass
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(L, "_resolve_exe", lambda p: ("x", None))
    monkeypatch.setattr(L, "_project_in_use", lambda p: [])
    monkeypatch.setattr(L, "_results_present", lambda p: True)
    monkeypatch.setattr(L, "_spawn_script", lambda e, s: (_P(), "s.py", None))
    monkeypatch.setattr(L, "_wait_for_artifact",
                        lambda *a, **k: (art.write_text("! h\n1 2 3\n", encoding="utf-8"), str(art))[1])

    r = L.export_laspcem_snp(str(sem))
    assert r["success"] is True
    assert r["results_dir_before"] == {"exists": False, "files": 0, "bytes": 0}
    assert "results_dir_after" in r
    assert r["results_dir_changed"] in (True, False)


def test_import_fails_fast_when_script_log_has_traceback(tmp_path):
    """脚本日志出现 Traceback ⇒ 立刻返回 None（不白等 timeout）。"""
    import time as _t

    import servers.laspcem.launcher as L

    out = tmp_path / "out"
    out.mkdir()
    log = tmp_path / "script.log"
    log.write_text("BEGIN import\nTraceback (most recent call last):\n"
                   "Exception: Import AEDT file failed\n", encoding="utf-8")

    t0 = _t.time()
    got = L._wait_for_new_sem(str(out), {}, None, 60, poll=0.01, script_log=str(log))
    elapsed = _t.time() - t0
    assert got is None
    assert elapsed < 10, elapsed          # 立刻失败，不等 60 s


def test_import_waits_when_script_log_clean(tmp_path):
    """日志没有异常时仍按 timeout 等待（不要误判成失败）。"""
    import time as _t

    import servers.laspcem.launcher as L

    out = tmp_path / "out2"
    out.mkdir()
    log = tmp_path / "ok.log"
    log.write_text("BEGIN import\nOK import\n", encoding="utf-8")

    t0 = _t.time()
    got = L._wait_for_new_sem(str(out), {}, None, 0.3, poll=0.01, script_log=str(log))
    assert got is None
    assert _t.time() - t0 >= 0.3
