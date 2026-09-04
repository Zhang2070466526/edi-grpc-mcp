"""MCP Resources & Prompts — 6 个 Resource + 8 个 Prompt。"""

from servers.resources_prompts.resources import (  # noqa: F401 — 触发 @mcp.resource() 注册
    resource_service_overview,
    resource_service_status,
    resource_projects_directory,
    resource_simulation_components,
    resource_operation_guide,
    resource_error_codes,
)
from servers.resources_prompts.prompts import (  # noqa: F401 — 触发 @mcp.prompt() 注册
    prompt_inspect_edi_project,
    prompt_run_and_review_simulation,
    prompt_configure_simulation_component,
    prompt_create_simulation_report,
    prompt_troubleshoot_edi_error,
    prompt_assess_anti_burnout,
    prompt_select_component,
    prompt_analyze_signal_chain,
)
