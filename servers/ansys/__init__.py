"""ANSYS 工具包 — HFSS / AEDT 仿真自动化（6 工具）。

config.py            进程检测 / COM 附着（多 ProgID 回退）/ 锁文件管理
project_manage.py    AEDT 工程管理（4 工具）
    open_hfss_project     启动 AEDT 并打开 .aedt 项目
    close_hfss_project    关闭 AEDT 项目
    launch_aedt           启动 AEDT
    get_hfss_project_info 查询项目列表和活动设计
run_analysis.py      异步仿真（2 工具）
    start_hfss_analysis_async 异步启动 HFSS Setup 仿真
    get_hfss_analysis_status  查询 HFSS 仿真状态
"""

from servers.ansys.project_manage import open_hfss_project, close_hfss_project, launch_aedt, get_hfss_project_info
from servers.ansys.run_analysis import start_hfss_analysis_async, get_hfss_analysis_status

__all__ = [
    "open_hfss_project", "close_hfss_project", "launch_aedt", "get_hfss_project_info",
    "start_hfss_analysis_async", "get_hfss_analysis_status",
]
