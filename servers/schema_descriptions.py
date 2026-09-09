"""工具 schema 参数描述注入 — 把 docstring 的 Args: 段同步到 MCP inputSchema。

FastMCP（mcp SDK）默认不解析 docstring，导致 tools/list 的 inputSchema.properties
只有 title+type 而无 description，标准 MCP 客户端（Claude Code 等）无法读懂参数。

这里在工具全部注册完成后，解析每个工具的 Args: 段，把描述注入到
``tool.parameters["properties"][name]["description"]``，让 docstring 成为参数的
单一事实源——改 docstring 即自动同步到 schema，无需在函数签名里重复 Annotated[Field]。
"""

from __future__ import annotations

import logging
import re

# 参数行：缩进 + 参数名 + ":" + 描述（参数名是合法 Python 标识符，描述可含任意内容含冒号）
_ARG_LINE_RE = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

_logger = logging.getLogger("schema_descriptions")


def parse_args_descriptions(doc: str | None) -> dict[str, str]:
    """解析 docstring 的 Args: 段，返回 {参数名: 描述}。

    支持 Google 风格 Args: 段，含多行续写（缩进的无冒号行拼到上一条描述）。
    无 Args: 段返回空字典。
    """
    if not doc:
        return {}
    lines = doc.splitlines()

    args_idx = next((i for i, ln in enumerate(lines) if ln.strip() == "Args:"), None)
    if args_idx is None:
        return {}

    descs: dict[str, str] = {}
    current: str | None = None
    for ln in lines[args_idx + 1:]:
        m = _ARG_LINE_RE.match(ln)
        if m:
            current = m.group(1)
            descs[current] = m.group(2).strip()
            continue
        if not ln.strip():
            # 空行：Args 段内部的分隔或结尾，继续探测
            continue
        if ln[0] in (" ", "\t") and current:
            # 缩进的续写行，拼到上一条描述
            descs[current] += " " + ln.strip()
            continue
        # 顶格的非空行：Args 段已结束
        break
    return descs


def inject_tool_descriptions(mcp) -> tuple[int, int]:
    """遍历已注册工具，把 Args: 描述注入 inputSchema。返回 (注入数, 仍缺失的参数数)。

    只补「还没有 description」的字段（若已用 Annotated[Field] 显式提供则跳过），
    不覆盖已有值。依赖 mcp._tool_manager 私有属性（SDK 升级可能变动），用 getattr 防御。
    """
    injected = 0
    missing = 0

    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", {}) if tool_manager is not None else {}
    if not tools:
        return 0, 0

    for tool in tools.values():
        fn = getattr(tool, "fn", None)
        descs = parse_args_descriptions(getattr(fn, "__doc__", None))

        parameters = getattr(tool, "parameters", None)
        if not isinstance(parameters, dict):
            continue
        properties = parameters.get("properties", {})
        if not isinstance(properties, dict):
            continue

        for name, desc in descs.items():
            field = properties.get(name)
            if not isinstance(field, dict):
                continue
            if field.get("description"):
                continue  # 已有显式 description，不覆盖
            field["description"] = desc
            injected += 1

        missing += sum(
            1 for f in properties.values()
            if isinstance(f, dict) and not f.get("description")
        )

    _logger.info("tool schema descriptions injected=%d missing=%d", injected, missing)
    return injected, missing
