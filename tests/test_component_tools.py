"""测试 list_simulation_components 的过滤与分页。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from servers.eda.simulation_components import list_simulation_components

PROJECT = r"C:\Users\JGL\EDI-Workspace\EDI_TEST\EDI_TEST.epp"


def test_list_components():
    if not Path(PROJECT).is_file():
        pytest.skip(f"依赖本机工程文件: {PROJECT}")
    r = list_simulation_components(PROJECT)
    assert r["success"], r.get("message", "")
    assert r["total"] >= 1
    for c in r["components"]:
        assert "component_id" in c
        assert "instance_name" in c
        assert "component_type" in c


def test_filter_by_type():
    if not Path(PROJECT).is_file():
        pytest.skip(f"依赖本机工程文件: {PROJECT}")
    r = list_simulation_components(PROJECT, component_type="TermG")
    assert r["success"]
    for c in r["components"]:
        assert c["component_type"] == "TermG"


def test_limit():
    if not Path(PROJECT).is_file():
        pytest.skip(f"依赖本机工程文件: {PROJECT}")
    r = list_simulation_components(PROJECT, limit=1)
    assert len(r["components"]) <= 1


def test_include_hidden_param_accepted():
    """include_hidden 参数应被接受（True/False 均可调用）。"""
    if not Path(PROJECT).is_file():
        pytest.skip(f"依赖本机工程文件: {PROJECT}")
    r = list_simulation_components(PROJECT, include_hidden=True)
    assert r["success"], r.get("message", "")


if __name__ == "__main__":
    test_list_components()
    test_filter_by_type()
    test_limit()
    test_include_hidden_param_accepted()
    print("test_component_tools.py: all passed")
