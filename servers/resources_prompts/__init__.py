"""MCP Resources & Prompts — 6 个 Resource + 8 个 Prompt。

按语义拆为 6 个文件，每个文件的 @mcp.resource() / @mcp.prompt() 装饰器在 import 时触发注册。

resources_service.py — 服务状态类（3 个 Resource）：
  edi://service/overview — 服务版本、协议版本、gRPC 目标、安全规则
  edi://service/status   — 实时运行时状态（gRPC 通道、队列占用、工具指纹）
  edi://projects         — 工作区工程目录清单（名称/路径/大小）

resources_reference.py — 参考类（3 个 Resource）：
  edi://reference/simulation-components — 仿真器件参数目录（与 get_schema 同源）
  edi://reference/operation-guide     — 操作安全约束（创建/删除/导入规则）
  edi://reference/error-codes         — 错误码词典（状态码→含义→建议动作）

prompts_project.py — 工程类（2 个 Prompt）：
  inspect_edi_project  — 只读检查工程：概览→变量→器件→仿真配置
  analyze_signal_chain — 追踪信号链路并逐级说明

prompts_simulation.py — 仿真类（2 个 Prompt）：
  run_and_review_simulation — 异步仿真 + 日志分析（含轮询限制）
  assess_anti_burnout       — 抗烧毁评估并按功率裕量排序

prompts_component.py — 器件类（2 个 Prompt）：
  configure_simulation_component — 按需求配置 SP/HB/XDB 器件参数
  select_component                — 从公共/个人模型库选型（含替换闭环）

prompts_report.py — 报告/诊断类（2 个 Prompt）：
  create_simulation_report — 协调多工具生成 PDF/DOCX 仿真报告
  troubleshoot_edi_error   — 诊断 gRPC 调用错误并给排查建议
"""

from servers.resources_prompts.resources_service import (  # noqa: F401
    resource_service_overview,
    resource_service_status,
    resource_projects_directory,
)
from servers.resources_prompts.resources_reference import (  # noqa: F401
    resource_simulation_components,
    resource_operation_guide,
    resource_error_codes,
)
from servers.resources_prompts.prompts_project import (  # noqa: F401
    prompt_inspect_edi_project,
    prompt_analyze_signal_chain,
)
from servers.resources_prompts.prompts_simulation import (  # noqa: F401
    prompt_run_and_review_simulation,
    prompt_assess_anti_burnout,
)
from servers.resources_prompts.prompts_component import (  # noqa: F401
    prompt_configure_simulation_component,
    prompt_select_component,
)
from servers.resources_prompts.prompts_report import (  # noqa: F401
    prompt_create_simulation_report,
    prompt_troubleshoot_edi_error,
)
