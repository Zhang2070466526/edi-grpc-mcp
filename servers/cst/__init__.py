"""CST 工具包 —— 电磁仿真（CST）求解 + 结果导出。

simulate.py          仿真求解工具（异步，一次性会话）
result_export.py     结果导出工具（S 参数无会话 / 远场方向图需会话）
"""

from servers.cst.simulate import cst_solve_async, cst_solve_query
from servers.cst.result_export import (
    cst_export_snp,
    cst_export_farfield,
    cst_export_farfield_query,
)

__all__ = [
    "cst_solve_async",
    "cst_solve_query",
    "cst_export_snp",
    "cst_export_farfield",
    "cst_export_farfield_query",
]
