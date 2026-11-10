"""LASPCEM 电磁仿真 — 启动软件 / 打开 .sem 工程 / 读求解设置。

config.py      可执行路径检测 + 进程状态
script_gen.py  通用仿真脚本生成（打开工程 → 分析 setup → 保存）
setup_reader.py 从 .sem 提取当前求解设置参数
launcher.py    MCP 工具（launch_laspcem / open_laspcem_project / get_laspcem_setup）
"""
from servers.laspcem.launcher import (
    launch_laspcem,
    open_laspcem_project,
    get_laspcem_setup,
)

__all__ = ["launch_laspcem", "open_laspcem_project", "get_laspcem_setup"]