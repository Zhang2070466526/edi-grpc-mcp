"""MCP Resources（服务状态类）— 服务概览、实时状态、工程目录。

  edi://service/overview — 服务版本、协议版本、gRPC 目标、安全规则
  edi://service/status   — 实时运行时状态（gRPC 通道、队列占用、工具指纹）
  edi://projects         — 工作区工程目录清单（名称/路径/大小）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import grpc
import hashlib

from servers import mcp, __version__ as SERVER_VERSION
from servers.eda.config import EDA_GRPC_SERVER
from servers.eda.grpc_client import get_cached_channel, is_queue_busy
from servers.eda.project_manage import list_epp_projects
from servers.eda.workspace_ops import get_current_workspace
from servers.multimodal_vision import OPENCLAW_WORKSPACE_PATH


@mcp.resource(
    "edi://service/overview",
    name="Service Overview",
    title="EDI gRPC MCP 服务概览",
    description="当前 MCP 协议版本、服务能力、安全规则和 gRPC 目标。",
    mime_type="application/json",
)
def resource_service_overview() -> dict[str, Any]:
    """返回服务能力概览。不包含密钥、路径或敏感信息。"""
    workspace_enabled = OPENCLAW_WORKSPACE_PATH is not None
    grpc_host = EDA_GRPC_SERVER or "127.0.0.1:50055"

    return {
        "server_name": "EDI gRPC MCP",
        "server_version": SERVER_VERSION,
        "protocol_version": "2",     # gRPC 协议版本
        "tool_api_version": "3",    # 仿真器件工具 API 版本
        "mode": "local",
        "grpc_target": grpc_host,
        "workspace_copy_enabled": workspace_enabled,
        "simulation_components": ["SParameter", "HarmonicBalance", "XDB"],
        "safety_rules": {
            "do_not_retry_unknown_outcome": True,
            "clear_schematic_requires_confirmation": True,
            "workspace_copy_requires_explicit_user_request": True,
            "show_image_uses_native_imagecontent": True,
        },
    }


@mcp.resource(
    "edi://service/status",
    name="Service Status",
    title="EDI gRPC MCP 实时状态",
    description="当前 gRPC 通道状态、队列占用、通道缓存等运行时信息。",
    mime_type="application/json",
)
def resource_service_status() -> dict[str, Any]:
    """返回运行时状态，与 get_service_status 共享数据源。"""
    target = EDA_GRPC_SERVER or "127.0.0.1:50055"
    ch = get_cached_channel(target)
    state = "unknown"
    if ch is not None:
        try:
            grpc.channel_ready_future(ch).result(timeout=1)
            state = "ready"
        except Exception:
            state = "unhealthy"

    return {
        "grpc_target": target,
        "channel_state": state,
        "channel_cached": ch is not None,
        "queue_locked": is_queue_busy(),
        "tool_count": len(_current_tools_names()),
        "tools_hash": _current_tools_hash(),
    }


def _current_workspace() -> str:
    """通过 GET_CURRENT_WORKSPACE 查询当前实际加载的工作区目录（以接口返回为准）。

    不再本地猜测 ~/EDI-Workspace/projects，也不读 projects_dir 配置：
    接口返回什么目录，就用什么目录当工作区。
    """
    result = get_current_workspace()
    if not result.get("success"):
        return ""
    return (result.get("details") or {}).get("workspace_path", "")


def _current_tools_names() -> list[str]:
    """返回当前已注册工具的排序名列表（与 /ready 的 tools_hash 同一数据源）。"""
    return sorted(t.name for t in mcp._tool_manager._tools.values())


def _current_tools_hash() -> str:
    """工具集版本指纹（与 /ready 的 tools_hash 同一算法：md5(sorted 名)[:8]）。"""
    return hashlib.md5(",".join(_current_tools_names()).encode()).hexdigest()[:8]


@mcp.resource(
    "edi://projects",
    name="Projects Directory",
    title="工作区工程目录",
    description="当前工作区所有 .epp 工程：名称/路径/大小。",
    mime_type="application/json",
)
def resource_projects_directory() -> dict[str, Any]:
    """查询当前工作区（GET_CURRENT_WORKSPACE）并扫描其 projects 子目录。"""
    workspace = _current_workspace()
    if not workspace:
        return {
            "workspace": "",
            "projects_dir": "",
            "count": 0,
            "projects": [],
            "warning": "未获取到当前工作区（EDI 未运行或 GET_CURRENT_WORKSPACE 失败）",
        }
    projects_dir = Path(workspace) / "projects"
    if projects_dir.is_dir():
        result = list_epp_projects(str(projects_dir))
    else:
        result = {"count": 0, "projects": []}
    return {
        "workspace": workspace,
        "projects_dir": str(projects_dir),
        "count": result.get("count", 0),
        "projects": [
            {"name": p["name"], "path": p["path"], "size": p.get("size", 0)}
            for p in result.get("projects", [])
        ],
    }