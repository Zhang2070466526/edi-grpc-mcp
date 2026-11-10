"""LASPCEM 仿真脚本封装 — 生成「打开工程 → 求解 → 保存」的通用脚本。

LASPCEM 的自动化接口是 GUI 内嵌 Python 脚本（控制台 / Run Script），无 headless 入口。
本模块生成通用脚本：对任意已配置好 setup 的 .sem 工程，打开后直接分析求解并写回。
不包含某个工程的录制 setup（EditSolution），所以是通用的 —— 前提是该工程已保存过 setup。
"""

from __future__ import annotations

#: 脚本头（BuildDate/BuildNo 与实测脚本一致）
_SCRIPT_VERSION = "{'BuildDate':'2026-09-23','BuildNo':'28167'}"


def build_laspcem_script(sem_path: str, setup_name: str = "Setup1") -> str:
    """生成「打开工程 → 分析 setup → 保存」的通用 LASPCEM 脚本。

    Args:
        sem_path: .sem 工程绝对路径（脚本内统一转正斜杠）。
        setup_name: 要分析的 setup 名（工程里已配置好的），默认 "Setup1"。

    注：目前用 cem.FEMAnalyze（FEM 求解）。若工程用的是 MoM/FDTD 等其它求解器，
    需要对应的 Analyze 命令，待拿到后再扩展。
    """
    p = sem_path.replace("\\", "/")
    return "\n".join([
        "#Version Information: LASPCEM(28167) Sep 23 2026",
        f"app.SetScriptVersion({_SCRIPT_VERSION})",
        f'app.Open("{p}")',
        f"cem.FEMAnalyze({setup_name!r},{{'Redo Mesh':True}})",
        f'app.SaveAs("{p}")',
    ]) + "\n"