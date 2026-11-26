"""simulation_tool 说明文档 Resource —— 把 exe 包装层的 .md 注入给上层 Agent。

5 个独立仿真 exe 的说明文档（Mixer / TR / NetlistPathfinder / ReportExporter / drawtool）
是参数表的单一事实源；打包成 exe 后这些 .md 不在 agent 能读的地方，所以注册成 Resource，
agent 用 resources/read 就能读原文。本模块**直接读 `servers/simulation_tool/*.md` 原文件**，
文件缺失时返回可操作的兜底提示（改调 describe_tool），不抛异常。

通用做法：`_GUIDES` 一张表驱动，循环注册，加新文档只往表里加一行；冻结态路径统一走
`_MEIPASS`，与 turbocharts/resource.py 的 guide_path() 同套路。
"""

from __future__ import annotations

import sys as _sys
from functools import partial
from pathlib import Path

from servers import mcp

#: slug -> (文件名, 显示名, 一句话说明)。slug 用小写短横线，避免中文 URI。
_GUIDES: dict[str, tuple[str, str, str]] = {
    "mixer-simulator": (
        "Mixer_Simulator使用说明.md", "Mixer Simulator Guide",
        "混频链路仿真参数表：project 的 links/defaults/switch_bits/indicators 结构、15 指标名与逐指标必填字段。"),
    "tr-simulator": (
        "TR_Simulator使用说明.md", "TR Simulator Guide",
        "TR 收发链路仿真说明：13 个链路字段、23 个指标名与单位。"),
    "netlist-pathfinder": (
        "Netlist_Pathfinder使用说明.md", "Netlist Pathfinder Guide",
        "网表寻路说明：器件连通规则、Bits 编码、五档 status 与三级 errors。"),
    "report-exporter": (
        "Report_Exporter使用说明.md", "Report Exporter Guide",
        "报告导出说明：report JSON 字段（components/links/charts/schematic_path）与产物。"),
    "xy-chart": (
        "xy_chart(drawtool)使用说明.md", "XY Chart Guide",
        "XY 绘图说明：drawtool.exe 的 JSON 配置（series/marks/chart_title/image_path/image_format）。"),
}


def _guide_path(filename: str) -> Path:
    """说明文件路径：冻结态读 _MEIPASS 打包副本，源码态读同目录。"""
    if getattr(_sys, "frozen", False):
        return Path(_sys._MEIPASS) / "servers" / "simulation_tool" / filename
    return Path(__file__).with_name(filename)


def _read_guide(filename: str) -> str:
    """读原文件；缺了返回可操作兜底而不是抛异常。"""
    path = _guide_path(filename)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (f"# 无法读取 {filename}\n\n预期路径: {path}\n错误: {exc}\n\n"
                f"改调 describe_tool 看对应工具的参数表，或确认打包时把该 .md 收进了 datas。")


for _slug, (_filename, _name, _desc) in _GUIDES.items():
    mcp.resource(
        f"edi://reference/{_slug}-guide",
        name=_name,
        title=_name,
        description=_desc,
        mime_type="text/markdown",
    )(partial(_read_guide, _filename))
