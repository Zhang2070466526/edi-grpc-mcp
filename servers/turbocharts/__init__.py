"""Turbocharts 工具包 — ADS RAW 文件图表生成（3 工具 + 1 Resource）。

convert_raw.py      RAW 转图 + 曲线查询（2 工具）
    turbocharts_convert  RAW → 曲线图 + CSV
    list_result_curves   解析 RAW 返回可用曲线名
compare_results.py  仿真结果对比（1 工具）
    compare_simulation_results  多 RAW 同曲线对比叠图
resource.py         引擎自带说明 Resource（画图前参考）
    edi://reference/turbocharts-guide  《RAW 转图像工具使用说明》原文
"""

from servers.turbocharts.compare_results import compare_simulation_results
from servers.turbocharts.convert_raw import turbocharts_convert, list_result_curves
from servers.turbocharts.resource import resource_turbocharts_guide  # noqa: F401 — 注册 Resource

__all__ = ["compare_simulation_results", "turbocharts_convert", "list_result_curves"]
