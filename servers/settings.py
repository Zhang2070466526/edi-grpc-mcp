"""统一配置加载 — 集中管理所有环境变量读取和默认值。

所有模块通过 get_settings() 获取配置，不直接读 os.getenv。
配置一经加载不可变（frozen dataclass），启动时校验关键字段。

使用方式：
    from servers.settings import get_settings
    s = get_settings()
    print(s.eda_grpc_server)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()
_logger = logging.getLogger("settings")


def _read_str(name: str, default: str = "") -> str:
    """读取字符串环境变量，为空时返回默认值。"""
    raw = os.getenv(name, "").strip()
    return raw if raw else default


def _read_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量（true/yes/1/on → True）。"""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取整数环境变量，带范围限制和非法值警告。"""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, min(int(raw), maximum))
    except ValueError:
        _logger.warning("%s=%s is invalid; using %d", name, raw, default)
        return default


@dataclass(frozen=True)
class Settings:
    """MCP 服务全部配置，单例不可变。

    字段按类别分组：服务器、路径、LLM、视觉、报告、工作区。
    路径类字段仅存储环境变量原始值；自动检测逻辑由各模块自行实现。
    """

    # ── 服务器 ──
    eda_grpc_server: str = field(default_factory=lambda: _read_str("EDA_GRPC_SERVER", "127.0.0.1:50055"))
    mcp_host: str = field(default_factory=lambda: _read_str("MCP_HOST", "127.0.0.1"))
    mcp_port: int = field(default_factory=lambda: _read_int("MCP_PORT", 50026, 1, 65535))
    mcp_transport: str = field(default_factory=lambda: _read_str("MCP_TRANSPORT", "streamable-http"))
    mcp_stateless_http: bool = field(default_factory=lambda: _read_bool("MCP_STATELESS_HTTP", True))

    # ── 路径（环境变量覆盖优先，空字符串 = 未设置 = 自动检测）──
    edi_path: str = field(default_factory=lambda: _read_str("EDI_PATH"))
    turbocharts_path: str = field(default_factory=lambda: _read_str("TURBOCHARTS_PATH"))
    aedt_path: str = field(default_factory=lambda: _read_str("AEDT_PATH"))

    # ── LLM / Chat ──
    llm_api_key: str = field(default_factory=lambda: _read_str("LLM_API_KEY"))
    llm_base_url: str = field(default_factory=lambda: _read_str("LLM_BASE_URL"))
    llm_model: str = field(default_factory=lambda: _read_str("LLM_MODEL"))

    # ── 视觉分析 ──
    vision_api_key: str = field(default_factory=lambda: _read_str("VISION_API_KEY"))
    vision_base_url: str = field(default_factory=lambda: _read_str("VISION_BASE_URL"))
    vision_model: str = field(default_factory=lambda: _read_str("VISION_MODEL"))
    vision_timeout: int = field(default_factory=lambda: _read_int("VISION_TIMEOUT_SECONDS", 45, 5, 120))
    vision_max_mb: int = field(default_factory=lambda: _read_int("VISION_MAX_IMAGE_MB", 10, 1, 100))

    # ── 报告渲染 ──
    report_render_url: str = field(
        default_factory=lambda: _read_str("REPORT_RENDER_URL", "http://127.0.0.1:17867/api/v1/reports/render"))
    report_timeout: int = field(default_factory=lambda: _read_int("REPORT_RENDER_TIMEOUT_SECONDS", 45, 5, 120))

    # ── 工作区 ──
    openclaw_workspace: str = field(default_factory=lambda: _read_str("OPENCLAW_WORKSPACE"))

    def validate(self) -> list[str]:
        """启动时校验关键配置，返回问题列表。不阻断启动，仅返回警告。"""
        issues: list[str] = []
        # ── gRPC 地址 ──
        if ":" not in self.eda_grpc_server:
            issues.append(f"EDA_GRPC_SERVER 格式无效（需要 host:port）: {self.eda_grpc_server}")
        else:
            host, port_str = self.eda_grpc_server.rsplit(":", 1)
            try:
                port = int(port_str)
                if port < 1 or port > 65535:
                    issues.append(f"EDA_GRPC_SERVER 端口越界: {port}")
            except ValueError:
                issues.append(f"EDA_GRPC_SERVER 端口不是整数: {port_str}")
        # ── 传输方式 ──
        if self.mcp_transport not in ("stdio", "streamable-http"):
            issues.append(f"MCP_TRANSPORT 不支持（streamable-http / stdio）: {self.mcp_transport}")
        return issues


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回全局唯一配置单例（lru_cache 缓存）。"""
    return Settings()
