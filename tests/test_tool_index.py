"""S3: 工具清单必须与注册表一致（漂移则 FAIL，提示重跑 scripts/gen_tool_index.py）。"""
from pathlib import Path

from servers import mcp
from servers.utils import registered_tools
import servers.registry_server  # noqa: F401 — 触发注册


def _render(tools: list[tuple[str, str]]) -> str:
    """与 scripts/gen_tool_index.py 的 render_tool_index 保持一致。"""
    lines = ["# EDI gRPC MCP 工具清单", ""]
    lines.append("> 自动生成，勿手改。权威数量见 /ready 的 tool_count。")
    lines.append("")
    lines.append(f"共 {len(tools)} 个工具：")
    lines.append("")
    for name, desc in tools:
        d = (desc or "").split("\n")[0].strip()
        lines.append(f"- `{name}` — {d}")
    return "\n".join(lines) + "\n"


def test_tool_index_in_sync():
    tools = sorted((t.name, t.description or "") for t in registered_tools(mcp))
    path = Path(__file__).parent.parent / "docs" / "TOOL_INDEX.md"
    assert path.exists(), f"{path} 不存在，运行 scripts/gen_tool_index.py"
    actual = path.read_text(encoding="utf-8")
    expected = _render(tools)
    assert actual == expected, "工具清单漂移，运行 scripts/gen_tool_index.py"
