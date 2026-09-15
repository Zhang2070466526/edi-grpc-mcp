"""pytest 共享基建 — sys.path 注入 + gRPC 调用 mock 夹具。

替掉各测试文件里重复的 sys.path.insert 与 _fake_caller 定义。
"""
import sys
from pathlib import Path

import pytest

# 让 `import servers` / `import proto` 可用（仓库根加入 sys.path）
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def fake_caller():
    """返回 (ecserver_pb2, calls, _fake)：_fake mock call_grpc 记录调用并返回成功。"""
    from proto import ecserver_pb2
    calls = []

    def _fake(task_type, payload, timeout, max_timeout_seconds=300):
        calls.append((task_type, payload))
        return {"success": True, "status": "SUCCEEDED"}

    return ecserver_pb2, calls, _fake
