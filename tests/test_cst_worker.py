"""测试 CST 执行器（cst_worker.py）—— 3.6 语法守卫、纯函数解析、作业协议。

本文件不需要 CST 环境：只测纯函数与作业协议，不真起 CST 会话。
"""
import ast
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

WORKER_PATH = Path(__file__).resolve().parent.parent / "servers" / "cst" / "cst_worker.py"


class TestPython36Guard:
    """执行器跑在 CST 自带解释器（实测 Python 3.6.0）上，必须保持 3.6 语法。

    两层守卫：① `ast.parse(feature_version=(3, 6))` 拦语法级特性（海象、match、位置参数 `/`）；
    ② 代码 token 黑名单拦**语法合法但 3.6 不认**的东西 —— `feature_version` 不校验
    future 特性，所以 `from __future__ import annotations` 能骗过它，却会让 3.6
    在真机上 `SyntaxError: future feature annotations is not defined`（2026-09-24 实测踩到）。
    """

    @staticmethod
    def _code_only() -> str:
        """去掉注释与字符串字面量后的代码 token 串（文档里提到这些词不算违规）。"""
        import io
        import tokenize
        src = WORKER_PATH.read_text(encoding="utf-8")
        return " ".join(
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.STRING)
        )

    def test_parses_as_python36(self):
        src = WORKER_PATH.read_text(encoding="utf-8")
        ast.parse(src, filename=str(WORKER_PATH), feature_version=(3, 6))

    def test_no_py37_plus_syntax_in_code_only(self):
        code = self._code_only()
        assert "from __future__ import annotations" not in code, "3.7+ 才有该 future 特性"
        assert "capture_output" not in code, "subprocess.run(capture_output=) 是 3.7 语法"
        assert "dataclass" not in code, "dataclasses 是 3.7 语法"
        for pat in ("-> list [", "-> dict [", "-> tuple [", ": list [", ": dict [", ": tuple ["):
            assert pat not in code, "PEP 585 泛型注解（%s）3.9 才支持" % pat


class TestToWritableCopy:
    def test_writable_returns_self(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        f = tmp_path / "m.cst"
        f.write_text("x")
        monkeypatch.setattr(cst_worker.os, "access", lambda *a: True)
        assert cst_worker.to_writable_copy(str(f)) == str(f)

    def test_readonly_copies_to_temp(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        f = tmp_path / "model.cst"
        f.write_text("x")
        monkeypatch.setattr(cst_worker.os, "access", lambda *a: False)
        monkeypatch.setattr(cst_worker.tempfile, "gettempdir", lambda: str(tmp_path))
        copied = cst_worker.to_writable_copy(str(f))
        assert copied != str(f)
        assert str(tmp_path) in copied
        assert Path(copied).is_file()


# ── 结果树解析与格式（纯函数） ──

class TestSParamIndices:
    def test_plain(self):
        from servers.cst.cst_worker import s_param_indices
        assert s_param_indices("S2,1") == (2, 1)

    def test_with_path(self):
        from servers.cst.cst_worker import s_param_indices
        assert s_param_indices("1D Results\\S-Parameters\\S1,1") == (1, 1)

    def test_with_spaces(self):
        from servers.cst.cst_worker import s_param_indices
        assert s_param_indices("S1, 1") == (1, 1)

    def test_invalid_returns_zero(self):
        from servers.cst.cst_worker import s_param_indices
        assert s_param_indices("Sxx") == (0, 0)

    def test_orders_by_port(self):
        from servers.cst.cst_worker import s_param_indices
        items = ["S2,1", "S1,2", "S1,1"]
        assert sorted(items, key=s_param_indices) == ["S1,1", "S1,2", "S2,1"]


class TestFreqUnit:
    def test_ghz(self):
        from servers.cst.cst_worker import freq_unit
        assert freq_unit("Frequency (GHz)") == "GHz"

    def test_default_hz(self):
        from servers.cst.cst_worker import freq_unit
        assert freq_unit("") == "Hz"


class TestHasFarfield:
    def test_true_when_farfield_in_tree(self):
        from servers.cst.cst_worker import has_farfield
        assert has_farfield(["1D Results\\Farfields\\farfield (f=1)"]) is True

    def test_false_when_only_s_parameter(self):
        from servers.cst.cst_worker import has_farfield
        assert has_farfield(["1D Results\\S-Parameters\\S1,1"]) is False


class TestWriteTouchstone:
    def test_two_port_ma_format(self, tmp_path):
        from servers.cst.cst_worker import write_touchstone
        out = tmp_path / "m.s2p"
        freq = [1.0, 2.0]
        sp_data = {(i, j): [complex(0.5, 0.0), complex(0.0, 0.5)]
                   for i in (1, 2) for j in (1, 2)}
        write_touchstone(str(out), freq, sp_data, 2, "GHz", 50.0)
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert lines[0].startswith("! Touchstone file exported by cst")
        assert lines[1] == "# GHz S MA R 50.0"
        # 表头 2 行 + 每个频点 1 行
        assert len(lines) == 2 + len(freq)
        # 1 行 = 频率 + 4 个复数（每复数 2 列）
        assert len(lines[2].split()) == 1 + 4 * 2


# ── 作业协议 ──

class TestExportSnpValidation:
    """端口数非法时必须在读结果之前就拒绝（沿用 result_export 时代的验证意图）。"""

    def test_rejects_invalid_port_count(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        result_3d = MagicMock()
        result_3d.get_tree_items.return_value = ["1D Results\\S-Parameters\\S1,1"]
        monkeypatch.setattr(cst_worker, "open_3d_results", lambda p: (MagicMock(), result_3d))
        with pytest.raises(RuntimeError) as exc:
            cst_worker.do_export_snp(
                {"model_path": "C:/m.cst", "output_dir": "", "port_count": 0})
        assert "port_count" in str(exc.value)


class TestFarfieldFallback:
    """归档（未解包）的 .cst 读不了结果树 —— 必须退化成「先求解」，不能直接失败。"""

    def test_detect_reports_note_when_tree_unreadable(self, monkeypatch):
        from servers.cst import cst_worker

        def boom(_path):
            raise UserWarning("Archived CST files are not supported")

        monkeypatch.setattr(cst_worker, "open_3d_results", boom)
        needs_solve, note = cst_worker.detect_farfield("C:/x.cst")
        assert needs_solve is True
        assert "Archived" in note

    def test_detect_needs_no_solve_when_farfield_present(self, monkeypatch):
        from servers.cst import cst_worker
        result_3d = MagicMock()
        result_3d.get_tree_items.return_value = ["1D Results\\Farfields\\farfield (f=1)"]
        monkeypatch.setattr(cst_worker, "open_3d_results", lambda p: (MagicMock(), result_3d))
        needs_solve, note = cst_worker.detect_farfield("C:/x.cst")
        assert (needs_solve, note) == (False, "")

    def test_export_solves_when_detection_fails(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        monkeypatch.setattr(cst_worker, "detect_farfield",
                            lambda p: (True, "结果树不可读（归档未解包）"))
        monkeypatch.setattr(cst_worker, "to_writable_copy", lambda p: p)
        de, prj = MagicMock(), MagicMock()
        prj.modeler.run_solver.return_value = True

        def fake_vba(_vba):
            (tmp_path / "f.txt").write_text("x")
            return True

        prj.schematic.execute_vba_code.side_effect = fake_vba
        monkeypatch.setattr(cst_worker, "open_session", lambda p: (de, prj))

        out = cst_worker.do_export_farfield(
            {"model_path": "C:/x.cst", "output_dir": str(tmp_path)})

        assert [Path(p).name for p in out] == ["f.txt"]
        prj.modeler.run_solver.assert_called_once()
        prj.save.assert_called_once()
        prj.schematic.execute_vba_code.assert_called_once()
        prj.close.assert_called_once()

    def test_export_reports_only_files_from_this_run(self, tmp_path, monkeypatch):
        """目录里既有的 txt 不能被报成本次结果（实测撞到：混进同目录另一份远场文件）。"""
        from servers.cst import cst_worker
        stale = tmp_path / "stale.txt"
        stale.write_text("旧文件")
        monkeypatch.setattr(cst_worker, "detect_farfield", lambda p: (False, ""))
        monkeypatch.setattr(cst_worker, "to_writable_copy", lambda p: p)
        de, prj = MagicMock(), MagicMock()

        def fake_vba(_vba):
            (tmp_path / "fresh.txt").write_text("新文件")
            return True

        prj.schematic.execute_vba_code.side_effect = fake_vba
        monkeypatch.setattr(cst_worker, "open_session", lambda p: (de, prj))

        out = cst_worker.do_export_farfield(
            {"model_path": "C:/x.cst", "output_dir": str(tmp_path)})

        assert [Path(p).name for p in out] == ["fresh.txt"]
        prj.modeler.run_solver.assert_not_called()  # 已有远场 → 不再求解

    def test_export_reports_nothing_when_directory_untouched(self, tmp_path, monkeypatch):
        """VBA 一个文件都没写（模型无 farfield 监视器）→ 返回空列表，不把既有 txt 当结果。"""
        from servers.cst import cst_worker
        (tmp_path / "stale.txt").write_text("旧文件")
        monkeypatch.setattr(cst_worker, "detect_farfield", lambda p: (False, ""))
        monkeypatch.setattr(cst_worker, "to_writable_copy", lambda p: p)
        de, prj = MagicMock(), MagicMock()
        prj.schematic.execute_vba_code.return_value = True
        monkeypatch.setattr(cst_worker, "open_session", lambda p: (de, prj))

        assert cst_worker.do_export_farfield(
            {"model_path": "C:/x.cst", "output_dir": str(tmp_path)}) == []

    def test_changed_txts_treats_touched_file_as_fresh(self, tmp_path):
        from servers.cst import cst_worker
        f = tmp_path / "same.txt"
        f.write_text("abc")
        before = cst_worker.snapshot_txts(str(tmp_path))
        assert cst_worker.changed_txts(str(tmp_path), before) == []   # 未动 → 不算本次
        f.write_text("ab")                                            # 大小变化
        assert [Path(p).name for p in cst_worker.changed_txts(str(tmp_path), before)] == ["same.txt"]


class TestFarfieldDetectionHints:
    """回归 F1：`get_tree_items()` 不含 Farfields 分支，判据必须认派生结果。

    实测 demo_horn_24GHz / Discone 都能导出远场，但结果树里只有
    `Tables\\1D Results\\GainMax` / 效率表 ⇒ 只认字面 "Farfield" 会漏报。
    """

    def test_pattern_tables_count_as_farfield(self):
        from servers.cst.cst_worker import has_farfield
        assert has_farfield(["Tables\\1D Results\\GainMax"]) is True
        assert has_farfield(["Tables\\1D Results\\GainPhi90"]) is True

    def test_efficiency_items_count_as_farfield(self):
        from servers.cst.cst_worker import has_farfield
        assert has_farfield(["1D Results\\Efficiencies\\Rad. Efficiency [1]"]) is True

    def test_literal_farfield_branch_still_detected(self):
        from servers.cst.cst_worker import has_farfield
        assert has_farfield(["1D Results\\Farfields\\farfield (f=1)"]) is True

    def test_s_parameters_only_is_not_farfield(self):
        from servers.cst.cst_worker import has_farfield
        items = ["1D Results\\S-Parameters\\S1,1", "1D Results\\Port signals\\i1",
                 "1D Results\\Power\\Excitation [1]\\Power Radiated"]
        assert has_farfield(items) is False


class TestDoInfoFarfieldMonitors:
    def _info_with_tree(self, monkeypatch, items):
        from servers.cst import cst_worker
        result_3d = MagicMock()
        result_3d.get_tree_items.return_value = items
        monkeypatch.setattr(cst_worker, "open_3d_results", lambda p: (MagicMock(), result_3d))
        return cst_worker.do_info({"model_path": "C:/x.cst"})

    def test_monitors_filled_when_only_gain_tables_exist(self, monkeypatch):
        info = self._info_with_tree(
            monkeypatch, ["1D Results\\S-Parameters\\S1,1", "Tables\\1D Results\\GainMax"])
        assert info["monitors"] == ["Farfield"]
        assert info["notes"] == ""
        assert info["solved"] is True
        assert info["ports"] == 1

    def test_notes_explain_when_no_farfield_signal(self, monkeypatch):
        info = self._info_with_tree(monkeypatch, ["1D Results\\S-Parameters\\S1,1"])
        assert info["monitors"] == []
        assert "远场" in info["notes"]


class TestRunJob:
    def test_unknown_op_reports_failure(self):
        from servers.cst.cst_worker import run_job
        out = run_job({"op": "nope"})
        assert out["success"] is False
        assert "Unknown op" in out["error"]
        assert out["error_type"] == "RuntimeError"
        assert out["result"] is None

    def test_success_path_carries_result_and_elapsed(self, monkeypatch):
        from servers.cst import cst_worker
        monkeypatch.setitem(cst_worker._OPS, "solve", lambda job: "C:/x.cst")
        out = cst_worker.run_job({"op": "solve"})
        assert out["success"] is True
        assert out["result"] == "C:/x.cst"
        assert out["op"] == "solve"
        assert out["elapsed_seconds"] >= 0.0

    def test_exception_becomes_failure_payload(self, monkeypatch):
        from servers.cst import cst_worker

        def boom(job):
            raise RuntimeError("run_solver returned False")

        monkeypatch.setitem(cst_worker._OPS, "solve", boom)
        out = cst_worker.run_job({"op": "solve"})
        assert out["success"] is False
        assert out["error"] == "run_solver returned False"
        assert "Traceback" in out["traceback"]


class TestMain:
    def test_writes_result_file_and_exit_code(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        monkeypatch.setitem(cst_worker._OPS, "solve", lambda job: "C:/x.cst")
        job_path = tmp_path / "job.json"
        result_path = tmp_path / "result.json"
        job_path.write_text(json.dumps({
            "op": "solve", "model_path": "C:/x.cst", "result_path": str(result_path),
        }), encoding="utf-8")

        rc = cst_worker.main(["--job", str(job_path)])

        assert rc == 0
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["success"] is True
        assert data["result"] == "C:/x.cst"

    def test_failure_exit_code_is_one(self, tmp_path, monkeypatch):
        from servers.cst import cst_worker
        monkeypatch.setitem(cst_worker._OPS, "solve", lambda job: (_ for _ in ()).throw(RuntimeError("nope")))
        job_path = tmp_path / "job.json"
        result_path = tmp_path / "result.json"
        job_path.write_text(json.dumps({
            "op": "solve", "model_path": "C:/x.cst", "result_path": str(result_path),
        }), encoding="utf-8")

        assert cst_worker.main(["--job", str(job_path)]) == 1
        assert json.loads(result_path.read_text(encoding="utf-8"))["error"] == "nope"

    def test_runs_without_result_path(self, tmp_path, monkeypatch):
        """result_path 缺省时不写文件，也不该抛异常（父进程靠 stdout 兜底）。"""
        from servers.cst import cst_worker
        monkeypatch.setitem(cst_worker._OPS, "solve", lambda job: "C:/x.cst")
        job_path = tmp_path / "job.json"
        job_path.write_text(json.dumps({"op": "solve", "model_path": "C:/x.cst"}), encoding="utf-8")
        assert cst_worker.main(["--job", str(job_path)]) == 0

    def test_missing_job_arg_exits(self):
        from servers.cst.cst_worker import _parse_args
        with pytest.raises(SystemExit):
            _parse_args([])


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
