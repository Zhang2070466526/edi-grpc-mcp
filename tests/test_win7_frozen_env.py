"""Win7 冻结栈（路线⑤）的守护测试 —— 把现场真崩过的两个坑锁住，防止复发。

背景：Win7 那台 = PythonWin7 3.10 + GBK 控制台 + PythonWin7 的 utf8_mode=0；
开发机 = utf8_mode=1。同一份文件在两个世界里结果不同，所以必须显式断言：

  坑 1（2026-09-14）：requirements-win7.txt 写了中文注释 → pip 23 读 -r 文件时按
        本地代码页(GBK)解码 → UnicodeDecodeError: 'gbk' codec can't decode byte 0x80
  坑 2（2026-09-14）：脚本 print 里含 GBK 没有的字符（U+2713 对勾等）→
        UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'

本地模拟 Win7 控制台的手段：PYTHONIOENCODING=gbk（子进程一起继承）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable

# 现场会上机的文件：任何一个字符都必须在 GBK 里
FIELD_FILES = [
    "requirements-win7.txt",
    "scripts/win7/check_pyd_imports.py",
    "scripts/win7/install_win7_env.py",
    "scripts/win7/patch_mcp_win7.py",
    "scripts/win7/check_dist_win7.py",
    "scripts/win7/build_win7_exe.py",
    "scripts/win7/setup_win7_env.bat",
    "scripts/win7/diag_win7_exe.py",
]
# 唯一硬约束（会让 Win7 上 import 直接崩的那几个）
HARD_PINS = ("mcp==1.12.4", "pydantic==2.10.6", "pydantic-core==2.27.2", "rpds-py==0.18.1")


def _gbk_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}  # 别让 hermes 的 PYTHONPATH 干扰
    env["PYTHONIOENCODING"] = "gbk"
    return env


def test_requirements_is_pure_ascii():
    """坑 1：pip 按本地代码页读 -r 文件，非 ASCII 会让它在中文 Win7 上崩。"""
    data = (REPO / "requirements-win7.txt").read_bytes()
    bad = [b for b in data if b > 127]
    assert not bad, "requirements-win7.txt 含 %d 个非 ASCII 字节，pip 23 在 GBK 环境会 UnicodeDecodeError" % len(bad)


def _active_pins(text: str) -> set:
    """只取生效的钉版行（忽略注释与空行），避免被说明文字误伤。"""
    return {ln.strip() for ln in text.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")}


def test_requirements_keeps_win7_hard_pins():
    """依赖可以增删，但这四条是 Win7 可用性的底线；cryptography 是 Rust 编译，不能进。"""
    pins = _active_pins((REPO / "requirements-win7.txt").read_text(encoding="ascii"))
    for pin in HARD_PINS:
        assert pin in pins, "缺少硬约束 %s（升上去 Win7 必崩）" % pin
    assert not [p for p in pins if p.startswith("cryptography")], \
        "cryptography 是 Rust 编译、Win7 红线，且本项目运行时不需要"


def test_field_files_are_gbk_safe():
    """坑 2：Win7 控制台是 GBK，print 里出现 GBK 没有的字符会崩在最后一步。"""
    bad = []
    for rel in FIELD_FILES:
        raw = (REPO / rel).read_bytes()
        for line_no, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            for ch in line:
                if ord(ch) > 127:
                    try:
                        ch.encode("gbk")
                    except UnicodeEncodeError:
                        bad.append("%s:%d U+%04X" % (rel, line_no, ord(ch)))
    assert not bad, "这些字符不在 GBK 里，现场打印会崩: %s" % bad[:10]


@pytest.mark.skipif(sys.version_info[:2] != (3, 10),
                    reason="扫描的是当前解释器的 site-packages，开发栈(3.12)本就含 Win7 红线扩展")
def test_scanner_survives_gbk_console():
    """扫描器要在 GBK 控制台下跑完并给出 ASCII 契约行（调用方靠它解析）。只在 .venv-win7 下有意义。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "check_pyd_imports.py")],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, "GBK 控制台下扫描失败:\n%s" % out[-500:]
    assert "SCAN_RESULT files=" in out, "缺少给调用方解析的 ASCII 契约行"
    assert "UnicodeEncodeError" not in out and "Traceback" not in out


def test_installer_check_survives_gbk_console():
    """安装器体检也要能在 GBK 控制台下跑完（--check 不起服务，秒级）。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "install_win7_env.py"), "--check"],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    assert "UnicodeEncodeError" not in out and "Traceback" not in out, out[-500:]


def test_installer_parses_scanner_contract():
    """安装器必须解析 ASCII 契约行而不是中文（否则编码错配时会误判 FAIL）。"""
    src = (REPO / "scripts" / "win7" / "install_win7_env.py").read_text(encoding="utf-8")
    assert "SCAN_RESULT files=" in src, "安装器没用 ASCII 契约行解析扫描结果"


def test_dist_checker_handles_missing_dir():
    """打包产物体检脚本：目录不存在要干净退出（rc=2），而不是崩。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "check_dist_win7.py"), "--dir", str(REPO / "dist" / "no-such")],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=120)
    assert p.returncode == 2 and "目录不存在" in (p.stdout or ""), p.stdout


def _win7_dist_or_skip():
    """找 Win7 产物：优先 dist-win7/edi-mcp（一仓双 venv 的约定），也认 dist/edi-mcp —— 但只认 3.10 打的。

    开发机那套 3.12 构建也在写 dist/edi-mcp，它是**上不了 Win7 的**（pydantic-core 2.46 红线），
    所以用 _internal 里的 python312/python310 区分，别拿开发构建去糊弄"0 红线"这条断言。
    """
    import pytest

    for cand in (REPO / "dist-win7" / "edi-mcp", REPO / "dist" / "edi-mcp"):
        if (cand / "edi_mcp_server.exe").exists():
            if (cand / "_internal" / "python310.dll").exists():
                return cand
    pytest.skip("没有 Win7 产物（.venv-win7 下跑 build_win7_exe.py --dist dist-win7/edi-mcp 生成）")


def test_dist_checker_reports_contract_on_built_dist():
    """若本机已打过 Win7 包，产物体检要给出 ASCII 契约行、0 红线 0 缺 CRT。"""
    dist = _win7_dist_or_skip()
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "check_dist_win7.py"), "--dir", str(dist)],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=600)
    out = (p.stdout or "") + (p.stderr or "")
    assert "DIST_RESULT files=" in out and "redlines=0" in out and "crt_missing=0" in out, out[-500:]


def test_builder_help_survives_gbk_console():
    """打包器 --help 在 GBK 控制台下要能跑完（它是现场会用的一键入口）。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "build_win7_exe.py"), "--help"],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0 and "--smoke" in out and "Traceback" not in out, out[-300:]


def test_builder_refuses_non_310():
    """打包器必须拒绝非 3.10 解释器：3.12 打出来的 exe 在 Win7 上必崩（2026-09-14 一仓双 venv 后加）。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "build_win7_exe.py"), "--no-build"],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    if sys.version_info[:2] == (3, 10):
        assert "必须用 Python 3.10 打包" not in out, out[-300:]
    else:
        assert p.returncode == 1 and "必须用 Python 3.10 打包" in out, out[-300:]


def test_builder_supports_dist_flag():
    """--dist 能把产物挪到别的目录（开发机那套 3.12 构建在用 dist/，别互相覆盖）。"""
    p = subprocess.run([PY, str(REPO / "scripts" / "win7" / "build_win7_exe.py"), "--help"],
                       capture_output=True, text=True, encoding="gbk", errors="replace",
                       cwd=str(REPO), env=_gbk_env(), timeout=120)
    assert "--dist" in (p.stdout or ""), p.stdout


def _run_dist_checker(d):
    return subprocess.run([PY, str(REPO / "scripts" / "win7" / "check_dist_win7.py"), "--dir", str(d)],
                          capture_output=True, text=True, encoding="gbk", errors="replace",
                          cwd=str(REPO), env=_gbk_env(), timeout=300)


def _system_ucrt():
    import pytest
    ref = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ucrtbase.dll"
    if not ref.is_file():
        pytest.skip("本机没有 System32/ucrtbase.dll")
    return ref


def test_dist_checker_demotes_bundled_system_ucrt(tmp_path):
    """坑 3（2026-09-14 现场）：Win7 上打包，PyInstaller 会把本机 UCRT 收进包。

    它与本机 System32 那份逐字节相同 → 不该算红线（本机 Python 此刻就在用同一文件）。
    开发机打包看不出这条：那份 CPython 不带 ucrtbase，不会进包。
    """
    import shutil

    d = tmp_path / "dist"
    (d / "_internal").mkdir(parents=True)
    shutil.copyfile(_system_ucrt(), d / "_internal" / "ucrtbase.dll")
    p = _run_dist_checker(d)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, out[-600:]
    assert "DIST_RESULT files=1 redlines=0 crt_missing=0" in out, out[-600:]
    assert "同源" in out and "降级为提示" in out, out[-600:]


def test_dist_checker_still_flags_foreign_ucrt(tmp_path):
    """别把"外来 UCRT"也放过：只多一个字节(md5 变)就仍按红线报。"""
    import shutil

    d = tmp_path / "dist"
    (d / "_internal").mkdir(parents=True)
    dst = d / "_internal" / "ucrtbase.dll"
    shutil.copyfile(_system_ucrt(), dst)
    with open(dst, "ab") as fh:
        fh.write(b"\x00")           # PE 结构不变，md5 变了 → 视为外来版本
    p = _run_dist_checker(d)
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 1 and "redlines=1" in out, out[-600:]
