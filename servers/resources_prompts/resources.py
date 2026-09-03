"""MCP Resources — 只读上下文，客户端通过 resources/list 和 resources/read 访问。

5 个 Resource：
  edi://service/overview              — 服务版本、协议版本、gRPC 目标、安全规则
  edi://service/status                — 实时运行时状态（gRPC 通道、队列占用）
  edi://reference/simulation-components — 仿真器件参数目录（与 get_schema 同源）
  edi://reference/operation-guide     — 操作安全约束（创建/删除/导入规则）
  edi://reference/error-codes         — 错误码词典（状态码→含义→建议动作）
"""

from __future__ import annotations

from typing import Any

import grpc

from servers import mcp, __version__ as SERVER_VERSION
from servers.eda.simulation_components import _load_catalog
from servers.eda.config import EDA_GRPC_SERVER
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
    "edi://reference/simulation-components",
    name="Simulation Components Reference",
    title="仿真器件参数参考",
    description="仿真器件支持的公开参数名、gRPC 参数名、值类型、单位和创建/更新权限。",
    mime_type="application/json",
)
def resource_simulation_components() -> dict[str, Any]:
    """直接复用参数目录，不维护两套定义。"""
    return _load_catalog()


@mcp.resource(
    "edi://reference/operation-guide",
    name="Operation Guide",
    title="EDI gRPC MCP 操作规则",
    description="创建、修改、删除仿真器件和网表导入的安全约束。",
    mime_type="text/markdown",
)
def resource_operation_guide() -> str:
    """返回 Markdown 格式的操作规则。"""
    return (
        "# EDI gRPC MCP 操作规则\n\n"
        "- 查询工程时优先使用 `get_project_summary`。\n"
        "- 创建或修改仿真器件前先查询参数 Schema。\n"
        "- `create_simulation_component` 每次都会创建新实例。\n"
        "- `TIMEOUT` 或 `STREAM_DISCONNECTED` 后禁止自动重试创建或导入。\n"
        "- `delete_simulation_component` 按实例名精确删除，删除前先确认目标。\n"
        "- `set_component_active_state` 是确定性设置，不是状态切换。\n"
        "- `clear_before_import=true` 必须获得用户明确确认，同时传 `confirm_clear=true`。\n"
        "- `show_image` 使用原生 MCP ImageContent 返回图片，不复制文件，不输出 MEDIA 文本。\n"
        "- 不要检查或读取服务端环境变量。\n"
        "- 不要猜测工程文件路径，先通过 `list_epp_projects` 获取。\n"
        "- 产生输出文件（截图/图表/报告）或采用默认值时，先告知用户输出位置或默认值，询问是否需要调整。\n"
    )


@mcp.resource(
    "edi://service/status",
    name="Service Status",
    title="EDI gRPC MCP 实时状态",
    description="当前 gRPC 通道状态、队列占用、通道缓存等运行时信息。",
    mime_type="application/json",
)
def resource_service_status() -> dict[str, Any]:
    """返回运行时状态，与 get_service_status 共享数据源。"""
    from servers.eda.config import EDA_GRPC_SERVER as _gs
    from servers.eda.grpc_client import get_cached_channel, is_queue_busy

    target = _gs
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
    }


@mcp.resource(
    "edi://reference/error-codes",
    name="Error Code Reference",
    title="gRPC 错误码词典",
    description="所有 gRPC 工具返回的状态码含义、原因及建议动作。",
    mime_type="text/markdown",
)
def resource_error_codes() -> str:
    """错误码词典：帮助 LLM 根据 status 选择合适的重试/排查策略。"""
    return (
        "# EDI gRPC MCP 错误码词典\n\n"
        "| 状态 | 含义 | 建议动作 |\n"
        "|---|---|---|\n"
        "| SUCCEEDED | 任务成功完成 | — |\n"
        "| FAILED | EDI 明确返回失败 | 查看 message/ads_output，修正参数后重试 |\n"
        "| REJECTED | EDI 未受理（参数/权限） | 检查参数，不要直接重试 |\n"
        "| QUEUE_TIMEOUT | 等待执行槽位超时 | 稍后重试，检查是否有长任务卡住 |\n"
        "| TIMEOUT | 总超时，EDI 结果未知 | 延长 timeout 或检查仿真进度 |\n"
        "| STREAM_DISCONNECTED | FetchEvent 流中断，结果未知 | 确认 EDI 进程存活，可重试一次 |\n"
        "| GRPC_UNAVAILABLE | 无法连接 EDI gRPC（掉线） | 手动启动 EDI 软件后重试 |\n"
        "| PAYLOAD_TOO_LARGE | EDI 返回消息过大（>256MB） | 日志已部分接收，考虑延长仿真时间或减少日志量 |\n"
        "| PROTOCOL_MISMATCH | client_uuid/task_id/event_type 不一致 | 调用链错误，不要重试，先排查代码 |\n"
        "| TASK_NOT_FOUND | 任务不存在（过期/重启） | 重新提交仿真任务 |\n"
        "\n"
        "重要原则：\n"
        "- TIMEOUT / STREAM_DISCONNECTED 时 outcome_known=false，"
        "task_success=null，不要假设仿真失败。\n"
        "- 非幂等操作（create/delete/generate）禁止自动重试。\n"
        "- 查询类操作（list/get_status）可以安全重试一次。\n"
    )
