"""EDA 基础配置 — 路径检测与环境变量加载。

EDI_PATH / TURBOCHARTS_PATH — 环境变量优先，否则自动检测同级 EXE
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sys as _sys
from dotenv import load_dotenv

# 冻结模式下从 EXE 所在目录加载 .env
if getattr(_sys, "frozen", False):
    _env_path = Path(_sys.executable).parent / ".env"
    load_dotenv(_env_path)
else:
    load_dotenv()

from servers.settings import get_settings as _get_settings
from servers.utils import validate_file, error_response
_settings = _get_settings()
EDA_GRPC_SERVER = _settings.eda_grpc_server

# 仿真器件目录类型（SP/HB/XDB），供 simulation_components 与 project_manage 共用
SIM_COMPONENT_TYPES = {"SParameter", "HarmonicBalance", "XDB"}

# ── EDI / TurboCharts 路径：优先 .env，否则自动检测 ──
# 探测基目录 = EDI 安装根（simulation_tool 的上一级）。旧实现用「应用根目录的上一级」：
# frozen 下 = dist、源码态 = 仓库上一级，都不含 EDI.exe / turbocharts_app.exe ⇒
# 自动探测恒失效、一直靠 .env 兜（2026-10-21 实测 DEP-1：exe 形态 .env 一空就暴露）。
from servers.simulation_tool.config import EDI_INSTALL_DIR as _EDI_INSTALL_DIR

_PARENT = Path(_EDI_INSTALL_DIR)

_EDI_CANDIDATES = ["EDI.exe", "EDA-PMDS.exe", "CAIS.exe"]
_TC_CANDIDATES = ["turbocharts_app.exe", "turbocharts.exe", "TurboCharts.exe"]


def _find_first(*candidates: str) -> str:
    """返回第一个存在的文件路径，都不存在则返回空串（供调用方区分「未检测到」）。"""
    for name in candidates:
        p = _PARENT / name
        if p.is_file():
            return str(p)
    return ""


EDI_PATH = _settings.edi_path or _find_first(*_EDI_CANDIDATES)
TURBOCHARTS_PATH = _settings.turbocharts_path or _find_first(*_TC_CANDIDATES)


def validate_project_path(project_path: str) -> str:
    """校验 .epp 工程路径，返回规范化后的绝对路径。"""
    return validate_file(project_path, (".epp",))


def require_project_path(project_path: str) -> tuple[str, dict[str, Any] | None]:
    """校验 .epp 工程路径，返回 (resolved, error)；失败返回统一错误字典而非抛异常。"""
    try:
        return validate_project_path(project_path), None
    except FileNotFoundError as e:
        return "", error_response("FILE_NOT_FOUND", str(e))
    except ValueError as e:
        return "", error_response("INVALID_PATH", str(e))
