"""统一配置加载 — 基于 pydantic-settings 的 BaseSettings，集中管理所有环境变量。

所有模块通过 get_settings() 获取配置，不直接读 os.getenv。
配置一经加载不可变（frozen），类型自动转换（str/int/bool），
字段范围用 Field 声明并校验；业务级格式校验见 validate()。

使用方式：
    from servers.settings import get_settings
    s = get_settings()
    print(s.eda_grpc_server)

注意：字段名（小写下划线）会按大小写不敏感匹配环境变量（如 eda_grpc_server ↔ EDA_GRPC_SERVER）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 固定为项目根目录（servers/ 的上一级），避免依赖当前工作目录。
# frozen（打包）模式下 .env 在 exe 同级，由 start_servers.py 的 load_dotenv 提前加载到环境变量；
# 此处 env_file 指向不存在的路径会被 BaseSettings 静默忽略，仍能读到已加载的环境变量。
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_logger = logging.getLogger("settings")


class Settings(BaseSettings):
    """MCP 服务全部配置，frozen 单例。

    字段按类别分组：服务器、路径、LLM、视觉、报告、工作区。
    路径类字段空字符串 = 未设置 = 自动检测（自动检测逻辑由各模块自行实现）。
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
    )

    # ── 服务器 ──
    eda_grpc_server: str = "127.0.0.1:50055"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=50026, ge=1, le=65535)
    mcp_transport: str = "streamable-http"
    mcp_stateless_http: bool = True
    # MCP 访问令牌：留空则不鉴权；配置后 /mcp 等端点要求 URL 带 ?token= 匹配才放行
    mcp_api_key: str = ""

    # ── 路径（环境变量覆盖优先，空字符串 = 未设置 = 自动检测）──
    edi_path: str = ""
    turbocharts_path: str = ""
    aedt_path: str = ""
    edi_log_dir: str = r"C:\Program Files (x86)\EDI\logs"

    # ── LLM / Chat ──
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # ── 视觉分析 ──
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    vision_timeout: int = Field(default=45, ge=5, le=120)
    vision_max_mb: int = Field(default=10, ge=1, le=100)

    # ── 报告渲染 ──
    report_render_url: str = "http://127.0.0.1:17867/api/v1/reports/render"
    report_timeout: int = Field(default=45, ge=5, le=120)

    # ── 工作区 ──
    openclaw_workspace: str = ""

    def validate(self) -> list[str]:
        """业务级格式校验，返回问题列表。不阻断启动，仅由 start_servers.py 打印警告。"""
        issues: list[str] = []
        # ── gRPC 地址格式（host:port）──
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
