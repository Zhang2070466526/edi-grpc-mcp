"""独立仿真工具包 — 直接调用固定目录下的四个 exe（不走 gRPC/HTTP）。

simulation_tool 目录（固定，见 settings.simulation_tool_dir）：
    simulation_report/Report_Exporter.exe  独立报告导出（document.pdf/docx/report.json）
    simulation_report/drawtool.exe         XY 数据绘图（JSON 配置 → 曲线图 PNG/JPG）
    tr_simulation/TR_Simulator.exe         TR 纯硬编码仿真（网表→路径分析→ADS→结果提取）
    mixer_simulation/mixer_simulation.exe  混频链路仿真（上下变频/多通道/任意级变频）

四个 exe 的 stdout 都是单行 UTF-8 JSON、退出码 0/1，判成败直接看退出码 + JSON 的
success/errors（drawtool 是 status/error_code；比 turbocharts 的 rc 恒 0 要简单）。
"""

from servers.simulation_tool.report_export import export_simulation_report
from servers.simulation_tool.tr_simulation import run_tr_simulation
from servers.simulation_tool.mixer_simulation import run_mixer_simulation
from servers.simulation_tool.xy_chart import draw_chart

__all__ = ["export_simulation_report", "run_tr_simulation", "run_mixer_simulation", "draw_chart"]
