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
from servers.registry_server import _INTERNAL_TOOLS  # noqa: E402


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


def main() -> None:
    tools = sorted((t.name, t.description or "") for t in registered_tools(mcp))
    full_out = Path(__file__).parent.parent / "docs" / "TOOL_INDEX.md"
    full_out.write_text(render_tool_index(tools), encoding="utf-8")
    print(f"wrote {full_out} ({len(tools)} tools)")

    visible = [(n, d) for n, d in tools if n not in _INTERNAL_TOOLS]
    filtered_out = Path(__file__).parent.parent / "docs" / "TOOL_INDEX_FILTERED.md"
    filtered_out.write_text(
        render_tool_index(visible,
                          title="EDI gRPC MCP 对外工具清单（过滤内部工具后）",
                          note="MCP_HIDE_INTERNAL_TOOLS=1 时对外可见；权威数量见 /ready 的 tool_count"),
        encoding="utf-8")
    print(f"wrote {filtered_out} ({len(visible)} visible tools)")


if __name__ == "__main__":
    main()