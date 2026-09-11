# -*- coding: utf-8 -*-
"""turbocharts_convert / list_result_curves 校验与拆分行为回归测试。

覆盖 2026-09-11 实测校准后的行为：
  - linename 语法校验（逗号/裸变量名/未识别前缀 → INVALID_LINENAME，不再静默出空图）
  - 变量存在性校验（→ CURVE_NOT_FOUND）
  - HB 多条曲线 CSV 自动拆分、VSWR 多条拆分的既有行为不回归
  - 输出父目录自动创建（引擎不会自建目录）
  - 回读 CSV 表头写入 curve_labels
  - HB 下 phase_ 前缀给出"引擎会忽略"警告
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from servers.turbocharts import convert_raw as cr

HB_RAW = """File Format: MDS
Revision:  2.01
Plotname: HB HB1[1] <test.net>
Flags: complex
No. Sweep Variables: 0
No. Variables: 3
Variables:\t0\tfreq\tfrequency type=real indep=yes
\t\t1\tOut1\tvoltage type=complex indep=no
\t\t2\tOut2\tvoltage type=complex indep=no
Values:
0\t0.0,0.0
"""

SP_RAW = """File Format: MDS
Revision:  2.01
Plotname: SP SP1[1] <test.net>   freq=(29 GHz->31 GHz)
Flags: complex
No. Sweep Variables: 0
No. Variables: 3
Variables:\t0\tfreq\tfrequency type=real indep=yes
\t\t1\tS[1,1]\ts-param type=complex indep=no
\t\t2\tS[2,1]\ts-param type=complex indep=no
Values:
0\t2.9e+10,0.0
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """构造 mock 环境：假 RAW、假 turbocharts、记录命令并按需产出文件。"""
    raw = tmp_path / "result.raw"
    raw.write_text(HB_RAW, encoding="utf-8")
    sp_raw = tmp_path / "sp.raw"
    sp_raw.write_text(SP_RAW, encoding="utf-8")

    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", "tc.exe")
    monkeypatch.setattr(cr, "validate_file", lambda p, *a, **k: str(p))

    commands: list[list[str]] = []

    def _run(cmd, timeout_seconds=60):
        commands.append(list(cmd))
        # 模拟引擎：把 --img / --csv 指定的文件写出来（图片内容带上调用序号，便于断言没被覆盖）
        for flag in ("--img", "--csv"):
            if flag in cmd:
                target = cmd[cmd.index(flag) + 1]
                if flag == "--img":
                    Path(target).write_bytes(b"\x89PNG" + str(len(commands)).encode())
                else:
                    Path(target).write_text("freq,dBm(Out1)\n0.0,-3.0\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(cr, "run_turbocharts", _run)
    return SimpleNamespace(raw=raw, sp_raw=sp_raw, tmp=tmp_path, commands=commands)


def _linenames(commands):
    return [c[c.index("--linename") + 1] for c in commands if "--linename" in c]


# ── 校验：非法写法不再静默出空图 ─────────────────────────────

def test_comma_separator_rejected_without_running_exe(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out1,dBm_Out2")
    assert resp["success"] is False
    assert resp["error_code"] == "INVALID_LINENAME"
    assert "&" in resp["message"]
    assert env.commands == [], "非法 linename 不应启动 turbocharts"


def test_bare_variable_name_rejected(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="Out1")
    assert resp["error_code"] == "INVALID_LINENAME"
    assert env.commands == []


def test_unknown_prefix_rejected(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="ang_Out1")
    assert resp["error_code"] == "INVALID_LINENAME"


def test_missing_variable_reports_curve_not_found(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out9")
    assert resp["error_code"] == "CURVE_NOT_FOUND"
    assert "Out1" in resp["message"]
    assert resp["details"]["known_variables"] == ["Out1", "Out2", "freq"]
    assert env.commands == []


def test_case_mismatch_warns_with_raw_name(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_out1")
    assert resp["success"] is True
    assert any("Out1" in w for w in resp["warnings"])


def test_hb_ignores_phase_prefix_warning(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="phase_Out1")
    assert resp["success"] is True
    assert any("HB" in w and "dBm" in w for w in resp["warnings"])


# ── 输出目录 / CSV 回读 ────────────────────────────────────

def test_output_parent_dir_created(env):
    target = env.tmp / "深层" / "sub" / "o.png"
    resp = cr.turbocharts_convert(raw_path=str(env.raw), output_path=str(target),
                                  chart_type="HB", linename="dBm_Out1")
    assert resp["img_generated"] is True
    assert target.parent.is_dir()


def test_csv_labels_read_back(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1")
    assert resp["curve_labels"][str(env.tmp / "o.csv")] == ["freq", "dBm(Out1)"]
    assert resp["csv_generated"] is True
    assert resp["curves"] == ["dBm_Out1"]


def test_missing_csv_warns(env, monkeypatch):
    monkeypatch.setattr(cr, "run_turbocharts",
                        lambda cmd, timeout_seconds=60: SimpleNamespace(
                            returncode=0, stderr="", stdout=""))
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1")
    assert resp["csv_generated"] is False
    assert any("CSV 未生成" in w for w in resp["warnings"])


# ── CSV 拆分 ───────────────────────────────────────────────

def test_hb_multi_curve_csv_split_per_curve(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1&dBm_Out2")
    assert sorted(_linenames(env.commands)[1:]) == ["dBm_Out1", "dBm_Out2"]
    assert len(resp["curve_labels"]) == 2
    assert any("拆分" in w for w in resp["warnings"])


def test_duplicate_curve_names_deduped_for_csv(env):
    """重复曲线（如 "dBm_Out1&dBm_Out1"）：同名曲线拆分会互相覆盖，应去重并提示。"""
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1&dBm_Out1")
    assert len(resp["curve_labels"]) == 1
    assert any("重复曲线" in w for w in resp["warnings"])
    assert env.commands[-1][env.commands[-1].index("--linename") + 1] == "dBm_Out1"


def test_csv_split_keeps_multi_curve_image(env):
    """CSV 拆分调用不得用 --img 覆盖已画好的多曲线图（2026-09-11 实测 bug）。"""
    out_png = env.tmp / "o.png"
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(out_png), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1&dBm_Out2")
    # 图片只渲染一次，且内容仍是第一次调用的产物
    img_calls = [c for c in env.commands if "--img" in c and c[c.index("--img") + 1] == str(out_png)]
    assert len(img_calls) == 1, f"图片应只渲染一次，实际 {len(img_calls)} 次"
    assert out_png.read_bytes() == b"\x89PNG1", "多曲线图被后续 CSV 调用覆盖了"
    # 临时图片不残留
    assert not list(env.tmp.glob("__csv_tmp_*.png"))
    assert not any("被改写" in w for w in resp.get("warnings", []))


def test_sp_multi_curve_csv_single_call(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.sp_raw), output_path=str(env.tmp / "o.png"), chart_type="SP",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_S[2,1]&dBm_S[1,1]")
    assert env.commands[-1][env.commands[-1].index("--linename") + 1] == \
        "dBm_S[2,1]&dBm_S[1,1]"
    assert len(resp["curve_labels"]) == 1


def test_multi_vswr_without_csv_warns(env):
    """自带说明：驻波只支持单条曲线 → 同图多条且未导 CSV 时给出提示。"""
    resp = cr.turbocharts_convert(
        raw_path=str(env.sp_raw), output_path=str(env.tmp / "o.png"), chart_type="SP",
        linename="VSWR_S[1,1]&VSWR_S[1,1]")
    assert resp["success"] is True
    assert any("VSWR" in w and "单条" in w for w in resp["warnings"])


def test_vswr_split_still_works(tmp_path, monkeypatch):
    """既有行为：多条 VSWR 逐条拆分，其它曲线合并为一次。"""
    raw = tmp_path / "sp.raw"
    raw.write_text(SP_RAW, encoding="utf-8")
    monkeypatch.setattr(cr, "validate_file", lambda p, *a, **k: str(p))
    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", "tc.exe")
    commands: list[list[str]] = []
    monkeypatch.setattr(cr, "run_turbocharts", lambda cmd, timeout_seconds=60: (
        commands.append(list(cmd)),
        SimpleNamespace(returncode=0, stderr="", stdout=""),
    )[1])

    cr.turbocharts_convert(
        raw_path=str(raw), output_path=str(tmp_path / "o.png"), chart_type="SP",
        csv_path=str(tmp_path / "o.csv"),
        linename="dBm_S[2,1]&VSWR_S[1,1]&VSWR_S[1,1]&dBm_S[1,1]")

    names = _linenames(commands)
    assert "dBm_S[2,1]&dBm_S[1,1]" in names
    assert "VSWR_S[1,1]" in names


# ── suggested_curves 按 plot 类型分派 ───────────────────────

def test_suggest_curves_hb_excludes_phase(tmp_path):
    raw = tmp_path / "hb.raw"
    raw.write_text(HB_RAW, encoding="utf-8")
    resp = cr.list_result_curves(str(raw))
    ds = resp["datasets"][0]
    assert ds["plot_type"] == "HB"
    assert all(not c.startswith("phase_") for c in ds["suggested_curves"])
    assert "dBm_Out1" in ds["suggested_curves"] and "real_Out2" in ds["suggested_curves"]
    assert any("HB" in n for n in ds["curve_notes"])


def test_suggest_curves_sp_includes_phase_and_vswr(tmp_path):
    raw = tmp_path / "sp.raw"
    raw.write_text(SP_RAW, encoding="utf-8")
    ds = cr.list_result_curves(str(raw))["datasets"][0]
    assert ds["plot_type"] == "SP"
    assert "phase_S[2,1]" in ds["suggested_curves"]
    assert "VSWR_S[1,1]" in ds["suggested_curves"]      # i == j
    assert "VSWR_S[2,1]" not in ds["suggested_curves"]  # i != j 不给


# ── Resource：引擎自带说明（edi://reference/turbocharts-guide）──

def test_guide_resource_registered():
    """资源随 turbocharts 包注册，URI 出现在 resources/list。"""
    import asyncio

    import servers.registry_server  # noqa: F401 — 触发全部注册
    from servers import mcp

    uris = [str(r.uri) for r in asyncio.run(mcp.list_resources())]
    assert "edi://reference/turbocharts-guide" in uris


def test_guide_resource_returns_manual_verbatim():
    """资源内容 = 引擎自带说明文件原文（单一事实源，不做二次加工）。"""
    from servers.turbocharts import resource as tr_resource

    text = tr_resource.resource_turbocharts_guide()
    assert text == tr_resource.guide_path().read_text(encoding="utf-8", errors="ignore")
    assert "--linename" in text and "real_delayS[1,1]" in text


def test_guide_resource_falls_back_when_file_missing(tmp_path, monkeypatch):
    from servers.turbocharts import resource as tr_resource

    monkeypatch.setattr(tr_resource, "guide_path", lambda: tmp_path / "nope.txt")
    text = tr_resource.resource_turbocharts_guide()
    assert "无法读取引擎自带说明" in text and "--linename" in text
