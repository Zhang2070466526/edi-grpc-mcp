"""仿真报告工具包 — 数据校验并调用本地报告渲染服务（1 工具）。

generator.py  generate_simulation_report  生成 PDF/DOCX 仿真报告（16 步校验 + 渲染）
"""

from servers.report.generator import generate_simulation_report

__all__ = ["generate_simulation_report"]
