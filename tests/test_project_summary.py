"""工程类接口的契约回归：路径风格、目录名字段、latest_result 语义、仿真参数结构。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from servers.eda.project_manage import analyze_variables, get_project_summary, list_epp_projects

META = (
    '(edi-project-metadata 11111111-2222-3333-4444-555555555555\n'
    ' (name "{name}")\n (author "tester")\n (version "1")\n'
    ' (created 2026-01-01T00:00:00Z)\n)\n'
)


def _psi(params: dict) -> str:
    """(paramsinfo "<两次转义 JSON>")"""
    return "(paramsinfo " + json.dumps(json.dumps(params)) + ")"


def _build(
    root: Path,
    dir_name: str = "demo_dir",
    meta_name: str = "demo_meta",
    with_metadata: bool = True,
    extra_schematic: str = "",
) -> Path:
    proj = root / dir_name
    for sub in ("project", "schematics/main"):
        (proj / sub).mkdir(parents=True, exist_ok=True)
    (proj / "p.epp").write_text("\n", encoding="utf-8")
    if with_metadata:
        (proj / "project" / "metadata.ep").write_text(
            META.format(name=meta_name), encoding="utf-8"
        )
    (proj / "schematics" / "schematics.ep").write_text(
        '(edi-schematics\n (schematic "schematics/main/schematic.ep")\n)\n', encoding="utf-8"
    )
    (proj / "schematics" / "main" / "schematic.ep").write_text(
        '(edi-schematic 2970bbcf-8969-4fb4-99b0-38c7db9aa257\n (name "main")\n'
        + extra_schematic
        + ")\n",
        encoding="utf-8",
    )
    return proj / "p.epp"


def _add_raw(proj_dir: Path, rel: str, size: int = 16, age_s: int = 0) -> Path:
    p = proj_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    if age_s:
        old = time.time() - age_s
        os.utime(p, (old, old))
    return p


def test_summary_reports_dir_name_and_posix_paths(tmp_path: Path) -> None:
    """metadata.name 与目录名不同时要区分开，所有路径统一正斜杠。"""
    epp = _build(tmp_path, dir_name="demo_dir", meta_name="demo_meta")
    r = get_project_summary(str(epp))

    assert r["project"]["name"] == "demo_meta"
    assert r["project"]["project_dir_name"] == "demo_dir"
    assert "\\" not in r["schematics"]["paths"][0]
    assert Path(r["schematics"]["paths"][0]).is_file()
    assert set(r["components"]) == {"total", "by_type"}, r["components"]


def test_missing_metadata_keeps_keys_and_warns(tmp_path: Path) -> None:
    """元数据缺失：字段仍存在（空串）+ 明确 warning，下游不会 KeyError。"""
    epp = _build(tmp_path, with_metadata=False)
    r = get_project_summary(str(epp))

    assert set(r["project"]) == {"project_id", "name", "author", "version",
                                 "created", "project_dir_name"}
    assert r["project"]["name"] == ""
    assert [w["code"] for w in r["warnings"]] == ["METADATA_MISSING"], r["warnings"]
    assert all(isinstance(w, dict) and "message" in w for w in r["warnings"])


def test_latest_result_semantics(tmp_path: Path) -> None:
    """未查询 → null；没有 raw → exists=false 的空结构；0 字节 → exists=false + warning。"""
    epp = _build(tmp_path)
    proj = epp.parent

    # 未查询
    assert get_project_summary(str(epp), include_latest_result=False)["latest_result"] is None

    # 没有 raw
    empty = get_project_summary(str(epp))["latest_result"]
    assert empty == {"path": "", "exists": False, "size": 0}, empty

    # 0 字节 raw
    _add_raw(proj, "history/result.raw", size=0)
    r = get_project_summary(str(epp))
    assert r["latest_result"]["exists"] is False
    assert r["latest_result"]["size"] == 0
    assert [w["code"] for w in r["warnings"]] == ["RESULT_EMPTY"], r["warnings"]
    assert r["warnings"][0]["path"].endswith("/history/result.raw")

    # 正常 raw
    _add_raw(proj, "history/result.raw", size=32)
    ok = get_project_summary(str(epp))["latest_result"]
    assert ok["exists"] is True and ok["size"] == 32 and ok["path"].endswith("/history/result.raw")


def test_latest_result_ignores_model_dir(tmp_path: Path) -> None:
    """model/ 下的 .result.raw（模型自带）即便更新也不能顶替 history 里的仿真结果。"""
    epp = _build(tmp_path)
    proj = epp.parent
    hist = _add_raw(proj, "history/result.raw", size=64, age_s=600)
    model = _add_raw(proj, "model/ice/Ice1/.result.raw", size=128)

    r = get_project_summary(str(epp))["latest_result"]
    assert Path(r["path"]) == hist.resolve(), r
    assert model.exists()


def test_simulation_parameters_are_structured(tmp_path: Path) -> None:
    """仿真器件参数返回 {value, unit} 结构，不再拼成 '8 GHz' 字符串。"""
    comp = (
        ' (component aaaaaaaa-0000-0000-0000-000000000001 (type "SParameter")'
        ' (name "SP1") (active_state 0) '
        + _psi({
            "BasicParameters": {"ItemName": "SP1", "ItemType": "SParameter"},
            "Start": {"Value": "8", "CurrentUnit": "GHz"},
            "Pts": {"Value": "41", "CurrentUnit": "None"},
        })
        + ")\n"
    )
    epp = _build(tmp_path, extra_schematic=comp)
    r = get_project_summary(str(epp))

    assert r["components"]["total"] == 1
    sim = r["simulation"][0]
    assert set(sim) == {"component_type", "instance_name", "parameters"}
    assert sim["parameters"]["Start"] == {"value": "8", "unit": "GHz"}
    assert sim["parameters"]["Pts"]["value"] == "41"


def test_missing_schematic_file_warns_instead_of_silent_skip(tmp_path: Path) -> None:
    """schematics.ep 登记了但文件缺失/为空：不能静默返回 count=1 且 total=0。"""
    epp = _build(tmp_path)
    sch = epp.parent / "schematics" / "main" / "schematic.ep"

    sch.unlink()
    r = get_project_summary(str(epp))
    assert r["schematics"]["count"] == 1
    assert r["components"]["total"] == 0
    assert [w["code"] for w in r["warnings"]] == ["SCHEMATIC_MISSING"], r["warnings"]
    assert r["warnings"][0]["path"] == r["schematics"]["paths"][0]

    sch.write_text("", encoding="utf-8")
    r2 = get_project_summary(str(epp))
    assert [w["code"] for w in r2["warnings"]] == ["SCHEMATIC_MISSING"], r2["warnings"]


def test_by_type_null_when_types_not_requested(tmp_path: Path) -> None:
    """include_component_types=False → by_type 为 null（区别于"没有器件"的空对象）。"""
    comp = (
        ' (component aaaaaaaa-0000-0000-0000-000000000009 (type "TermG")'
        ' (name "TermG1") (active_state 0) '
        + _psi({"BasicParameters": {"ItemName": "TermG1", "ItemType": "TermG"}})
        + ")\n"
    )
    epp = _build(tmp_path, extra_schematic=comp)

    on = get_project_summary(str(epp))
    assert on["components"]["total"] == 1
    assert on["components"]["by_type"] == {"TermG": 1}
    assert on["warnings"] == []

    off = get_project_summary(str(epp), include_component_types=False)
    assert off["components"]["total"] == 1
    assert off["components"]["by_type"] is None


def test_analyze_variables_project_path_is_posix(tmp_path: Path) -> None:
    epp = _build(tmp_path)
    r = analyze_variables(str(epp))
    assert "\\" not in r["project_path"]
    assert r["project_path"].endswith("/p.epp")


def test_list_epp_projects_returns_posix_paths(tmp_path: Path) -> None:
    _build(tmp_path)
    r = list_epp_projects(str(tmp_path))
    assert r["success"] is True
    assert "\\" not in r["folder"]
    assert r["count"] == 1
    assert all("\\" not in p["path"] for p in r["projects"])
