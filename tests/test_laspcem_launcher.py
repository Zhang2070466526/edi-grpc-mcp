"""LASPCEM 工具 — 路径解析、参数校验、脚本生成、setup 读取的最小回归。"""
from servers.laspcem.config import _first_quoted_arg
from servers.laspcem.launcher import open_laspcem_project, run_laspcem_simulation, get_laspcem_setup
from servers.laspcem.script_gen import build_laspcem_script
from servers.laspcem.setup_reader import _extract_value, read_setup_report


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


def test_build_laspcem_script_general():
    s = build_laspcem_script(r"C:\proj\CPW.sem", setup_name="Setup2")
    assert 'app.Open("C:/proj/CPW.sem")' in s            # 反斜杠转正斜杠
    assert s.count("C:/proj/CPW.sem") == 2               # Open + SaveAs
    assert "cem.FEMAnalyze('Setup2'," in s               # setup 名可传
    assert "cem.EditSolution" not in s                    # 通用脚本不含项目录制 setup
    assert "app.SaveAs" in s


def test_run_laspcem_simulation_rejects_bad_path(tmp_path):
    r = run_laspcem_simulation(str(tmp_path / "nope.sem"), open_project=False)
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


def test_run_laspcem_simulation_rejects_bad_extension(tmp_path):
    p = tmp_path / "foo.txt"
    p.write_text("x", encoding="utf-8")
    r = run_laspcem_simulation(str(p), open_project=False)
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


def test_run_laspcem_simulation_writes_script(tmp_path):
    sem = tmp_path / "demo.sem"
    sem.write_text("x", encoding="utf-8")
    r = run_laspcem_simulation(str(sem), open_project=False)
    assert r["success"] is True
    assert r["script_path"].endswith("demo_run.py")
    assert (tmp_path / "demo_run.py").is_file()
    assert r["project_opened"] is False


# --------------------------------------------------------------------------
# .sem 记录格式：key_len(4) + key + type(4) + 0x18(4) + len(4) + value
# --------------------------------------------------------------------------
def _mk(key: str, vtype: int, value: bytes) -> bytes:
    """按真实 .sem 布局拼一条「长度前缀 + key + 类型 + 0x18 + 长度 + 值」记录。"""
    kb = key.encode()
    return (len(kb).to_bytes(4, "little") + kb + vtype.to_bytes(4, "little")
            + (0x18).to_bytes(4, "little") + len(value).to_bytes(4, "little") + value)


def _mk_double(key: str, ref: int) -> bytes:
    """按真实 .sem 布局拼一条 double 记录（记录内只有引用序号，没有 8 字节数值）。"""
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
    assert r["setup"]["RangeCount"] == "10"
    assert r["setup"]["RangeStart"] is None
    assert set(r["unresolved_double_keys"]) == {"RangeStart", "RangeEnd"}
    assert r["count"] == len(r["setup"])
    assert r["resolved_count"] == r["count"] - 2


def test_get_laspcem_setup_rejects_bad_path(tmp_path):
    r = get_laspcem_setup(str(tmp_path / "nope.sem"))
    assert r["success"] is False
    assert r["error_code"] == "INVALID_PATH"


# --------------------------------------------------------------------------
# open_laspcem_project：LASPCEM 无进程间投递 → 同工程去重，避免重复起实例
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
    assert r["new_instance"] is False
    assert r["pid"] == 4242
    assert r["running_instances"] == [4242]


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
    assert r["reused_existing"] is False
    assert r["pid"] == 7777
    assert r["running_instances"] == [111, 7777]
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
    assert r["reused_existing"] is False
    assert r["pid"] == 8888
