"""测试公共工具层 utils.py —— 文件校验、错误响应、地址管理、链接生成。"""
from pathlib import Path

import pytest



class TestValidateFile:
    def test_existing_file(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.cst"
        f.write_text("x")
        assert validate_file(str(f), (".cst",)) == str(f.resolve())

    def test_missing_file(self, tmp_path):
        from servers.utils import validate_file
        with pytest.raises(FileNotFoundError):
            validate_file(str(tmp_path / "missing.cst"))

    def test_wrong_extension(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.txt"
        f.write_text("x")
        with pytest.raises(ValueError):
            validate_file(str(f), (".cst",))

    def test_no_extension_filter(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.any"
        f.write_text("x")
        assert validate_file(str(f)) == str(f.resolve())

    def test_extension_case_insensitive(self, tmp_path):
        from servers.utils import validate_file
        f = tmp_path / "m.CST"
        f.write_text("x")
        assert validate_file(str(f), (".cst",)) == str(f.resolve())

    def test_rejects_non_str(self):
        from servers.utils import validate_file
        for bad in (None, 123, ["x"], {"p": 1}):
            with pytest.raises(ValueError):
                validate_file(bad)


class TestToolError:
    def test_basic(self):
        from servers.utils import error_response
        assert error_response("CODE", "msg") == {
            "success": False, "error_code": "CODE", "message": "msg",
            "hint": "do_not_retry",
        }

    def test_retryable(self):
        from servers.utils import error_response
        r = error_response("CODE", "msg", retryable=True)
        assert r["retryable"] is True
        assert r["hint"] == "retry_safe"

    def test_extra(self):
        from servers.utils import error_response
        r = error_response("CODE", "msg", detail="x")
        assert r["details"] == {"detail": "x"}


class TestRequirePosition:
    def test_valid(self):
        from servers.utils import require_position
        pos, err = require_position({"x": 100, "y": 200})
        assert err is None and pos == {"x": 100, "y": 200}

    def test_rejects_non_dict(self):
        from servers.utils import require_position
        for bad in (None, "x=1", [1, 2], 42):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_missing_axis(self):
        from servers.utils import require_position
        pos, err = require_position({"x": 1})
        assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_numeric(self):
        from servers.utils import require_position
        for bad in ({"x": "1", "y": 2}, {"x": 1, "y": "2"}, {"x": True, "y": 1}):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_finite(self):
        from servers.utils import require_position
        for bad in ({"x": float("nan"), "y": 1}, {"x": 1, "y": float("inf")}):
            pos, err = require_position(bad)
            assert pos is None and err["error_code"] == "INVALID_PARAMETERS"


class TestRequireUuid:
    def test_valid(self):
        from servers.utils import require_uuid
        u = "12345678-1234-4234-8234-123456789abc"
        v, err = require_uuid(u)
        assert err is None and v == u

    def test_rejects_invalid(self):
        from servers.utils import require_uuid
        for bad in ("not-a-uuid", "12345", "uuid-1", ""):
            v, err = require_uuid(bad)
            assert v == "" and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_str(self):
        from servers.utils import require_uuid
        v, err = require_uuid(123)
        assert v == "" and err["error_code"] == "INVALID_PARAMETERS"


class TestRequireNonempty:
    def test_valid(self):
        from servers.utils import require_nonempty
        v, err = require_nonempty("  x  ")
        assert err is None and v == "x"

    def test_rejects_empty(self):
        from servers.utils import require_nonempty
        for bad in ("", "   "):
            v, err = require_nonempty(bad)
            assert v == "" and err["error_code"] == "INVALID_PARAMETERS"

    def test_rejects_non_str(self):
        from servers.utils import require_nonempty
        for bad in (None, 123, ["x"]):
            v, err = require_nonempty(bad)
            assert v == "" and err["error_code"] == "INVALID_PARAMETERS"


class TestBuildFileLink:
    def test_markdown_link(self, tmp_path):
        from servers.utils import build_file_link
        f = tmp_path / "r.pdf"
        f.write_text("x")
        r = build_file_link(str(f), label="报告")
        assert r["file_uri"].startswith("file://")
        assert r["download_url"].startswith("http")
        assert "/documents/" in r["download_url"]
        assert r["markdown_link"].startswith("[报告](http")

    def test_missing_file_fallback(self, tmp_path):
        from servers.utils import build_file_link
        r = build_file_link(str(tmp_path / "nope.png"), label="图")
        assert r["download_url"] == ""
        assert r["markdown_link"].startswith("[图](file://")
        assert "仅引擎机有效" in r["markdown_link"]


class TestServerAddress:
    def test_localhost_base_url(self):
        from servers.utils import set_server_address, get_server_base_url
        set_server_address("127.0.0.1", 50026)
        assert get_server_base_url() == "http://127.0.0.1:50026"

    def test_wildcard_host_maps_to_localhost(self):
        from servers.utils import set_server_address, get_server_base_url
        set_server_address("0.0.0.0", 9000)
        assert get_server_base_url() == "http://127.0.0.1:9000"


class TestUptime:
    def test_non_negative(self):
        from servers.utils import server_uptime_seconds
        assert server_uptime_seconds() >= 0


class TestDecodeLocalText:
    def test_gbk(self):
        from servers.utils import decode_local_text
        assert decode_local_text("专业".encode("gbk")) == "专业"

    def test_utf8_bom(self):
        from servers.utils import decode_local_text
        assert decode_local_text(b"\xef\xbb\xbf" + "专业".encode("utf-8")) == "专业"

    def test_ascii(self):
        from servers.utils import decode_local_text
        assert decode_local_text(b"freq 1.0") == "freq 1.0"

    def test_none_and_empty(self):
        from servers.utils import decode_local_text
        assert decode_local_text(None) == ""
        assert decode_local_text(b"") == ""


class TestTokenStore:
    def test_token_is_urlquote_permanent(self):
        """token 就是 urlencode 路径：确定性、无过期、重启（新实例）后可解。"""
        from servers.token_registry import TokenStore
        t1, url1 = TokenStore(route="/x").register("/tmp/a b.pdf")
        t2, url2 = TokenStore(route="/x").register("/tmp/a b.pdf")
        assert t1 == t2 and url1 == url2          # 同路径同 token（确定性）
        assert "%20" in t1                        # urlencode，不是随机/base64
        entry = TokenStore(route="/x").lookup(t1)  # 新实例 = 服务重启
        assert entry["path"] == "/tmp/a b.pdf"
        assert "expires_at" not in entry           # 不过期
        assert "/x/" in url1


class TestResultPath:
    """默认产物目录：`<result_dir>/<工具名>_<时间戳>[_序号]<ext>`。"""

    def test_default_result_path_format(self):
        from servers.utils import default_result_path
        p = default_result_path("capture_schematic", ".png")
        assert p.endswith(".png")
        assert "capture_schematic_" in p

    def test_extension_without_dot_is_normalized(self):
        """少写点号要自动补，别产出 `x_20261116_120000png`。"""
        from servers.utils import default_result_path
        p = default_result_path("draw_chart", "png")
        assert p.endswith(".png"), p
        assert "png.png" not in p

    def test_same_millisecond_does_not_collide(self, monkeypatch):
        """同毫秒连续调用必须拿到不同路径（时间戳只到秒时会同名覆盖）。

        2026-11-16：`result_dir` 下循环出图时同秒撞名，产物互相覆盖难排查。
        """
        import servers.utils as u
        monkeypatch.setattr(u.time, "time", lambda: 1794825050.123)
        a = u.result_subdir("draw_chart")
        b = u.result_subdir("draw_chart")
        assert a != b, f"同毫秒两次应不同，实际都是 {a}"
        assert Path(a).is_dir() and Path(b).is_dir()
        Path(a).rmdir()
        Path(b).rmdir()

    def test_same_millisecond_file_path_not_reused(self, monkeypatch, tmp_path):
        """文件路径：同毫秒且前一个文件已存在时要换名，不能返回已存在路径。"""
        import servers.utils as u
        monkeypatch.setattr(u, "result_dir", lambda: str(tmp_path))
        monkeypatch.setattr(u.time, "time", lambda: 1794825050.456)
        first = u.default_result_path("x", ".png")
        Path(first).write_text("exists")
        second = u.default_result_path("x", ".png")
        assert second != first, "已有文件时不能返回同一路径（会被覆盖）"
        assert not Path(second).exists()

    def test_result_dir_is_absolute(self, monkeypatch, tmp_path):
        """MCP_RESULT_DIR 配成相对路径时也要解析成绝对路径。"""
        import servers.utils as u
        from servers import settings as st

        class _S:
            mcp_result_dir = "rel_out"

        monkeypatch.setattr(st, "get_settings", lambda: _S())
        monkeypatch.chdir(tmp_path)
        d = u.result_dir()
        assert Path(d).is_absolute(), d
        assert Path(d) == (tmp_path / "rel_out").resolve()


class TestPruneEmptyDefaultDirs:
    """工具失败时回收它刚建的空默认产物目录（2026-11-16，ART-1）。

    实测背景：`result_subdir()` 先建目录再执行，失败调用会在 `%TEMP%/mcp_result` 下
    攒空目录（实测残留 cst_export_snp_* 2 个、export_simulation_report_* 4 个）。
    """

    def _tool(self, monkeypatch, tmp_path, *, mode: str, boom: bool = False):
        import servers.utils as u

        monkeypatch.setattr(u, "result_dir", lambda: str(tmp_path / "out"))

        @u.prune_empty_default_dirs
        def tool():
            d = u.result_subdir("demo_tool")
            if boom:
                raise RuntimeError("上游炸了")
            if mode == "write":
                Path(d, "a.png").write_text("x")
            # mode == "submit_only"：模拟异步提交成功但目录还是空的
            return {"success": mode != "fail", "dir": d}

        return tool

    def test_failure_removes_empty_dir(self, monkeypatch, tmp_path):
        r = self._tool(monkeypatch, tmp_path, mode="fail")()
        assert r["success"] is False
        assert not Path(r["dir"]).exists(), "失败后不该留空目录"

    def test_success_with_products_keeps_dir(self, monkeypatch, tmp_path):
        r = self._tool(monkeypatch, tmp_path, mode="write")()
        assert Path(r["dir"]).is_dir()
        assert (Path(r["dir"]) / "a.png").is_file()

    def test_success_but_still_empty_is_kept(self, monkeypatch, tmp_path):
        """异步工具提交成功时目录还是空的，后台 worker 马上写 —— 不能删。"""
        r = self._tool(monkeypatch, tmp_path, mode="submit_only")()
        assert r["success"] is True
        assert Path(r["dir"]).is_dir(), "success=True 的调用一律不动"

    def test_exception_also_prunes_then_reraises(self, monkeypatch, tmp_path):
        import servers.utils as u

        tool = self._tool(monkeypatch, tmp_path, mode="submit_only", boom=True)
        with pytest.raises(RuntimeError):
            tool()
        assert list((tmp_path / "out").glob("demo_tool_*")) == [], "抛异常也要回收空目录"

    def test_leaves_non_default_dirs_alone(self, monkeypatch, tmp_path):
        """只清理本次 result_subdir 建的目录；用户显式指定的目录不碰。"""
        import servers.utils as u

        monkeypatch.setattr(u, "result_dir", lambda: str(tmp_path / "out"))
        keep = tmp_path / "user_dir"
        keep.mkdir()

        @u.prune_empty_default_dirs
        def tool():
            return {"success": False, "dir": str(keep)}

        tool()
        assert keep.is_dir(), "不是默认产物目录，不该被删"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-p", "no:cacheprovider"])
