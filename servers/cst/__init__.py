"""CST 工具包 —— 电磁仿真求解 + 结果导出 + 模型体检/解包 + 远场绘图 + 任务管理（13 工具）。

simulate.py      仿真求解（2 工具，异步一次性会话）
    cst_solve_async  异步求解 .cst 模型
    cst_solve_query  查询求解任务（进度+结果）
result_export.py 结果导出（5 工具）
    cst_export_snp             导出 S 参数（同步，返回 snp_path）
    cst_export_snp_async       导出 S 参数（异步，返回 task_id）
    cst_export_snp_query       查询 S 参数导出任务
    cst_export_farfield        导出远场方向图（自动判断求解）
    cst_export_farfield_query  查询远场导出任务
model.py         模型级只读操作（3 工具，同步串行）
    cst_get_model_info         只读模型体检（solved/unpacked/ports/monitors）
    cst_list_results           列出结果树全部条目
    cst_open_project           打开并保存（= 解包归档），不求解
plot.py          远场绘图（1 工具）
    cst_plot_farfield          远场 txt 出极坐标 + 直角 PNG
tasks.py         任务管理（2 工具）
    cst_task_list              列出全部 CST 异步任务
    cst_task_cancel            取消一个任务（尽力终止其子进程）
"""

from servers.cst.simulate import cst_solve_async, cst_solve_query
from servers.cst.result_export import (
    cst_export_snp,
    cst_export_snp_async,
    cst_export_snp_query,
    cst_export_farfield,
    cst_export_farfield_query,
)
from servers.cst.model import cst_get_model_info, cst_list_results, cst_open_project
from servers.cst.plot import cst_plot_farfield
from servers.cst.tasks import cst_task_list, cst_task_cancel

__all__ = [
    "cst_solve_async",
    "cst_solve_query",
    "cst_export_snp",
    "cst_export_snp_async",
    "cst_export_snp_query",
    "cst_export_farfield",
    "cst_export_farfield_query",
    "cst_get_model_info",
    "cst_list_results",
    "cst_open_project",
    "cst_plot_farfield",
    "cst_task_list",
    "cst_task_cancel",
]
