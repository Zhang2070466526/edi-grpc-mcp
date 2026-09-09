"""测试 compare_simulation_results 的对齐与 CSV 导出逻辑。

回归：修复 range(n) 未定义 bug（应使用 file_count）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))


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
