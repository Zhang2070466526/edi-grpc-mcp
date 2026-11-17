"""生成工具清单 —— 从注册表自动渲染。

- docs/TOOL_INDEX.md          全量工具清单（所有已注册工具）
- docs/TOOL_INDEX_FILTERED.md 过滤内部工具后的清单（排除 registry_server._INTERNAL_TOOLS）

用法（仓库根）: python scripts/gen_tool_index.py
清单漂移时 tests/test_tool_index.py 会 FAIL，提示重跑本脚本。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 强制跑在「全量」态再生成：否则 .env 里若开了 MCP_HIDE_INTERNAL_TOOLS=1，
# 注册表里内部工具已被 pop，全量清单会误生成成过滤版。
os.environ["MCP_HIDE_INTERNAL_TOOLS"] = "0"

from servers import mcp  # noqa: E402
from servers.utils import registered_tools  # noqa: E402
import servers.registry_server  # noqa: E402, F401 — 触发工具注册
from servers.registry_server import (  # noqa: E402
    _CALC_TOOLS, _EXE_HIDDEN_TOOLS, _INTERNAL_TOOLS, _MODE3_TOOLS,
)


def render_tool_index(tools: list[tuple[str, str]],
                      title: str = "EDI gRPC MCP 工具清单",
                      note: str = "权威数量见 /ready 的 tool_count") -> str:
    """把 (name, description) 列表渲染成 markdown 清单。"""
    lines = [f"# {title}", ""]
    lines.append(f"> 自动生成，勿手改。{note}。")
    lines.append("")
    lines.append(f"共 {len(tools)} 个工具：")
    lines.append("")
    for name, desc in tools:
        d = (desc or "").split("\n")[0].strip()
        lines.append(f"- `{name}` — {d}")
    return "\n".join(lines) + "\n"


def _mode_tools(tools, predicate) -> list[tuple[str, str]]:
    return [(n, d) for n, d in tools if predicate(n)]


def render_mode_index(tools: list[tuple[str, str]]) -> str:
    """渲染按 MCP_HIDE_INTERNAL_TOOLS 模式分组的清单（口径与 _hide_internal_tools 一致）。"""
    keep_mode2 = _INTERNAL_TOOLS | _CALC_TOOLS | {"describe_tool"}
    sections = [
        ("mode=0（全量暴露，默认）",
         "所有已注册工具；exe 打包版（frozen）会在此基础上无条件去掉 analyze_image。",
         tools),
        ("mode=1（下架内部工具 _INTERNAL_TOOLS）",
         "对外可见面 = 全量 − 内部工具（依赖 EDI gRPC / 独立 exe / 诊断）。",
         _mode_tools(tools, lambda n: n not in _INTERNAL_TOOLS)),
        ("mode=2（只保留内部工具 + 无依赖计算）",
         "白名单 = _INTERNAL_TOOLS | _CALC_TOOLS | describe_tool，其余全下架。",
         _mode_tools(tools, lambda n: n in keep_mode2)),
        ("mode=3（下架软 IP + LASPCEM）",
         "全量 − design_chip − LASPCEM 电磁仿真工具。",
         _mode_tools(tools, lambda n: n not in _MODE3_TOOLS)),
        ("exe 打包版（frozen 无条件下架）",
         "全量 − analyze_image（不暴露第三方图片上传），与 mode 无关。",
         _mode_tools(tools, lambda n: n not in _EXE_HIDDEN_TOOLS)),
    ]

    lines = ["# EDI gRPC MCP 工具清单（按 MCP_HIDE_INTERNAL_TOOLS 模式分组）", "",
             "> 自动生成，勿手改。权威数量见 /ready 的 tool_count。", ""]
    for title, note, group in sections:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"> {note}")
        lines.append("")
        lines.append(f"共 {len(group)} 个工具：")
        lines.append("")
        for name, desc in group:
            d = (desc or "").split("\n")[0].strip()
            lines.append(f"- `{name}` — {d}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    tools = sorted((t.name, t.description or "") for t in registered_tools(mcp))
    full_out = Path(__file__).parent.parent / "docs" / "TOOL_INDEX.md"
    full_out.write_text(render_tool_index(tools), encoding="utf-8")
    print(f"wrote {full_out} ({len(tools)} tools)")

    filtered_out = Path(__file__).parent.parent / "docs" / "TOOL_INDEX_FILTERED.md"
    filtered_out.write_text(render_mode_index(tools), encoding="utf-8")
    print(f"wrote {filtered_out} ({len(tools)} tools)")


if __name__ == "__main__":
    main()