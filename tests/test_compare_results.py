"""测试 compare_simulation_results 的对齐与 CSV 导出逻辑。

回归：修复 range(n) 未定义 bug（应使用 file_count）。
"""
from pathlib import Path
from types import SimpleNamespace



def _setup(tmp_path, monkeypatch, x_values=(1.0, 2.0, 3.0)):
    """构造满足入口校验的最小环境，mock turbocharts 导出与 CSV 读取。"""
    from servers.turbocharts import compare_results as cr

    # 入口校验：turbocharts 可执行文件 + 两个 RAW 文件均需存在
    tc = tmp_path / "tc.exe"
    tc.write_text("")
    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", str(tc))

    raw_a = tmp_path / "a.raw"
    raw_b = tmp_path / "b.raw"
    raw_a.write_text("")
    raw_b.write_text("")

    monkeypatch.setattr(
        cr, "run_turbocharts",
        lambda cmd, timeout_seconds=60: SimpleNamespace(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        cr, "_read_curve_csv_xy",
        lambda path: (list(x_values), [10.0, 11.0, 12.0]),
    )
    # 前置校验会调 resolve_curves（→ 引擎 --curve-info）；离线 mock，否则老测试会真去
    # spawn 引擎进程（convert_raw 里的 run_turbocharts/TURBOCHARTS_PATH 是独立名字，上面
    # 只 patch 了 compare_results 的）。个别测试要测这条路径时再自己覆盖本 patch。
    monkeypatch.setattr(cr, "resolve_curves", lambda p: {"error_code": "CURVE_INFO_FAILED"})
    return cr, raw_a, raw_b


def test_compare_intersection_regression(tmp_path, monkeypatch):
    """intersection 对齐 + CSV 导出应正常返回，不抛 NameError。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)

    img = tmp_path / "cmp.png"
    csv = tmp_path / "cmp.csv"
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(img),
        csv_path=str(csv),
        labels=["a", "b"],
    )

    assert result["success"] is True
    assert len(result["series"]) == 2
    assert len(result["metrics"]) == 1
    assert img.exists()
    assert csv.exists()
    # CSV 表头应包含每个 label 的曲线列
    header = csv.read_text(encoding="utf-8").splitlines()[0]
    assert "a_DB_S[2,1]" in header and "b_DB_S[2,1]" in header


def test_compare_interpolation_requires_increasing_reference(tmp_path, monkeypatch):
    """interpolation 模式要求参考文件 X 轴严格递增，否则返回 INVALID_RAW_DATA。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch, x_values=(1.0, 1.0, 2.0))

    img = tmp_path / "cmp.png"
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(img),
        alignment="interpolation",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_RAW_DATA"


def test_compare_timeout_returns_error(tmp_path, monkeypatch):
    """run_turbocharts 超时抛 RuntimeError 时应返回 TOOL_TIMEOUT，而非崩溃。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)

    def _raise(cmd, timeout_seconds=60):
        raise RuntimeError("Turbocharts 执行超时（60 秒）")
    monkeypatch.setattr(cr, "run_turbocharts", _raise)

    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["success"] is False
    assert result["error_code"] == "TOOL_TIMEOUT"


def test_compare_missing_csv_dir_returns_error(tmp_path, monkeypatch):
    """csv_path 父目录不存在时应返回 OUTPUT_DIRECTORY_NOT_FOUND，而非 FileNotFoundError。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)

    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
        csv_path=str(tmp_path / "nonexistent_dir" / "cmp.csv"),
    )
    assert result["success"] is False
    assert result["error_code"] == "OUTPUT_DIRECTORY_NOT_FOUND"


def _write_mds_raw(path, plotname):
    """写一个最小 MDS 头（detect_chart_type 只读 Plotname / 变量行）。"""
    path.write_text(
        "File Format: MDS\n"
        f"Plotname: {plotname}\n"
        "No. Variables: 2\n"
        "Variables:\n"
        " 0 freq frequency type=real indep=yes\n"
        " 1 Out1 dBm type=real indep=no\n"
        "Values:\n"
        "1\n"
        "2\n",
        encoding="utf-8",
    )
    return path


def test_compare_auto_detects_chart_type_from_plotname(tmp_path, monkeypatch):
    """GW-2：不传 chart_type 时按 RAW 的 Plotname 自动识别（HB 数据不再被当 SP 白跑）。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    _write_mds_raw(raw_a, "HB HB1[1] freq=(1 GHz->2 GHz)")
    _write_mds_raw(raw_b, "HB HB1[1] freq=(1 GHz->2 GHz)")

    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="dBm_out",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["success"] is True
    assert result["chart_type"] == "HB"
    assert "warning" not in result


def test_compare_chart_type_falls_back_to_sp_with_warning(tmp_path, monkeypatch):
    """识别不出时退回旧默认 SP，但必须带 warning —— 不静默。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)  # 空 RAW，没有 Plotname
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["chart_type"] == "SP"
    assert "warning" in result


def test_compare_mixed_dataset_types_is_flagged(tmp_path, monkeypatch):
    """混用 SP + HB 文件时：chart_type 取首个文件，其余文件的类型不一致要写进 warning。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    _write_mds_raw(raw_a, "SP SP1[1] freq=(1 GHz->2 GHz)")
    _write_mds_raw(raw_b, "HB HB1[1] freq=(1 GHz->2 GHz)")

    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[1,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["chart_type"] == "SP"        # 首个文件是 SP
    assert "warning" in result
    assert "不一致" in result["warning"]
    assert "b.raw(HB)" in result["warning"]


def test_compare_failure_message_names_file_plotname(tmp_path, monkeypatch):
    """导出失败时，若该文件自己的 Plotname 与 chart_type 不一致，要直接点出来。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    _write_mds_raw(raw_a, "SP SP1[1] freq=(1 GHz->2 GHz)")
    _write_mds_raw(raw_b, "HB HB1[1] freq=(1 GHz->2 GHz)")
    calls = {"n": 0}

    def fake_run(cmd, timeout_seconds=60):
        calls["n"] += 1
        # 第一个文件（SP，与 chart_type 一致）正常导出；第二个（HB，不一致）按实测崩在 0xC0000005
        return SimpleNamespace(returncode=0 if calls["n"] == 1 else 3221225477,
                               stderr="", stdout="")

    monkeypatch.setattr(cr, "run_turbocharts", fake_run)
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[1,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["error_code"] == "TOOL_EXECUTION_FAILED"
    assert "rc=3221225477" in result["message"]
    assert "该 RAW 的 Plotname=HB" in result["message"]   # 只在该文件类型与 chart_type 不同时加


def test_compare_failure_message_carries_rc_and_stdout(tmp_path, monkeypatch):
    """GW-2：引擎失败且 stderr 为空时，message 要带 rc + stdout 兜底（原来冒号后为空）。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cr, "run_turbocharts",
        lambda cmd, timeout_seconds=60: SimpleNamespace(returncode=1, stderr="", stdout="引擎：未找到曲线"),
    )
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["error_code"] == "TOOL_EXECUTION_FAILED"
    assert "rc=1" in result["message"]
    assert "未找到曲线" in result["message"]
def test_command_runs_recorded_per_raw(tmp_path, monkeypatch):
    """compare：每个 RAW 一次的引擎调用逐条回传（引擎多数失败零输出，故要留痕）。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)], curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    runs = result["command_runs"]
    assert len(runs) == 2
    assert all(set(r) == {"command", "return_code", "stdout", "stderr", "ok"} for r in runs)
    assert all(r["return_code"] == 0 for r in runs)
    assert "--raw" in runs[0]["command"]


def test_command_runs_in_failure_details(tmp_path, monkeypatch):
    """导出失败时 details.command_runs 要含失败那条的真实 rc 与输出。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake(cmd, timeout_seconds=60):
        calls["n"] += 1
        return SimpleNamespace(returncode=0 if calls["n"] == 1 else 7,
                               stderr="引擎: 无数据", stdout="")

    monkeypatch.setattr(cr, "run_turbocharts", fake)
    result = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)], curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["error_code"] == "TOOL_EXECUTION_FAILED"
    runs = result["details"]["command_runs"]
    assert [r["return_code"] for r in runs] == [0, 7]
    assert runs[1]["stderr"] == "引擎: 无数据"


def test_compare_rejects_bad_linename_before_running_engine(tmp_path, monkeypatch):
    """括号形式会让 exe 段错误（实测 rc=0xC0000005）⇒ 必须在启动前拦下并给出可用写法。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        cr, "run_turbocharts",
        lambda cmd, timeout_seconds=60: (calls.append(cmd),
                                         SimpleNamespace(returncode=0, stderr=""))[1])
    monkeypatch.setattr(cr, "resolve_curves", lambda p: {
        "chart_type": "SP", "curves": {}, "usable_linenames": ["db_S[1,1]"]})
    r = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)], curve="DB(S11)",
        output_path=str(tmp_path / "bad.png"))
    assert r["error_code"] == "INVALID_LINENAME", r
    assert (r.get("details") or {}).get("available_curves") == ["db_S[1,1]"], r
    assert calls == [], "语法非法的曲线名不应启动引擎"


def test_compare_reports_available_curves_when_csv_empty(tmp_path, monkeypatch):
    """语法合法、但该 RAW 没有对应数据时，报错要列出可用写法。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cr, "_read_curve_csv_xy", lambda path: ([], []))
    monkeypatch.setattr(cr, "resolve_curves", lambda p: {
        "chart_type": "SP",
        "curves": {"freq": [{"S[1,1]": ["db", "APS"]}]},
        "usable_linenames": ["db_S[1,1]"],
    })
    # 变量必须存在，否则会被前置的存在性校验拦下（那是另一条路径）
    r = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)], curve="db_S[1,1]",
        output_path=str(tmp_path / "bad2.png"))
    assert r["error_code"] == "INVALID_RAW_DATA", r
    # error_response 的额外字段统一进 details（与 turbocharts_convert 的 known_variables 一致）
    assert (r.get("details") or {}).get("available_curves") == ["db_S[1,1]"], r
    assert "db_S[1,1]" in r["message"]


def test_compare_rejects_missing_variable_before_running_engine(tmp_path, monkeypatch):
    """变量不存在时 exe 同样段错误（实测 dBm_Out9 → rc=0xC0000005）⇒ 必须前置拦下。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        cr, "run_turbocharts",
        lambda cmd, timeout_seconds=60: (calls.append(cmd),
                                        SimpleNamespace(returncode=0, stderr=""))[1])
    monkeypatch.setattr(cr, "resolve_curves", lambda p: {
        "chart_type": "SP",
        "curves": {"freq": [{"S[1,1]": ["db"]}, {"S[2,1]": ["db"]}]},
        "usable_linenames": ["db_S[1,1]", "db_S[2,1]"],
    })
    r = cr.compare_simulation_results(
        raw_paths=[str(raw_a), str(raw_b)], curve="dBm_Out9",
        output_path=str(tmp_path / "miss.png"))
    assert r["error_code"] == "CURVE_NOT_FOUND", r
    assert (r.get("details") or {}).get("available_curves") == ["db_S[1,1]", "db_S[2,1]"], r
    assert "S[1,1]" in r["message"], r      # 报错里带该 RAW 实测变量
    assert calls == [], "变量不存在时不应启动引擎"
