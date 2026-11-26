"""MCP Prompts（报告/诊断类）— 报告生成、错误诊断。

  create_simulation_report — 查询工程→生成曲线→渲染 PDF/DOCX
  troubleshoot_edi_error   — 错误诊断：读 error-codes → 查服务状态 → 建议动作
"""

from __future__ import annotations

from typing import Any

from servers import mcp


@mcp.prompt(
    name="create_simulation_report",
    title="生成仿真报告",
    description="协调多个工具完成仿真报告：查询工程→确认结果→生成曲线→整理数据→渲染 PDF/DOCX。",
)
def prompt_create_simulation_report(
    project_path: str = "",
    output_path: str = "",
    result_path: str = "",
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """生成仿真报告的完整工作流模板。

    Args:
        project_path: .epp 工程文件绝对路径（与 result_path 至少给一个）。
        output_path: 输出目录绝对路径（产出 document.pdf / document.docx / report.json）。
        result_path: 已有 RAW 结果文件绝对路径；给了就走「不依赖工程」的分支。
        overwrite: 输出目录里已存在的目标文件是否覆盖。
    """
    if not project_path and not result_path:
        return [{"role": "user", "content":
                 "需要 `project_path`（.epp 工程）或 `result_path`（已有 RAW 结果文件）至少一个，才能生成报告。"}]

    out = output_path or "（请提供）"
    steps: list[str] = [f"1. 确认输出目录：`{out}`（将生成 document.pdf / document.docx / report.json）。"]
    n = 1

    def add(text: str) -> None:
        nonlocal n
        n += 1
        steps.append(f"{n}. {text}")

    if result_path:
        add(f"数据源为已有 RAW：`{result_path}`，直接用它，不重新仿真；工程信息（若有）仅作封面素材。")
        add(f"调用 `list_result_curves`（result_path）列出 RAW 里实际可用的曲线名与单位。HB 类只信 dBm/real，其余单位会被引擎静默回退。")
        add(f"按用户需求选关键曲线，调用 `turbocharts_convert`（raw_path=该 RAW）生成曲线图。")
        add("如需拓扑图：工程已在 EDI 打开才用 `capture_schematic`，取不到就跳过，不要为了截图去启动 EDI。")
    else:
        add(f"调用 `get_project_summary`（project_path=`{project_path}`）获取工程基本信息作为封面和简介素材。")
        add("查询已有仿真结果（`get_simulation_async_result` 或 `list_eda_tasks`），不自动重新仿真。")
        steps.append("   没有结果时询问用户是否执行仿真。")
        add("调用 `list_result_curves` 获取 RAW 中实际可用的曲线名与单位。")
        add("根据用户需求选择关键曲线，调用 `turbocharts_convert` 生成曲线图片。")
        add("如需拓扑图，调用 `capture_schematic` 截取原理图。")

    add("整理电参数表（spec_table）：只填入有真实测量值的指标，无要求值时结果列填'未判定'。")
    add("整理器件选型表（components）：type/model/manufacturer/specs 四项均为字符串，不得猜测。")
    add("收集 description（产品简介）和 conclusion（结论文字）。")
    add(f"把电参数表/器件表/曲线/简介/结论组装成 report JSON，调用 `export_simulation_report` 渲染"
        f"（output_dir=\"{out}\"，overwrite={str(overwrite).lower()}）；JSON 较大时先落盘再传 report_path，"
        f"目录已有同名文件要么换目录要么 overwrite=true，不要凭空造新文件名。")

    steps += [
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