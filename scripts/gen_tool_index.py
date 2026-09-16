"""生成 docs/TOOL_INDEX.md —— 从注册表自动渲染权威工具清单。

用法（仓库根）: python scripts/gen_tool_index.py
清单漂移时 tests/test_tool_index.py 会 FAIL，提示重跑本脚本。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from servers import mcp  # noqa: E402
from servers.utils import registered_tools  # noqa: E402
import servers.registry_server  # noqa: E402, F401 — 触发工具注册


def render_tool_index(tools: list[tuple[str, str]]) -> str:
    """把 (name, description) 列表渲染成 markdown 清单。"""
    lines = ["# EDI gRPC MCP 工具清单", ""]
    lines.append("> 自动生成，勿手改。权威数量见 /ready 的 tool_count。")
    lines.append("")
    lines.append(f"共 {len(tools)} 个工具：")
    lines.append("")
    for name, desc in tools:
        d = (desc or "").split("\n")[0].strip()
        lines.append(f"- `{name}` — {d}")
    return "\n".join(lines) + "\n"


def main() -> None:
    tools = sorted((t.name, t.description or "") for t in registered_tools(mcp))
    out = Path(__file__).parent.parent / "docs" / "TOOL_INDEX.md"
    out.write_text(render_tool_index(tools), encoding="utf-8")
    print(f"wrote {out} ({len(tools)} tools)")


if __name__ == "__main__":
    main()
