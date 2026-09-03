"""测试 S-expression 解析器和 ProjectReader。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from servers.eda.project_reader import parse_sexp, _kv, _walk_find, parse_paramsinfo, parse_components, ProjectReader


def test_parse_sexp_simple():
    items = list(parse_sexp('(name "test")'))
    assert len(items) == 1
    assert items[0] == ["name", "test"]
    assert _kv(items[0], "name") == "test"


def test_parse_sexp_nested():
    text = '(edi-project-metadata uuid (name "EDI_TEST") (author "bm") (version "1.0.1"))'
    items = list(parse_sexp(text))
    meta = items[0]
    assert _kv(meta, "name") == "EDI_TEST"
    assert _kv(meta, "author") == "bm"
    assert _kv(meta, "version") == "1.0.1"


def test_parse_sexp_escaped_quotes():
    items = list(parse_sexp(r'(paramsinfo "{\"key\": {\"Value\": \"42\", \"CurrentUnit\": \"GHz\"}}")'))
    param = parse_paramsinfo(items[0][1])
    assert param["key"]["value"] == "42"
    assert param["key"]["unit"] == "GHz"


def test_walk_find():
    items = list(parse_sexp('(root (component A) (pin 1) (component B) (pin 2))'))
    comps = _walk_find(items, "component")
    assert len(comps) == 2
    pins = _walk_find(items, "pin")
    assert len(pins) == 2


def test_parse_components_from_real_file():
    project = r"C:\Users\JGL\EDI-Workspace\EDI_TEST\EDI_TEST.epp"
    if not Path(project).is_file():
        print(f"SKIP: {project} not found")
        return
    r = ProjectReader(project)
    raw = r.read_schematic("main")
    assert raw is not None
    comps = parse_components(raw)
    assert len(comps) >= 1
    terms = [c for c in comps if c["type"] == "TermG"]
    assert len(terms) >= 1
    assert terms[0]["pin_count"] >= 1


if __name__ == "__main__":
    test_parse_sexp_simple()
    test_parse_sexp_nested()
    test_parse_sexp_escaped_quotes()
    test_walk_find()
    test_parse_components_from_real_file()
    print("test_project_reader.py: all passed")
