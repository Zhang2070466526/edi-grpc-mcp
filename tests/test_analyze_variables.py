"""analyze_variables：表达式型引用（P[1]/Bits）与变量状态字段的回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

from servers.eda.project_manage import analyze_variables


def _psi(params: dict) -> str:
    """把参数字典写成 schematic.ep 里的 (paramsinfo "<两次转义的 JSON>") 形式。"""
    return "(paramsinfo " + json.dumps(json.dumps(params)) + ")"


def _build_project(root: Path) -> Path:
    proj = root / "demo"
    for sub in ("project", "schematics/main"):
        (proj / sub).mkdir(parents=True, exist_ok=True)
    (proj / "demo.epp").write_text("\n", encoding="utf-8")
    (proj / "project" / "metadata.ep").write_text(
        '(edi-project-metadata 11111111-2222-3333-4444-555555555555\n'
        ' (name "demo")\n (author "tester")\n (version "1")\n'
        ' (created 2026-01-01T00:00:00Z)\n)\n',
        encoding="utf-8",
    )
    (proj / "schematics" / "schematics.ep").write_text(
        '(edi-schematics\n (schematic "schematics/main/schematic.ep")\n)\n', encoding="utf-8"
    )

    var_params = {"BasicParameters": {"ItemName": "Var1", "ItemType": "Var"}}
    for name, initial in (("Freq", "10"), ("Pwr_IN", "-30"), ("X", "0"), ("X1", "0")):
        var_params[name] = {
            "Initial": initial, "Max": "", "Min": "", "Status": "Disable", "Tunable": "false",
        }

    port_params = {
        "BasicParameters": {"ItemName": "PORT1", "ItemType": "P_nToneG"},
        "Freq[1]": {"Value": "Freq", "CurrentUnit": "GHz"},
        "P[1]": {"Value": "polar(dbmtow(Pwr_IN),0)", "CurrentUnit": ""},
    }
    bits_expr = {"BasicParameters": {"ItemName": "HGC259_1", "ItemType": "HGC259"},
                 "Bits": {"Value": "itob(int(X1))"}}
    bits_literal = {"BasicParameters": {"ItemName": "HGC218_1", "ItemType": "HGC218"},
                    "Bits": {"Value": '"000000"'}}
    delay = {"BasicParameters": {"ItemName": "DLY1", "ItemType": "DelayLine"},
             "Delay": {"Value": "X1*2"}}
    sweep = {"BasicParameters": {"ItemName": "Sweep1", "ItemType": "Sweep"},
             "SweepVar": {"Value": "Freq"}, "Start": {"Value": "8"},
             "Stop": {"Value": "12"}, "Step": {"Value": "0.1"}}

    comps = [
        ("aaaaaaaa-0000-0000-0000-000000000001", "Var", "Var1", 0, var_params),
        ("aaaaaaaa-0000-0000-0000-000000000002", "P_nToneG", "PORT1", 0, port_params),
        ("aaaaaaaa-0000-0000-0000-000000000003", "HGC259", "HGC259_1", 0, bits_expr),
        ("aaaaaaaa-0000-0000-0000-000000000004", "HGC218", "HGC218_1", 0, bits_literal),
        ("aaaaaaaa-0000-0000-0000-000000000005", "DelayLine", "DLY1", 1, delay),
        ("aaaaaaaa-0000-0000-0000-000000000006", "Sweep", "Sweep1", 0, sweep),
    ]
    lines = ['(edi-schematic 2970bbcf-8969-4fb4-99b0-38c7db9aa257', ' (name "main")']
    for cid, ctype, cname, active_state, params in comps:
        lines.append(
            f' (component {cid} (type "{ctype}") (name "{cname}") '
            f'(active_state {active_state}) {_psi(params)})'
        )
    lines.append(")")
    (proj / "schematics" / "main" / "schematic.ep").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return proj / "demo.epp"


def test_references_use_expression_matching(tmp_path: Path) -> None:
    """P[1] / Bits 里的变量要算引用，引号字面量与变量名前缀不误判。"""
    epp = _build_project(tmp_path)
    result = analyze_variables(str(epp))
    assert result["success"] is True

    refs = {(r["variable"], r["component"], r["parameter"]) for r in result["references"]}
    assert ("Pwr_IN", "PORT1", "P[1]") in refs
    assert ("X1", "HGC259_1", "Bits") in refs
    assert ("Freq", "PORT1", "Freq[1]") in refs
    assert ("Freq", "Sweep1", "SweepVar") in refs
    # 字面量 "000000" 不算引用
    assert ("X", "HGC218_1", "Bits") not in refs
    # X1*2 里不能把 X 单独当引用（标识符边界）
    assert not any(r[0] == "X" and r[1] == "DLY1" for r in refs)


def test_variables_report_device_state_not_param_status(tmp_path: Path) -> None:
    """变量状态取自器件 active_state/state，不再回传恒为 Disable 的 Status。"""
    epp = _build_project(tmp_path)
    result = analyze_variables(str(epp))

    assert len(result["variables"]) == 4
    for var in result["variables"]:
        assert "status" not in var, var
        assert var["active_state"] == 0, var
        assert var["state"] == "NORMAL", var
