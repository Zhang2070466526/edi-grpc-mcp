"""MCP Prompts（报告/诊断类）— 报告生成、错误诊断。

  create_simulation_report — 查询工程→生成曲线→渲染 PDF/DOCX
  troubleshoot_edi_error   — 错误诊断：读 error-codes → 查服务状态 → 建议动作
"""

from __future__ import annotations

from pathlib import Path as _Path
from typing import Any

from servers import mcp


@mcp.prompt(
    name="create_simulation_report",
    title="生成仿真报告",
    description="协调多个工具完成仿真报告：查询工程→确认结果→生成曲线→整理数据→渲染 PDF/DOCX。",
)
def prompt_create_simulation_report(
    project_path: str,
    output_path: str,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """生成仿真报告的完整工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径。
        output_path: 输出文件绝对路径（.pdf 或 .docx）。
        overwrite: 输出文件已存在时是否覆盖。
    """
    ext = _Path(output_path).suffix.lower() if output_path else ".pdf"
    file_type = ext.lstrip(".")

    steps = [
        f"1. 确认输出路径：`{output_path or '（请提供）'}`（{file_type.upper()} 格式）。",
        "2. 调用 `get_project_summary` 获取工程基本信息作为报告封面和简介素材。",
        "3. 查询已有仿真结果（`get_simulation_async_result` 或 `list_eda_tasks`），不自动重新仿真。",
        "   没有结果时询问用户是否执行仿真。",
        "4. 调用 `list_result_curves` 获取 RAW 中实际可用的曲线名。",
        "5. 根据用户需求选择关键曲线，调用 `turbocharts_convert` 生成曲线图片。",
        "6. 如需拓扑图，调用 `capture_schematic` 截取原理图。",
        "7. 整理电参数表（spec_table）：只填入有真实测量值的指标，无要求值时结果列填'未判定'。",
        "8. 整理器件选型表（components）：type/model/manufacturer/specs 四项均为字符串，不得猜测。",
        "9. 收集 description（产品简介）和 conclusion（结论文字）。",
        f"10. 调用 `generate_simulation_report` 渲染报告：output_path=\"{output_path or '（请提供）'}\"，overwrite={'true' if overwrite else 'false'}",
        "",
        "重要约束：",
        "- 不要自动启动新的仿真，除非用户明确要求。",
        "- log_complete=false 时不能断言日志完整。",
        "- TIMEOUT/STREAM_DISCONNECTED 时禁止自动重试，也不写成功结论。",
        "- 器件厂家和规格不得根据型号名称猜测。",
        "- 只有同时具备测量值和判定要求时才能写'合格/不合格'。",
    ]

    return [{"role": "user", "content": "\n".join(steps)}]


@mcp.prompt(
    name="troubleshoot_edi_error",
    title="诊断 EDI 调用错误",
    description="根据 gRPC 返回的 status/error_code 查错误码词典、检查服务状态，给出排查建议。",
)
def prompt_troubleshoot_edi_error(
    status: str,
    error_code: str = "",
) -> list[dict[str, Any]]:
    """诊断 EDI 工具调用失败的工作流模板。

    Args:
        status: gRPC 返回的 status（如 TIMEOUT/STREAM_DISCONNECTED/GRPC_UNAVAILABLE）。
        error_code: 工具的 error_code（如 FILE_NOT_FOUND/INVALID_PARAMETERS）。
    """
    steps = [
        f"诊断目标：status={status}" + (f", error_code={error_code}" if error_code else ""),
        "",
        "步骤：",
        "1. 读取 `edi://reference/error-codes` 资源，查找该状态码的含义和建议动作。",
        "2. 调用 `get_service_status` 检查 gRPC 通道是否健康、是否有任务在排队。",
    ]

    if status in ("TIMEOUT", "STREAM_DISCONNECTED"):
        steps += [
            "3. 不要自动重试。告知用户：EDI 任务结果未知（outcome_known=false）。",
            "4. 建议：确认 EDI 是否仍在运行，查看 ads_output 尾部有无报错。",
        ]
    elif status == "GRPC_UNAVAILABLE":
        steps += [
            "3. 确认 EDI 是否已启动、gRPC 地址端口是否正确。",
            "4. 若 EDI 已掉线，请手动启动 EDI 软件后重试。",
        ]
    elif status in ("QUEUE_TIMEOUT",):
        steps += [
            "3. 调用 `list_eda_tasks` 查看是否有长任务卡住执行槽位。",
            "4. 如无任务运行，稍后重试即可。",
        ]
    elif status == "REJECTED":
        steps += [
            "3. 查看返回的 message 了解拒绝原因（通常是参数错误或权限问题）。",
            "4. 修正参数后重试，不要用相同参数反复调用。",
        ]
    else:
        steps += [
            "3. 根据 error_code 判断是 MCP 层校验错误还是 EDI 业务错误。",
            "4. MCP 层问题（FILE_NOT_FOUND/INVALID_PARAMETERS 等）修正参数重试。",
        ]

    return [{"role": "user", "content": "\n".join(steps)}]