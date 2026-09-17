"""pytest 共享基建 — sys.path 注入 + gRPC mock 夹具 + 环境探测（S4）。

替掉各测试文件里重复的 sys.path.insert 与 _fake_caller 定义。
环境探测（REFERENCE_PROJECT / HAS_PROJECT）集中在此：需要真实 .epp 的测试统一用
real_project fixture，本机无该工程则整条 skip——不再各文件散落硬编码路径。
"""
import os
import sys
from pathlib import Path

import pytest

# 让 `import servers` / `import proto` 可用（仓库根加入 sys.path）
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 环境探测（S4：测试可信度分类）──────────────────────────
# 需要真实 .epp 工程的测试依赖本机参考工程；可用环境变量 EDI_TEST_PROJECT 覆盖。
REFERENCE_PROJECT = Path(
    os.environ.get("EDI_TEST_PROJECT", r"C:\Users\JGL\EDI-Workspace\EDI_TEST\EDI_TEST.epp")
)
HAS_PROJECT = REFERENCE_PROJECT.is_file()


@pytest.fixture
def real_project() -> str:
    """返回本机参考 .epp 工程路径；本机无该工程则 skip（供需要真实工程文件的测试用）。"""
    if not HAS_PROJECT:
        pytest.skip(f"本机无参考工程（可用 EDI_TEST_PROJECT 覆盖）: {REFERENCE_PROJECT}")
    return str(REFERENCE_PROJECT)


@pytest.fixture
def fake_caller():
    """返回 (ecserver_pb2, calls, _fake)：_fake mock call_grpc 记录调用并返回成功。"""
    from proto import ecserver_pb2
    calls = []

    def _fake(task_type, payload, timeout, max_timeout_seconds=300):
        calls.append((task_type, payload))
        return {"success": True, "status": "SUCCEEDED"}

    return ecserver_pb2, calls, _fake
