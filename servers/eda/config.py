"""EDA 基础配置 — 路径检测与环境变量加载。

EDI_PATH / TURBOCHARTS_PATH — 环境变量优先，否则自动检测同级 EXE
"""

from __future__ import annotations

from pathlib import Path

import sys as _sys
from dotenv import load_dotenv

# 冻结模式下从 EXE 所在目录加载 .env
if getattr(_sys, "frozen", False):
    _env_path = Path(_sys.executable).parent / ".env"
    load_dotenv(_env_path)
else:
    load_dotenv()

from servers.settings import get_settings as _get_settings
from servers.utils import validate_file
_settings = _get_settings()
EDA_GRPC_SERVER = _settings.eda_grpc_server
MCP_TRANSPORT = _settings.mcp_transport if _settings.mcp_transport else None

# 仿真器件目录类型（SP/HB/XDB），供 simulation_components 与 project_manage 共用
SIM_COMPONENT_TYPES = {"SParameter", "HarmonicBalance", "XDB"}

# ── 应用根目录检测 ──

if getattr(_sys, "frozen", False):
    _APP_ROOT = Path(_sys.executable).parent.resolve()
else:
    _APP_ROOT = Path(__file__).resolve().parent.parent.parent  # servers/eda/ → servers/ → 项目根

# ── EDI / TurboCharts 路径：优先 .env，否则自动检测 ──
_PARENT = _APP_ROOT.parent

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
