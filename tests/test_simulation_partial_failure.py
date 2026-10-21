"""run_mixer_simulation「部分成功」降级回归测试。

背景（2026-10-21 实测）：exe 在某个指标（带内杂散）崩溃时进程退出码为 1，但另外 4 个指标
的结果与 194 KB 的报告 PDF/DOCX 都已生成。旧实现只要 returncode != 0 就整单判失败，
把这些成果全吞掉；现改为「有 results 就按部分成功返回」，只有一条结果都没有才算真失败。
"""

from __future__ import annotations

import pytest

from servers.simulation_tool import mixer_simulation as mod

_EPP = "C:/fake/proj/proj.epp"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把外部依赖全部打桩：edi/ads 目录、端口探测、exe 调用。"""
    edi = tmp_path / "EDI"
    edi.mkdir()
    (edi / "turbocharts_app.exe").write_bytes(b"stub")
    ads = tmp_path / "ADS"
    ads.mkdir()

    sim_dir = tmp_path / "simulate_x"
    sim_dir.mkdir()
    (sim_dir / "proj_仿真报告.pdf").write_bytes(b"%PDF stub")
    (sim_dir / "proj_仿真报告.docx").write_bytes(b"PK stub")

    monkeypatch.setattr(mod, "require_file", lambda p, exts: (p, None))
    monkeypatch.setattr(mod, "tcp_port_open", lambda *a, **k: True)
    monkeypatch.setattr(mod, "detect_ads_path", lambda: str(ads))

    def _install(payload: dict, returncode: int):
        monkeypatch.setattr(mod, "run_exe", lambda *a, **k: {
            "json": payload, "stdout": "", "stderr": "",
            "returncode": returncode, "json_error": None,
        })

    return {"edi": str(edi), "ads": str(ads), "sim_dir": str(sim_dir), "install": _install}


def _call(env):
    return mod.run_mixer_simulation(
        epp_path=_EPP,
        links=[{"name": "L", "direction": "down", "f_rf": [33, 37], "f_if": 3.9,
                "bw": 0.4, "pin": -30, "p_lo": 4,
                "channels": [{"name": "CH1", "rf_port": 1, "if_port": 2, "mixer": "MIX1"}],
                "stages": [{"mixer": "MIX1", "lo_port": 5, "relation": "diff",
                            "injection": "low", "f_if": 3.9}]}],
        indicators="变频增益,带内杂散",
        edi_path=env["edi"],
        ads_path=env["ads"],
        timeout_seconds=10,
    )


def _payload(env):
    return {
        "simulation_path": env["sim_dir"],
        "results": [{"link": "L/CH1", "electrical_parameters": [
            {"indicator": "变频增益", "minimum": "25.19", "typical": "26.45",
             "maximum": "27.10", "unit": "dB", "note": ""}]}],
        "errors": [{"code": "UNHANDLED_ERROR", "stage": "runtime", "link": "",
                    "indicator": "", "message": "步骤失败：工况执行 [WinError 5]"}],
    }


def test_nonzero_exit_with_results_degrades_to_partial_success(env):
    env["install"](_payload(env), returncode=1)

    resp = _call(env)

    assert resp["success"] is True
    assert len(resp["results"]) == 1
    assert resp["errors"], "失败的指标必须仍然暴露在 errors 里"
    # 成果没被吞：报告仍然交付
    assert [a["type"] for a in resp["report_artifacts"]] == ["pdf", "docx"]
    assert "部分完成" in resp["message"]
    assert any("部分成功" in w for w in resp["warnings"])


def test_zero_exit_keeps_normal_message(env):
    env["install"](_payload(env), returncode=0)

    resp = _call(env)

    assert resp["success"] is True
    assert "混频仿真完成" in resp["message"]
    assert not any("部分成功" in w for w in resp.get("warnings") or [])


def test_nonzero_exit_without_results_is_still_failure(env):
    payload = _payload(env)
    payload["results"] = []
    env["install"](payload, returncode=1)

    resp = _call(env)

    assert resp["success"] is False
    assert resp["error_code"] == "SIMULATION_EXECUTION_FAILED"
