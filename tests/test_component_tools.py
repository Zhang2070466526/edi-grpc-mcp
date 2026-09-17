"""测试 list_simulation_components 的过滤与分页。

依赖本机参考工程（conftest.real_project），本机无该工程时自动 skip。
"""
from servers.eda.simulation_components import list_simulation_components


def test_list_components(real_project):
    r = list_simulation_components(real_project)
    assert r["success"], r.get("message", "")
    assert r["total"] >= 1
    for c in r["components"]:
        assert "component_id" in c
        assert "instance_name" in c
        assert "component_type" in c


def test_filter_by_type(real_project):
    r = list_simulation_components(real_project, component_type="TermG")
    assert r["success"]
    for c in r["components"]:
        assert c["component_type"] == "TermG"


def test_limit(real_project):
    r = list_simulation_components(real_project, limit=1)
    assert len(r["components"]) <= 1


def test_include_hidden_param_accepted(real_project):
    """include_hidden 参数应被接受（True/False 均可调用）。"""
    r = list_simulation_components(real_project, include_hidden=True)
    assert r["success"], r.get("message", "")
