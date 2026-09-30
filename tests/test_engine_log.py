# -*- coding: utf-8 -*-
"""引擎原文落盘（log_path）与"必须原样转述"描述守卫。

背景（2026-09-30 用户反馈）：turbocharts_convert 的返回里本身就有 stderr / command_runs /
engine_report，但**模型可能只回一句 message**。于是两层加固：

  1) 引擎原文额外落盘 → 与 RAW 同目录 ``<raw 文件名>.turbocharts.log``（追加写），返回
     ``log_path`` / ``log_lines``；不依赖模型自觉，user/agent 随时可读全文。
  2) "回复须把引擎原文原样转述"的硬指令写进**工具描述**。⚠️ 注意
     ``servers/schema_descriptions.py`` 的 ``trim_tool_descriptions()`` 只把 docstring
     **第一段**发进 tools/list，指令一旦挪到第二段就等于消失 —— 所以这里断言注册表里的
     **最终 description**，而不是 docstring。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from servers.turbocharts import convert_raw as cr
from servers.turbocharts.config import append_engine_log, engine_log_path

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

ENGINE_STDERR = ('绘制线名： "DB_S[1,1]"\n绘制线段: ("dB(S[1,1])")\n绘图线程返回数据\n'
                 '"C:\\Users\\JGL\\EDA-PMDS-Workspace\\projects\\x\\s11.png 导出成功"')


def _mock_engine(tmp_path, monkeypatch, stderr_text: str = ENGINE_STDERR):
    """假 RAW + 假引擎：按 --img/--csv 产出文件，stderr 带引擎原措辞。"""
    raw = tmp_path / "result.raw"
    raw.write_text(SP_RAW, encoding="utf-8")

    monkeypatch.setattr(cr, "TURBOCHARTS_PATH", "tc.exe")
    monkeypatch.setattr(cr, "validate_file", lambda p, *a, **k: str(p))

    calls: list[list[str]] = []

    def _run(cmd, timeout_seconds=60):
        calls.append(list(cmd))
        for flag in ("--img", "--csv"):
            if flag in cmd:
                target = cmd[cmd.index(flag) + 1]
                if flag == "--img":
                    Path(target).write_bytes(b"\x89PNG")
                else:
                    Path(target).write_text("freq,dB(S11)\n0.0,-1.0\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr=stderr_text)

    monkeypatch.setattr(cr, "run_turbocharts", _run)
    return raw, calls


def test_convert_returns_log_path_with_raw_engine_output(tmp_path, monkeypatch):
    """返回里要有 log_path，且日志内容 = 引擎原措辞 + 每趟子进程命令行。"""
    raw, calls = _mock_engine(tmp_path, monkeypatch)

    resp = cr.turbocharts_convert(
        raw_path=str(raw), output_path=str(tmp_path / "s11.png"), chart_type="SP",
        csv_path=str(tmp_path / "s11.csv"), linename="DB_S[1,1]", dependency="freq")

    assert resp["success"] is True
    assert resp["log_path"] == str(engine_log_path(raw))
    assert Path(resp["log_path"]).name == "result.raw.turbocharts.log"
    assert resp["log_lines"] > 0

    text = Path(resp["log_path"]).read_text(encoding="utf-8")
    for want in ('绘制线名： "DB_S[1,1]"', '绘制线段: ("dB(S[1,1])")', "绘图线程返回数据"):
        assert want in text, f"日志里缺引擎原句: {want}"

    # 图片 1 趟 + CSV 1 趟，逐趟记录且命令行齐全（下游可凭 rc/stderr 判断）
    assert text.count("-- run[") == len(resp["command_runs"]) == len(calls) == 2
    assert "--img" in text and "--csv" in text
    assert "engine_ok=" in text


def test_engine_log_appends_instead_of_overwriting(tmp_path):
    """同一 RAW 多次调用累积到同一日志，不覆盖历史。"""
    raw = tmp_path / "r.raw"
    raw.write_text(SP_RAW, encoding="utf-8")
    runs = [{"command": "tc.exe --raw r.raw", "return_code": 0, "stdout": "",
             "stderr": "第一次", "ok": True}]

    p1, n1 = append_engine_log(raw, runs, context={"linename": "DB_S[1,1]"})
    p2, n2 = append_engine_log(raw, runs, context={"linename": "DB_S[2,1]"})

    assert p1 == p2 == str(engine_log_path(raw))
    assert n1 == n2 > 0
    text = Path(p1).read_text(encoding="utf-8")
    assert "第一" in text  # 引擎原文（"第一次"）
    assert text.count("-- run[0]") == 2, "两次调用应各留一条 run 记录"
    assert "linename=DB_S[1,1]" in text and "linename=DB_S[2,1]" in text


def test_transcript_instruction_reaches_tools_list():
    """硬指令必须留在**最终 description**（trim 只发第一段，挪到第二段就等于没写）。"""
    from start_servers import mcp

    tool = mcp._tool_manager._tools["turbocharts_convert"]
    desc = getattr(tool, "description", "") or ""

    assert "原样转述" in desc, f"tools/list 的 description 丢了转述指令: {desc[:160]}"
    assert "\n\n" not in desc, "description 不应含空行（trim 只保留第一段）"
    assert "Args:" not in desc and "Returns:" not in desc
    assert "log_path" in desc, "description 应提示返回里有 log_path"
