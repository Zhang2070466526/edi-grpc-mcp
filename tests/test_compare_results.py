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
    return cr, raw_a, raw_b


def test_compare_intersection_regression(tmp_path, monkeypatch):
    """intersection 对齐 + CSV 导出应正常返回，不抛 NameError。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)

    img = tmp_path / "cmp.png"
    csv = tmp_path / "cmp.csv"
    result = cr.compare_simulation_results(
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["success"] is False
    assert result["error_code"] == "TOOL_TIMEOUT"


def test_compare_missing_csv_dir_returns_error(tmp_path, monkeypatch):
    """csv_path 父目录不存在时应返回 OUTPUT_DIRECTORY_NOT_FOUND，而非 FileNotFoundError。"""
    cr, raw_a, raw_b = _setup(tmp_path, monkeypatch)

    result = cr.compare_simulation_results(
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
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
        result_paths=[str(raw_a), str(raw_b)],
        curve="DB_S[2,1]",
        output_path=str(tmp_path / "cmp.png"),
    )
    assert result["error_code"] == "TOOL_EXECUTION_FAILED"
    assert "rc=1" in result["message"]
    assert "未找到曲线" in result["message"]
