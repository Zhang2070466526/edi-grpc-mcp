"""snp_compare 的回归：Touchstone 三格式解析、端口数自洽、参考阻抗重归一化（round-trip）。"""
import numpy as np
import pytest

from servers.snp_compare import (
    align_freqs,
    parse_touchstone,
    read_laspcem_z0,
    renormalize,
    s_to_db,
)

MA_FILE = """! test
# GHZ S MA R 50
1.0 0.5 90 0.9 0 0.9 0 0.1 0
2.0 0.4 90 0.8 0 0.8 0 0.2 0
"""

RI_FILE = """# MHz S RI R 75
1000 0.3 0.4 0.5 0.6 0.5 0.6 0.7 0.8
"""

DB_FILE = """# GHz S DB R 50
1.0 -20 0 -6 0 -6 0 -30 0
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_parse_ma_and_port_ordering(tmp_path):
    d = parse_touchstone(_write(tmp_path, "a.s2p", MA_FILE))
    assert d["fmt"] == "MA" and d["nports"] == 2 and d["z0"] == 50.0
    assert np.allclose(d["freqs"], [1.0, 2.0])
    s = d["s"]
    # Touchstone 按列存：S11 S21 S12 S22
    assert np.isclose(s[0, 0, 0], 0.5j)
    assert np.isclose(s[0, 0, 1], 0.9)   # S21 落在 S[i=1][j=0] 的转置位置
    assert np.isclose(s[0, 1, 0], 0.9)
    assert np.isclose(s[0, 1, 1], 0.1)
    assert np.isclose(s_to_db(s, 0, 1)[0], 20 * np.log10(0.9))


def test_parse_ri_with_mhz_and_z0(tmp_path):
    d = parse_touchstone(_write(tmp_path, "b.s2p", RI_FILE))
    assert d["fmt"] == "RI"
    assert d["z0"] == 75.0
    assert np.isclose(d["freqs"][0], 1.0)          # 1000 MHz → 1 GHz
    assert np.isclose(d["s"][0, 0, 0], 0.3 + 0.4j)
    assert np.isclose(d["s"][0, 0, 1], 0.5 + 0.6j)


def test_parse_db(tmp_path):
    d = parse_touchstone(_write(tmp_path, "c.s2p", DB_FILE))
    assert d["fmt"] == "DB"
    assert np.isclose(abs(d["s"][0, 0, 0]), 0.1)   # -20 dB
    assert np.isclose(abs(d["s"][0, 1, 1]), 10 ** (-30 / 20))


def test_port_count_must_match_suffix(tmp_path):
    """后缀写了 .s3p 但数据是 2 端口 ⇒ 必须报错，不能静默读歪。"""
    with pytest.raises(ValueError, match="端口数与文件名不符"):
        parse_touchstone(_write(tmp_path, "d.s3p", MA_FILE))


def test_renormalize_identity():
    rng = np.random.default_rng(7)
    s = 0.1 * (rng.standard_normal((5, 2, 2)) + 1j * rng.standard_normal((5, 2, 2)))
    out = renormalize(s, 50.0, 50.0)
    assert np.allclose(out, s, atol=1e-12)


def test_renormalize_roundtrip():
    """52.66/45.05 Ω → 50 Ω → 再回 52.66/45.05 Ω 必须还原（独立于实现的守恒校验）。"""
    rng = np.random.default_rng(11)
    s = 0.2 * (rng.standard_normal((4, 2, 2)) + 1j * rng.standard_normal((4, 2, 2)))
    z0 = [52.66, 45.05]
    to50 = renormalize(s, z0, 50.0)
    back = renormalize(to50, 50.0, z0)
    assert np.allclose(back, s, atol=1e-10)


def test_renormalize_changes_passivity_preserving_energy():
    """重归一化是相似变换：|S| 的整体口径变了，但无源网络仍无源（|S|<=1 的判据用最大奇异值）。"""
    s = np.zeros((1, 2, 2), dtype=complex)
    s[0, 0, 0] = 0.1 - 0.05j
    s[0, 0, 1] = 0.9
    s[0, 1, 0] = 0.9
    s[0, 1, 1] = 0.05
    out = renormalize(s, [52.66, 45.05], 50.0)
    assert np.max(np.linalg.svd(out[0], compute_uv=False)) <= 1.0 + 1e-9


def test_align_freqs_interpolates_complex():
    f1 = np.array([1.0, 2.0, 3.0])
    f2 = np.array([1.0, 3.0])
    cur = np.array([1 + 1j, 3 + 3j])
    grid, a, b = align_freqs(
        f1, [(f1, np.array([5 + 0j, 6 + 0j, 7 + 0j])), (f2, cur)])
    assert np.allclose(grid, [1.0, 2.0, 3.0])
    assert np.isclose(b[1], 2 + 2j)              # 中点插值
    assert np.allclose(a, [5, 6, 7])
    assert align_freqs(np.array([1.0]), [(np.array([5.0]), np.array([1 + 0j]))]) is None


def test_read_laspcem_z0(tmp_path):
    csv = tmp_path / "S_result.csv"
    csv.write_text(
        'Freq(GHz),"Func=none(Z0(1:1)) ","Func=none(Z0(2:1)) "\n'
        "1,52.66047195\\0.001172162967,45.04890356\\-0.01003753267\n"
        "2,52.66054976\\0.001175117631,45.04797464\\-0.009678050704\n",
        encoding="utf-8")
    z = read_laspcem_z0(str(csv))
    assert len(z) == 2
    assert abs(z[0].real - 52.66) < 0.01 and abs(z[0].imag) < 0.002
    assert abs(z[1].real - 45.05) < 0.01


def test_compare_snp_z0_csv_hits_laspcem_file(tmp_path):
    """z0_csv 只命中文件名含 laspcem 的文件，HFSS 侧走文件头 R 不被覆盖。"""
    from servers.rf_analysis.snp_compare_tool import compare_snp

    laspcem_ri = """# GHZ S RI R 50
1.0 0.3 0.1 0.4 0.2 0.4 0.2 0.3 0.1
2.0 0.3 0.1 0.4 0.2 0.4 0.2 0.3 0.1
"""
    hfss = _write(tmp_path, "hfss_design.s2p", MA_FILE)              # 无 laspcem，R 50
    laspcem = _write(tmp_path, "export_laspcem_snp_1.s2p", laspcem_ri)  # 含 laspcem
    csv = tmp_path / "S_result.csv"
    csv.write_text(
        'Freq(GHz),"Func=none(Z0(1:1)) ","Func=none(Z0(2:1)) "\n'
        "1,52.66047195\\0.001172162967,45.04890356\\-0.01003753267\n"
        "2,52.66054976\\0.001175117631,45.04797464\\-0.009678050704\n",
        encoding="utf-8")

    r = compare_snp(paths=[hfss, laspcem], renormalize_to=50.0,
                    z0_csv=str(csv), output_dir=str(tmp_path / "out"))
    assert r["success"] is True
    files = r["files"]
    assert files[0]["renormalized_from"] is None        # HFSS：50→50 no-op
    rf = files[1]["renormalized_from"]                  # LASPCEM：从 CSV 的 52.66/45.05 重归一化
    assert rf is not None
    assert abs(rf[0].real - 52.66) < 0.01 and abs(rf[1].real - 45.05) < 0.01


def test_compare_snp_z0_csv_result_is_json_serializable(tmp_path):
    """z0_csv 从 CSV 读出的 Z0 是 complex；返回体必须能 JSON 序列化，否则线上 tools/call 直接失败。"""
    import json

    from servers.rf_analysis.snp_compare_tool import compare_snp

    laspcem_ri = "# GHZ S RI R 50\n1.0 0.3 0.1 0.4 0.2 0.4 0.2 0.3 0.1\n2.0 0.3 0.1 0.4 0.2 0.4 0.2 0.3 0.1\n"
    hfss = _write(tmp_path, "hfss_design.s2p", MA_FILE)
    laspcem = _write(tmp_path, "export_laspcem_snp_2.s2p", laspcem_ri)
    csv = tmp_path / "S_result.csv"
    csv.write_text(
        'Freq(GHz),"Func=none(Z0(1:1)) ","Func=none(Z0(2:1)) "\n'
        "1,52.66047195\\0.001172162967,45.04890356\\-0.01003753267\n"
        "2,52.66054976\\0.001175117631,45.04797464\\-0.009678050704\n",
        encoding="utf-8")

    r = compare_snp(paths=[hfss, laspcem], renormalize_to=50.0,
                    z0_csv=str(csv), output_dir=str(tmp_path / "out2"))
    json.dumps(r, ensure_ascii=False)                   # 不抛 = complex 已转实数
    rf = r["files"][1]["renormalized_from"]
    assert all(isinstance(x, float) for x in rf), f"应为实部 float 列表: {rf}"
    assert abs(rf[0] - 52.66) < 0.01 and abs(rf[1] - 45.05) < 0.01


def test_compare_snp_z0_csv_miss_warns(tmp_path):
    """文件名不含 laspcem 时必须明确告警，不能静默按文件头 R 处理。"""
    from servers.rf_analysis.snp_compare_tool import compare_snp

    hfss = _write(tmp_path, "hfss_design.s2p", MA_FILE)
    plain = _write(tmp_path, "cpw_sl_plain.s2p", MA_FILE)
    csv = tmp_path / "S_result.csv"
    csv.write_text(
        'Freq(GHz),"Func=none(Z0(1:1)) ","Func=none(Z0(2:1)) "\n'
        "1,52.66\\0.001,45.05\\-0.010\n", encoding="utf-8")

    r = compare_snp(paths=[hfss, plain], renormalize_to=50.0,
                    z0_csv=str(csv), output_dir=str(tmp_path / "out3"))
    assert any("未用 z0_csv" in w and "laspcem" in w for w in r["warnings"]), r["warnings"]
    assert r["files"][1]["renormalized_from"] is None
