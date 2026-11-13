"""内部工具/prompt/resource 过滤回归 —— 依赖 EDI gRPC(50055) 的可按下架、对智能体彻底不可见。

开关 `MCP_HIDE_INTERNAL_TOOLS`：
  0 = 全量暴露（默认）；1 = 下架内部工具（黑名单 `registry_server._INTERNAL_*`）；
  2 = 只保留「内部工具 + 无依赖计算工具」（白名单），其余（ANSYS/CST/LASPCEM/图片/文档）全下架；
  3 = 下架软 IP（design_chip）+ LASPCEM 电磁仿真工具。
代码保留、可逆（改回开关重启即恢复）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import servers.registry_server  # noqa: F401
from servers import mcp
from servers.registry_server import _INTERNAL_PROMPTS, _INTERNAL_RESOURCES, _INTERNAL_TOOLS, _MODE3_TOOLS
from servers.utils import registered_tools

_REPO = Path(__file__).resolve().parent.parent


def test_internal_lists_are_valid():
    """三个名单里都是真实注册的名字，且工具名单覆盖了 gRPC 那批。"""
    tool_names = {t.name for t in registered_tools(mcp)}
    assert _INTERNAL_TOOLS <= tool_names, f"工具名单含未注册项: {sorted(_INTERNAL_TOOLS - tool_names)}"
    assert len(_INTERNAL_TOOLS) >= 30, f"工具名单过小（{len(_INTERNAL_TOOLS)}），疑似漏了 gRPC 工具"
    assert _MODE3_TOOLS <= tool_names, f"mode3 名单含未注册项: {sorted(_MODE3_TOOLS - tool_names)}"

    assert _INTERNAL_PROMPTS <= set(mcp._prompt_manager._prompts), \
        f"prompt 名单含未注册项: {sorted(_INTERNAL_PROMPTS - set(mcp._prompt_manager._prompts))}"
    assert _INTERNAL_RESOURCES <= set(mcp._resource_manager._resources), \
        f"resource 名单含未注册项: {sorted(_INTERNAL_RESOURCES - set(mcp._resource_manager._resources))}"


def test_switch_off_keeps_all():
    """默认开关 0：内部工具仍在注册表（行为与旧版一致）。"""
    names = {t.name for t in registered_tools(mcp)}
    assert names & _INTERNAL_TOOLS, "默认下内部工具应仍在注册表"


def test_switch_on_hides_tools_prompts_resources():
    """开关 1：子进程里工具/prompt/resource 都从注册表消失。"""
    env = os.environ.copy()
    env["MCP_HIDE_INTERNAL_TOOLS"] = "1"
    code = (
        "import servers.registry_server\n"
        "from servers import mcp\n"
        "from servers.registry_server import _INTERNAL_TOOLS, _INTERNAL_PROMPTS, _INTERNAL_RESOURCES\n"
        "tools = set(mcp._tool_manager._tools)\n"
        "prompts = set(mcp._prompt_manager._prompts)\n"
        "resources = set(mcp._resource_manager._resources)\n"
        "assert not (tools & _INTERNAL_TOOLS), sorted(tools & _INTERNAL_TOOLS)\n"
        "assert not (prompts & _INTERNAL_PROMPTS), sorted(prompts & _INTERNAL_PROMPTS)\n"
        "assert not (resources & _INTERNAL_RESOURCES), sorted(resources & _INTERNAL_RESOURCES)\n"
        "print('hidden ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"开关开启后内部项仍可见:\n{r.stderr}"


def test_switch_2_keeps_only_internal_and_calc():
    """开关 2：只保留内部工具 + 无依赖计算工具，其余（ANSYS/CST/LASPCEM/图片/文档）全下架。"""
    from servers.registry_server import _CALC_TOOLS

    env = os.environ.copy()
    env["MCP_HIDE_INTERNAL_TOOLS"] = "2"
    code = (
        "import servers.registry_server\n"
        "from servers import mcp\n"
        "from servers.registry_server import _INTERNAL_TOOLS, _CALC_TOOLS\n"
        "tools = set(mcp._tool_manager._tools)\n"
        "keep = _INTERNAL_TOOLS | _CALC_TOOLS | {'describe_tool'}\n"
        "assert tools <= keep, sorted(tools - keep)\n"          # 剩下的必须都在白名单内
        "assert _CALC_TOOLS <= tools, '计算工具应保留'\n"
        "assert 'open_hfss_project' not in tools\n"             # ANSYS 应下架
        "assert 'cst_solve_async' not in tools\n"               # CST 应下架
        "assert 'run_laspcem_solve_async' not in tools\n"       # LASPCEM 应下架
        "print('mode2 ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"开关 2 行为不符:\n{r.stderr}"


def test_switch_3_hides_design_chip_and_laspcem():
    """开关 3：下架软 IP（design_chip）+ LASPCEM 工具，其余（含内部 gRPC 工具）保留。"""
    env = os.environ.copy()
    env["MCP_HIDE_INTERNAL_TOOLS"] = "3"
    code = (
        "import servers.registry_server\n"
        "from servers import mcp\n"
        "from servers.registry_server import _MODE3_TOOLS\n"
        "tools = set(mcp._tool_manager._tools)\n"
        "assert not (tools & _MODE3_TOOLS), sorted(tools & _MODE3_TOOLS)\n"
        "assert 'design_chip' not in tools\n"
        "assert 'run_laspcem_solve_async' not in tools\n"
        "assert 'open_edi_project' in tools\n"          # 内部 gRPC 工具应保留
        "assert 'open_hfss_project' in tools\n"         # HFSS 应保留
        "print('mode3 ok')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(_REPO), capture_output=True, text=True)
    assert r.returncode == 0, f"开关 3 行为不符:\n{r.stderr}"


def test_error_hints_do_not_leak_hidden_tools():
    """错误 hint 里引用下架工具名，只有当「可见工具会产生该错误」时才构成泄漏。

    死条目（错误只由下架工具产生）不构成泄漏——智能体触发不了那个错误，hint 不会出现。
    只对「可见工具会产生该错误码 + hint 引用了下架工具名」的报警。
    """
    import inspect

    from servers.chat.service import ERROR_CODES
    from servers.utils import _DEFAULT_HINTS

    leaks = []
    for source_name, hints in (("_DEFAULT_HINTS", _DEFAULT_HINTS), ("ERROR_CODES", ERROR_CODES)):
        for code, hint in hints.items():
            if not isinstance(hint, str):
                continue
            refs = sorted(t for t in _INTERNAL_TOOLS if t in hint)
            if not refs:
                continue
            # 找出「可见工具」里谁会产生这个错误码（源码里含该 code 字符串）。
            # 下架工具产生的错误对智能体不可达，不计入产生者。
            producers = []
            for t in registered_tools(mcp):
                if t.name in _INTERNAL_TOOLS:
                    continue
                try:
                    src = inspect.getsource(t.fn)
                except Exception:
                    src = ""
                if f'"{code}"' in src or f"'{code}'" in src:
                    producers.append(t.name)
            if producers:
                leaks.append((source_name, code, hint, refs, sorted(producers)))

    assert not leaks, f"可见工具的错误 hint 泄漏了下架工具名：{leaks}"