"""ANSYS 工具包 — HFSS / AEDT 仿真自动化（8 工具）。

config.py            进程检测 / COM 附着（多 ProgID 回退）/ 锁文件管理
com_child.py         隔离子进程：单次 COM 操作（超时可 kill，卡死不拖服务）
project_manage.py    AEDT 工程管理（5 工具）
    open_hfss_project     启动 AEDT 并打开 .aedt 项目
    close_hfss_project    关闭 AEDT 项目
    launch_aedt           启动 AEDT
    quit_aedt             退出 AEDT 程序（force=True+confirm=True 才硬杀）
    get_hfss_project_info 查询项目列表和活动设计
run_analysis.py      异步仿真 + 导出（3 工具）
    start_hfss_analysis_async 异步启动 HFSS Setup 仿真
    get_hfss_analysis_status  查询 HFSS 仿真状态
    export_hfss_snp           导出已求解的 S 参数为 Touchstone
"""

from servers.ansys.project_manage import (
    open_hfss_project, close_hfss_project, launch_aedt, quit_aedt, get_hfss_project_info,
)
from servers.ansys.run_analysis import (
    start_hfss_analysis_async, get_hfss_analysis_status, export_hfss_snp,
)

__all__ = [
    "open_hfss_project", "close_hfss_project", "launch_aedt", "quit_aedt", "get_hfss_project_info",
    "start_hfss_analysis_async", "get_hfss_analysis_status", "export_hfss_snp",
]
