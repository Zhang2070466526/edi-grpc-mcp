"""MCP instructions 回归测试（2026-10-21）。

背景：`servers/__init__.py` 的 instructions 原本写「先用 tool_search 按关键词搜工具名，
再用 tool_describe 查看具体参数」—— 这两个是 **Hermes 客户端**的概念，本服务根本没有，
而服务端真正的自描述工具 `describe_tool` 一个字没提。结果：agent 不知道该调什么来查工具
用法，换到 Claude Code / Cursor 这类客户端更是直接找不到入口。
"""

from __future__ import annotations

from servers import mcp


def _ins() -> str:
    return mcp.instructions or ""


def test_instructions_point_to_describe_tool():
    ins = _ins()
    assert "describe_tool" in ins, "instructions 必须告诉 agent 用 describe_tool 自查工具用法"
    # 两种用法都要写清楚：留空列全部、传名字看细节
    assert "describe_tool()" in ins
    assert 'describe_tool("工具名")' in ins or "describe_tool(\"工具名\")" in ins


def test_instructions_do_not_reference_client_only_tools():
    ins = _ins()
    for wrong in ("tool_search", "tool_describe"):
        assert wrong not in ins, (
            f"instructions 提到了服务端不存在的客户端工具 {wrong!r}；"
            "服务端的自描述工具是 describe_tool"
        )


def test_instructions_cover_simulation_and_report_tools():
    ins = _ins()
    for name in ("run_mixer_simulation", "run_tr_simulation",
                 "export_simulation_report", "report_artifacts"):
        assert name in ins, f"instructions 导航缺少 {name}"


def test_tr_is_not_described_as_self_reporting():
    """TR_Simulator 不产 PDF/DOCX（见 tr_simulation.py 模块 docstring）。

    instructions 必须把它指向 export_simulation_report；曾经被写成「TR 也自带报告」，
    已实测纠错（2026-10-21），这条锁住，防止改回去。
    """
    ins = _ins()
    assert "run_tr_simulation" in ins
    assert "export_simulation_report" in ins, "TR 的正式报告路径必须写在 instructions 里"
    # 反面：不能出现“TR（纯链路）自带报告 / 直接交付 report_artifacts”的表述
    assert "run_tr_simulation(纯链路) → 结果里的 report_artifacts" not in ins
    assert "run_tr_simulation → 自带" not in ins
