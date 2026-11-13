"""LASPCEM 电磁仿真 — 启动 / 打开工程 / aedt 转 sem / 异步求解（可带后处理）/ 同步导出·建报告 / 读设置。

设计（2026-11-13 定稿）：只有「求解」是慢操作 → 只把求解做成异步任务；
导出/报告是 5–6 s 的同步快工具，未求解时立刻返回 SOLVE_REQUIRED。
“先求解再导出/建报告”由**服务端**在求解任务里串（run_laspcem_solve_async(then_export/then_report)），
调用方不需要自己按顺序调工具。

config.py       可执行路径检测 + 进程状态
script_gen.py   脚本生成（导入 aedt / 求解 / 补保存 / 导出 S 参数 / 建报告）
setup_reader.py 从 .sem 提取变量/解名/setup 名/端口数 + 求解状态判断
launcher.py     启动 / 打开 / 导入 / 导出 / 建报告 工具 + 求解内部实现
async_solve.py  异步求解任务（提交 / 查询 / 取消 / 列表，含 then_export / then_report 串联）
"""
from __future__ import annotations

from servers.laspcem import async_solve  # noqa: F401 — 导入即注册异步工具
from servers.laspcem.async_solve import (
    laspcem_cleanup_orphans,
    laspcem_solve_query,
    laspcem_task_cancel,
    laspcem_task_list,
    run_laspcem_solve_async,
)
from servers.laspcem.launcher import (
    build_laspcem_report,
    close_laspcem_project,
    export_laspcem_snp,
    get_laspcem_setup,
    import_aedt_as_sem,
    launch_laspcem,
    open_laspcem_project,
)

__all__ = [
    "build_laspcem_report",
    "close_laspcem_project",
    "export_laspcem_snp",
    "get_laspcem_setup",
    "import_aedt_as_sem",
    "laspcem_cleanup_orphans",
    "laspcem_solve_query",
    "laspcem_task_cancel",
    "laspcem_task_list",
    "launch_laspcem",
    "open_laspcem_project",
    "run_laspcem_solve_async",
]
