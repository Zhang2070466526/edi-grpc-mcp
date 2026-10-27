"""MCP Resources（参考类）— 仿真器件参数目录、操作规则、错误码词典。

  edi://reference/simulation-components — 仿真器件参数目录（与 get_schema 同源）
  edi://reference/operation-guide     — 操作安全约束（创建/删除/导入规则）
  edi://reference/error-codes         — 错误码词典（状态码→含义→建议动作）
"""

from __future__ import annotations

from typing import Any

from servers import mcp
from servers.eda.simulation_components import _load_catalog


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


@mcp.resource(
    "edi://reference/tr-simulation-guide",
    name="TR Simulation Guide",
    title="TR 仿真指标与链路字段",
    description=("run_tr_simulation 的 23 个精确指标名（单位/实现方式/必需链路字段）与 13 个"
                 "链路字段（类型/单位/默认值）。调用前读本资源，不要猜参数名。"),
    mime_type="text/markdown",
)
def resource_tr_simulation_guide() -> str:
    """由代码里的 _INDICATOR_CATALOG / _LINK_FIELDS 生成，和工具校验同源（不会文档漂移）。"""
    from servers.simulation_tool.tr_simulation import (
        TR_CONTRACT_VERSION,
        _INDICATOR_CATALOG,
        _INDICATOR_REQUIRES,
        _LINK_FIELDS,
    )

    lines = [
        f"# TR 仿真契约 {TR_CONTRACT_VERSION}",
        "",
        "工具：`run_tr_simulation`（TR_Simulator.exe，纯链路仿真）。以下名称/字段是**精确**写法，",
        "不接受简称、别名或表外字段；写错会在 MCP 层直接报错并给出候选。",
        "",
        "## 指标（`indicators` 逗号分隔）",
        "",
        "| 指标名称 | 单位 | 实现方式 | 额外链路参数 | 数值输出 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, meta in _INDICATOR_CATALOG.items():
        lines.append(f"| {name} | {meta['unit']} | {meta['method']} | {meta['extra']} | {meta['output']} |")

    lines += [
        "",
        "## 链路字段（`links[]`，共 13 个）",
        "",
        "| 字段 | 类型 | 必填 | 默认值 | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for field, meta in _LINK_FIELDS.items():
        default = "" if meta["default"] is None else meta["default"]
        lines.append(f"| {field} | {meta['type']} | {'是' if meta['required'] else '否'} "
                     f"| {default} | {meta['doc']} |")

    lines += ["", "## 指标 → 必须提供的器件字段", ""]
    lines.append("| 指标 | 必须给出 |")
    lines.append("| --- | --- |")
    for ind, field in _INDICATOR_REQUIRES.items():
        lines.append(f"| {ind} | `{field}`（器件实例名数组，须在有效链路上且类型正确） |")

    lines += [
        "",
        "## 常用名 → 接口名（程序不会自动转换）",
        "",
        "| 常用名 | 必须传入的名称 |",
        "| --- | --- |",
        "| 输入P-1 / 输入P1dB / 输入1dB压缩点 | IP1dB |",
        "| 输出P1dB / 输出1dB压缩点 | OP1dB |",
        "| 移相精度（RMS） | 移相误差RMS |",
        "| 衰减精度（RMS） | 衰减误差RMS |",
        "",
        "## 输出约定",
        "",
        "- `工作频率` 自动加入每条链路结果，不必重复选择。",
        "- `移相态` / `衰减态` 只生成曲线与 CSV，**不进入** `results[].electrical_parameters`。",
        "- 其余指标进 `results[].electrical_parameters`，未使用的数值为空字符串 `\"\"`。",
        "- 返回值带 `echo`（本次实际生效的 indicators/links/threads）；若请求的指标没出现在结果里，"
        "`warnings` 会点名，别把「没跑」当成「已测」。",
        "",
    ]
    return "\n".join(lines)