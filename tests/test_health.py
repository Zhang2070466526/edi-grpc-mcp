"""测试 health 端点逻辑。"""
import asyncio
from pathlib import Path



def test_tcp_check_unavailable():
    """模拟检查不可达端口应返回 False。"""
    from servers.chat.routes import _check_tcp

    result = asyncio.run(_check_tcp("127.0.0.1:19999"))
    assert result is False, f"expected False for unavailable port, got {result}"


def test_tcp_check_format():
    """测试端口解析异常处理。"""
    from servers.chat.routes import _check_tcp

    result = asyncio.run(_check_tcp("invalid"))
    assert result is False


if __name__ == "__main__":
    test_tcp_check_unavailable()
    test_tcp_check_format()
    print("test_health.py: all passed")
