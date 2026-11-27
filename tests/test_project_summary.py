"""工程类接口的契约回归：路径风格、目录名字段、latest_result 语义、仿真参数结构。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from servers.eda.project_manage import analyze_variables, get_project_summary, list_epp_projects

META = (
    '(edi-project-metadata 11111111-2222-3333-4444-555555555555\n'
    ' (name "{name}")\n (author "tester")\n (version "1")\n'
    ' (created 2026-01-01T00:00:00Z)\n)\n'
)


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
