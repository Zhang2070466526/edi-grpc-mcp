"""S3: 工具清单必须与注册表一致（漂移则 FAIL，提示重跑 scripts/gen_tool_index.py）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from servers import mcp  # noqa: E402
from servers.utils import registered_tools  # noqa: E402
import servers.registry_server  # noqa: E402, F401 — 触发注册
import gen_tool_index  # noqa: E402 — 复用渲染器，避免两处漂移


def test_tool_index_in_sync():
    tools = sorted((t.name, t.description or "") for t in registered_tools(mcp))
    path = Path(__file__).parent.parent / "docs" / "TOOL_INDEX.md"
    assert path.exists(), f"{path} 不存在，运行 scripts/gen_tool_index.py"
    actual = path.read_text(encoding="utf-8")
    expected = gen_tool_index.render_tool_index(tools)
    assert actual == expected, "工具清单漂移，运行 scripts/gen_tool_index.py"
