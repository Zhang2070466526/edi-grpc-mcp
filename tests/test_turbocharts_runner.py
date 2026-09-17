"""测试 turbocharts runner 超时范围校验。"""
import pytest

from servers.turbocharts.config import run_turbocharts


def test_timeout_range_too_low():
    with pytest.raises(ValueError):
        run_turbocharts(["dummy"], timeout_seconds=0)


def test_timeout_range_too_high():
    with pytest.raises(ValueError):
        run_turbocharts(["dummy"], timeout_seconds=601)
