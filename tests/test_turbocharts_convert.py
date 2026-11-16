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
from servers.turbocharts.config import judge_engine_output

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
    # 默认不真调引擎 --curve-info（否则每个用例多一条命令，干扰 commands 断言）；
    # 需要 known_vars 非空的用例再单独 monkeypatch 覆盖。
    monkeypatch.setattr(cr, "_engine_curve_info", lambda p: None)
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


def test_mag_prefix_recognized(env):
    """引擎 --curve-info 返回 `mag` 可用单位（SP 下出独立幅度图），convert 必须认 mag_。"""
    resp = cr.turbocharts_convert(
        raw_path=str(env.sp_raw), output_path=str(env.tmp / "o.png"), chart_type="SP",
        linename="mag_S[1,1]")
    assert resp["success"] is True
    assert "INVALID_LINENAME" not in resp.get("error_code", "")


def test_missing_variable_reports_curve_not_found(env, monkeypatch):
    monkeypatch.setattr(cr, "_engine_curve_info",
                        lambda p: {"freq": [{"Out1": ["dBm"]}, {"Out2": ["dBm"]}]})
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out9")
    assert resp["error_code"] == "CURVE_NOT_FOUND"
    assert "Out1" in resp["message"]
    assert resp["details"]["known_variables"] == ["Out1", "Out2"]
    assert env.commands == []


def test_case_mismatch_warns_with_raw_name(env, monkeypatch):
    monkeypatch.setattr(cr, "_engine_curve_info",
                        lambda p: {"freq": [{"Out1": ["dBm"]}, {"Out2": ["dBm"]}]})
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


def test_unit_fallback_detected_via_csv_header(env):
    """请求 phase_Out1 但引擎回退成 dBm(Out1) ⇒ warnings 必须点名。

    2026-11-16 实测：HB 下 db/phase/imag/mag/VSWR/APS/MAS/MV/PSS 全部静默回退成 dBm，
    rc=0 且 success=True、图有内容，**只有产物 CSV 表头能看出真相**。
    """
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="phase_Out1")
    assert resp["success"] is True
    hits = [w for w in resp["warnings"] if "回退" in w and "phase" in w]
    assert hits, f"应报单位回退，实际 warnings={resp['warnings']}"
    assert "dBm(Out1)" in hits[0], "要点名实际产物表头"


def test_no_fallback_warning_when_unit_matches(env):
    """请求 dBm_Out1 且表头就是 dBm(Out1) ⇒ 不该有回退告警（避免狼来了）。"""
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1")
    assert not [w for w in (resp.get("warnings") or []) if "回退" in w]


def test_label_unit_maps_csv_header():
    """CSV 列名 → 单位前缀的反推规则（保留引擎原始写法，比对时才 lower）。"""
    assert cr._label_unit("dBm(Out1)") == "dBm"
    assert cr._label_unit("dB(S11)") == "dB"        # 引擎表头就是大写 B
    assert cr._label_unit("phase(S11)") == "phase"
    assert cr._label_unit("VSWR(1)") == "VSWR"
    assert cr._label_unit("mag(S11)") == "mag"
    assert cr._label_unit("S11") == "real"          # 裸变量名等价 real
    assert cr._label_unit("Out1") == "real"


def test_list_result_curves_includes_unit_notes(tmp_path, monkeypatch):
    """list_result_curves 要带 unit_notes：--curve-info 声明的能力与绘图实现不符。"""
    raw = tmp_path / "hb.raw"
    raw.write_text(HB_RAW, encoding="utf-8")
    monkeypatch.setattr(cr, "_engine_curve_info",
                        lambda p: {"freq": [{"Out1": ["dBm", "real"]}]})
    resp = cr.list_result_curves(str(raw))
    notes = " ".join(resp.get("unit_notes") or [])
    assert "HB" in notes and "回退" in notes, notes
    assert "csv_path" in notes, "要指向 turbocharts_convert 的自检路径"
    assert resp["curves"] == {"freq": [{"Out1": ["dBm", "real"]}]}, "curves 必须原样透传引擎"


def test_auto_compose_curves_when_linename_missing(env, monkeypatch):
    """不传 linename ⇒ 用 `--curve-info` 的清单自动组图，不交给引擎自己挑。

    实测（2026-11-16）：SP 不传 linename 时引擎会把 dB(S11)/dB(S12)/dB(S21)/dB(S22)
    一股脑画上、单位由引擎定且返回里无从对账；HB 更是只画一张空图。
    """
    monkeypatch.setattr(cr, "_engine_curve_info", lambda p: {
        "freq": [{"S[1,1]": ["db", "dBm", "phase"]}, {"S[1,2]": ["db", "dBm"]}]})
    resp = cr.turbocharts_convert(
        raw_path=str(env.sp_raw), output_path=str(env.tmp / "o.png"), chart_type="SP")
    assert resp["success"] is True
    assert _linenames(env.commands)[0] == "db_S[1,1]&db_S[1,2]"
    cmd = env.commands[0]
    assert cmd[cmd.index("--dependcy") + 1] == "freq", "依赖也要自动定下来"
    assert any("自动组图" in w for w in resp["warnings"]), resp["warnings"]


def test_auto_compose_uses_dbm_for_hb(env, monkeypatch):
    """HB 自动组图默认 dBm（实测只有 dBm_/real_ 是真图）。"""
    monkeypatch.setattr(cr, "_engine_curve_info", lambda p: {
        "freq": [{"Out1": ["db", "dBm", "phase", "real", "imag", "mag"]},
                 {"Out2": ["db", "dBm", "phase", "real", "imag", "mag"]}]})
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB")
    assert resp["success"] is True
    assert _linenames(env.commands)[0] == "dBm_Out1&dBm_Out2"


def test_auto_compose_keeps_explicit_linename(env, monkeypatch):
    """显式传了 linename ⇒ 不受自动组图影响。"""
    monkeypatch.setattr(cr, "_engine_curve_info", lambda p: {"freq": [{"Out1": ["dBm"]}]})
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="real_Out1")
    assert resp["success"] is True
    assert _linenames(env.commands)[0] == "real_Out1"
    assert not [w for w in (resp.get("warnings") or []) if "自动组图" in w]


def test_default_unit_priority():
    """默认单位优先级：HB→dBm，SP→db（大小写不敏感，返回引擎原始写法）。"""
    assert cr._default_unit(["db", "dBm", "real"], "HB") == "dBm"
    assert cr._default_unit(["dB", "dBm"], "SP") == "dB"
    assert cr._default_unit(["real"], "HB") == "real"
    assert cr._default_unit([], "SP") is None


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


# ── 引擎 --curve-info 原生曲线探测 ─────────────────────────

def test_engine_curve_info_parses_json(monkeypatch):
    proc = SimpleNamespace(stdout='{"freq": ["S[1,1]", "nf(1)"]}', stderr="")
    monkeypatch.setattr(cr, "run_turbocharts", lambda *a, **k: proc)
    assert cr._engine_curve_info("C:/x.raw") == {"freq": ["S[1,1]", "nf(1)"]}


def test_engine_curve_info_reads_stderr_when_stdout_empty(monkeypatch):
    proc = SimpleNamespace(stdout="", stderr='{"freq": ["Out1"]}')
    monkeypatch.setattr(cr, "run_turbocharts", lambda *a, **k: proc)
    assert cr._engine_curve_info("C:/x.raw") == {"freq": ["Out1"]}


def test_engine_curve_info_returns_none_on_non_json(monkeypatch):
    proc = SimpleNamespace(stdout="", stderr="some progress line")
    monkeypatch.setattr(cr, "run_turbocharts", lambda *a, **k: proc)
    assert cr._engine_curve_info("C:/x.raw") is None


def test_engine_curve_info_returns_none_on_exe_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("exe not found")
    monkeypatch.setattr(cr, "run_turbocharts", boom)
    assert cr._engine_curve_info("C:/x.raw") is None


def test_list_result_curves_passthrough_engine_units(tmp_path, monkeypatch):
    """引擎已直接返回带单位的结构，工具原样透传（不再补单位）。"""
    raw = tmp_path / "sp.raw"
    raw.write_text(SP_RAW, encoding="utf-8")
    monkeypatch.setattr(cr, "_engine_curve_info",
                        lambda p: {"freq": [{"S[2,1]": ["db", "dBm"]}, {"nf(1)": ["real"]}]})
    resp = cr.list_result_curves(str(raw))
    assert resp["success"] is True
    assert resp["chart_type"] == "SP"
    assert resp["curves"] == {
        "freq": [
            {"S[2,1]": ["db", "dBm"]},
            {"nf(1)": ["real"]},
        ]
    }
    assert "datasets" not in resp and "engine_curves" not in resp  # 不再返回手写解析产物


def test_list_result_curves_hb_passthrough(tmp_path, monkeypatch):
    """引擎直接给带单位结构（HB 只给 dBm/real），工具原样透传。"""
    raw = tmp_path / "hb.raw"
    raw.write_text(HB_RAW, encoding="utf-8")
    monkeypatch.setattr(cr, "_engine_curve_info",
                        lambda p: {"freq": [{"Out1": ["dBm", "real"]}],
                                   "mix_1": [{"Out1": ["dBm", "real"]}]})
    resp = cr.list_result_curves(str(raw))
    assert resp["chart_type"] == "HB"
    assert resp["curves"] == {
        "freq": [{"Out1": ["dBm", "real"]}],
        "mix_1": [{"Out1": ["dBm", "real"]}],
    }


def test_list_result_curves_fails_when_engine_unavailable(tmp_path, monkeypatch):
    raw = tmp_path / "sp.raw"
    raw.write_text(SP_RAW, encoding="utf-8")
    monkeypatch.setattr(cr, "_engine_curve_info", lambda p: None)
    resp = cr.list_result_curves(str(raw))
    assert resp["success"] is False
    assert resp["error_code"] == "CURVE_INFO_FAILED"


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


def test_turbocharts_timeout_returns_error(env, monkeypatch):
    """run_turbocharts 超时抛 RuntimeError 时应返回 TOOL_TIMEOUT，而非崩溃。"""
    def _raise_timeout(cmd, timeout_seconds=60):
        raise RuntimeError("Turbocharts 执行超时（120 秒）")
    monkeypatch.setattr(cr, "run_turbocharts", _raise_timeout)

    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out1")
    assert resp["success"] is False
    assert resp["error_code"] == "TOOL_TIMEOUT"


# ── command_runs：每次子进程调用逐条回传（2026-09-29 需求）──────────

def test_command_runs_records_image_and_each_csv_call(env):
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1&dBm_Out2")
    runs = resp["command_runs"]
    assert len(runs) == 3                     # 图片 1 次 + HB 多曲线拆 2 次
    assert [r["command"] for r in runs] == [" ".join(c) for c in env.commands]
    assert all(set(r) == {"command", "return_code", "stdout", "stderr", "ok"} for r in runs)
    assert all(r["return_code"] == 0 for r in runs)
    assert f"--img {env.tmp / 'o.png'}" in runs[0]["command"]


def test_command_runs_carries_exe_output(env, monkeypatch):
    """引擎 Qt 参数层报错（GBK 文本 + rc=127）必须原样进 command_runs。"""
    msg = "错误：缺少必需参数 --raw"
    monkeypatch.setattr(cr, "run_turbocharts", lambda cmd, timeout_seconds=60: (
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG"),
        SimpleNamespace(returncode=127, stdout="", stderr=msg),
    )[1])
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out1")
    assert resp["command_runs"][0]["stderr"] == msg
    assert resp["command_runs"][0]["return_code"] == 127
    assert resp["stderr"] == msg                 # 顶层字段保持兼容


def test_command_runs_marks_csv_timeout(env, monkeypatch):
    """CSV 拆分调用超时：仍留一条 return_code=None + error 的记录，且不丢已画好的图。"""
    def _run(cmd, timeout_seconds=60):
        if "--csv" in cmd:
            raise RuntimeError("Turbocharts 执行超时（120 秒）")
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cr, "run_turbocharts", _run)
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1&dBm_Out2")
    runs = resp["command_runs"]
    assert len(runs) == 3
    assert all("error" in r and r["return_code"] is None for r in runs[1:])
    assert resp["img_generated"] is True
    assert any("超时" in w for w in resp["warnings"])

# ── 执行成功判定（引擎 rc 恒 0 ⇒ 靠引擎自报措辞 + 产物）────────────

def test_judge_engine_output_on_real_engine_strings():
    """判定规则用 2026-09-30 实测抓到的引擎原句做回归。"""
    ok_out = '绘制线名： "dBm_input_AMP_test1_XH2036"\r\n"C:/t/n.png 导出成功"'
    assert judge_engine_output(ok_out) is True
    assert judge_engine_output("绘制线段: ()") is False
    assert judge_engine_output("linename为空") is False
    assert judge_engine_output("HB CSV导出失败：x_label_为空，当前没有有效曲线数据") is False
    assert judge_engine_output("") is None
    assert judge_engine_output("") is None


def test_engine_verdict_flags_failed_csv_with_reason(env, monkeypatch):
    """不传 linename（TC-1 场景）：引擎自报 x_label_为空 ⇒ engine_ok False、message 带原话、不回填 csv 路径。"""
    eng_err = ('linename为空\r\n"C:/t/n.png 导出成功"\r\n'
               'HB CSV导出失败：x_label_为空，当前没有有效曲线数据')
    monkeypatch.setattr(cr, "run_turbocharts", lambda cmd, timeout_seconds=60: (
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG"),
        SimpleNamespace(returncode=0, stdout="", stderr=eng_err),
    )[1])
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"))
    assert resp["engine_ok"] is False
    assert any("x_label_为空" in line for line in resp["engine_report"])
    assert resp["message"].startswith("CSV 未生成") and "x_label_为空" in resp["message"]
    assert "csv" not in resp["output_paths"]                 # TC-1：未生成就不回填
    assert any("引擎自报未成功" in w for w in resp["warnings"])


def test_engine_verdict_ok_keeps_paths(env, monkeypatch):
    """正常路径：引擎报导出成功 ⇒ engine_ok True、message 带引擎结果、csv 路径照旧回填。"""
    def _run(cmd, timeout_seconds=60):
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG")
        if "--csv" in cmd:
            Path(cmd[cmd.index("--csv") + 1]).write_text("freq,dBm(Out1)\n0,-3\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="",
                               stderr='绘制线名： "Out1"\r\n"x.png 导出成功"')
    monkeypatch.setattr(cr, "run_turbocharts", _run)
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        csv_path=str(env.tmp / "o.csv"), linename="dBm_Out1")
    assert resp["engine_ok"] is True
    assert resp["output_paths"]["csv"] == str(env.tmp / "o.csv")
    assert resp["message"] == "曲线图已生成（引擎：x.png 导出成功）"


def test_engine_verdict_failure_overrides_optimistic_message(env, monkeypatch):
    """白跑（HB 数据传 --type SP）：引擎自报「绘制线段: ()」
    但仍写空图、rc=0 ⇒ engine_ok False 时 message 不能再报成功。
    """
    eng = ('绘制线名： "dBm_Out1"\r\n'
           '绘制线段: ()\r\n'
           '绘制依赖: ""\r\n"C:/t/n.png 导出成功"')
    monkeypatch.setattr(cr, "run_turbocharts", lambda cmd, timeout_seconds=60: (
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG"),
        SimpleNamespace(returncode=0, stdout="", stderr=eng),
    )[1])
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="SP",
        linename="dBm_Out1")
    assert resp["img_generated"] is True          # 引擎照样写了空图（rc=0）
    assert resp["engine_ok"] is False
    assert resp["message"].startswith("引擎自报未成功")
    assert "绘制线段: ()" in resp["message"]

def test_record_run_marks_nonzero_rc_as_failure(env, monkeypatch):
    """rc≠0 属硬失败：Qt 参数层 rc=127 的 stderr 不含任何判据措辞，
    只靠措辞会漏判成 None ⇒ 必须直接 False（数据层失败 rc 恒 0，方向安全）。
    """
    monkeypatch.setattr(cr, "run_turbocharts", lambda cmd, timeout_seconds=60: (
        Path(cmd[cmd.index("--img") + 1]).write_bytes(b"\x89PNG"),
        SimpleNamespace(returncode=127, stdout="", stderr="错误：缺少必需参数 --raw"),
    )[1])
    resp = cr.turbocharts_convert(
        raw_path=str(env.raw), output_path=str(env.tmp / "o.png"), chart_type="HB",
        linename="dBm_Out1")
    assert resp["command_runs"][0]["ok"] is False
    assert resp["command_runs"][0]["return_code"] == 127
    assert resp["engine_ok"] is False
    assert resp["message"].startswith("引擎自报未成功")
