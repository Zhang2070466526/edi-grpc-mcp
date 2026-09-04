"""CST 工具包 —— 电磁仿真求解 + 结果导出（5 工具）。

simulate.py      仿真求解（2 工具，异步一次性会话）
    cst_solve_async  异步求解 .cst 模型
    cst_solve_query  查询求解任务（进度+结果）
result_export.py 结果导出（3 工具）
    cst_export_snp             导出 S 参数为 Touchstone .sNp
    cst_export_farfield        导出远场方向图（自动判断求解）
    cst_export_farfield_query  查询远场导出任务
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
