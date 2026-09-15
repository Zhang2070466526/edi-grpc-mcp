"""测试 turbocharts runner 超时校验。"""
from pathlib import Path



def test_timeout_range_valid():
    from servers.turbocharts.config import run_turbocharts
    import subprocess
    # 用 echo 代替真实 turbocharts，只测参数校验
    try:
        result = run_turbocharts(["echo", "test"], timeout_seconds=30)
        assert result.returncode == 0
    except FileNotFoundError:
        pass  # echo 在某些环境可能不存在


def test_timeout_range_too_low():
    from servers.turbocharts.config import run_turbocharts
    try:
        run_turbocharts(["echo"], timeout_seconds=0)
        assert False, "should have raised"
    except ValueError:
        pass


def test_timeout_range_too_high():
    from servers.turbocharts.config import run_turbocharts
    try:
        run_turbocharts(["echo"], timeout_seconds=601)
        assert False, "should have raised"
    except ValueError:
        pass


if __name__ == "__main__":
    test_timeout_range_valid()
    test_timeout_range_too_low()
    test_timeout_range_too_high()
    print("test_turbocharts_runner.py: all passed")
