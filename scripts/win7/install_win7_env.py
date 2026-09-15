#!/usr/bin/env python
"""Win7 环境一键安装（路线⑤：全栈冻结到 Rust<1.78 时代）。

把「建 venv → 装依赖 → 打 mcp 补丁 → 体检」串成一个幂等脚本，每步都有判据，
失败即停并打印原因。设计目标：现场工程师只看最后那张 PASS/FAIL 表就能判断成败。

用法：
    python scripts/win7/install_win7_env.py                      # 装到 .venv-win7（默认）
    python scripts/win7/install_win7_env.py --check               # 只体检，不装
    python scripts/win7/install_win7_env.py --download W:\\wh     # 联网机器上先下好离线 wheel
    python scripts/win7/install_win7_env.py --offline W:\\wh      # Win7 上用离线 wheel 装
    python scripts/win7/install_win7_env.py --recreate            # 删掉重建 venv
    python scripts/win7/install_win7_env.py --no-app-checks       # 只装依赖，跳过应用层自检
    python scripts/win7/install_win7_env.py --no-smoke            # 跳过起服务冒烟

约定：
  - 解释器必须是 Python 3.10（路由⑤ 的 wheel 全是 cp310，且依赖表在 3.10 上实测 403 测试全绿）；
    非 3.10 直接报错退出，除非给 --allow-any-python。
  - 默认走清华镜像（现场网速友好），用 --index-url 换源。
  - 不升级 pip：新版 pip 已不再支持 Win7，装 Python 时带的那个就够用。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent   # scripts/win7/ -> 仓库根
TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"

# Win7 是 GBK 控制台：Dingbats / Emoji（U+2705 之类）不在 GBK 里，print 会抛
# UnicodeEncodeError 把安装流程崩在最后一行。降级成 '?'，并保持输出只用 GBK 安全字符。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

results: list[tuple[str, bool, str]] = []


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    say("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + detail) if detail else ""))
    return ok


def warn(msg: str) -> None:
    """提醒但不下结论、不拦流程（例如：文件非 ASCII 在 UTF-8 机器上没事、在 GBK 机器上会崩）。"""
    say("  [WARN] %s" % msg)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", **kw)


def py_in(venv: Path) -> Path:
    return venv / "Scripts" / "python.exe"


# ── 0. 体检 ───────────────────────────────────────────────────────
def check_host(allow_any_python: bool, req: Path) -> bool:
    say("\n[0/5] 环境体检")
    ver = "%d.%d.%d" % sys.version_info[:3]
    step("解释器 = Python 3.10（当前 %s）" % ver,
         sys.version_info[:2] == (3, 10) or allow_any_python,
         "" if sys.version_info[:2] == (3, 10) else "请用 PythonWin7 的 3.10 重跑，或加 --allow-any-python")

    system32 = Path(r"C:\Windows\System32")
    # 别查文件是否存在：Win10+ 上 api-ms-win-* 是 API set，由加载器虚拟解析，System32 里没有实体文件。
    # 直接按加载器的方式「真去加载」，Win7 缺 UCRT 时这里才会失败。
    import ctypes
    probes = ["api-ms-win-crt-runtime-l1-1-0.dll", "api-ms-win-crt-heap-l1-1-0.dll",
              "api-ms-win-core-path-l1-1-0.dll", "ucrtbase.dll"]
    missing = []
    for name in probes:
        try:
            ctypes.WinDLL(name)
        except OSError:
            missing.append(name)
    step("UCRT 可用（实测加载 %d 个 API set）" % len(probes), not missing,
         "" if not missing else "缺: %s —— 装 KB3118401（Win7 专用号，不是 KB2999226）后重启"
         % ", ".join(missing))

    for dll in ("vcruntime140.dll", "msvcp140.dll"):
        p = system32 / dll
        step("系统里有 %s（x64 扩展的 C++ 运行时）" % dll, True,
             "已装" if p.exists() else "未装 —— 若后面扩展加载失败（WinError 126），装 VC++ 2015-2022 x64 运行库")

    step("找到 %s" % req.name, req.exists(), str(req))
    step("找到 scripts/win7/patch_mcp_win7.py", (REPO / "scripts" / "win7" / "patch_mcp_win7.py").exists())
    if req.exists() and any(b > 127 for b in req.read_bytes()):
        warn("requirements 含非 ASCII 字符：pip(23) 读 -r 文件时按**本地代码页**解码（中文 Win7 = GBK），"
             "会以 UnicodeDecodeError: 'gbk' codec ... auto_decode 崩掉。开发机多为 UTF-8 所以看不出来。"
             "请保持该文件纯 ASCII（或存成带 BOM 的 UTF-8）。")
    return all(ok for _, ok, _ in results)


# ── 1. venv ───────────────────────────────────────────────────────
def have_pip(py: Path) -> bool:
    return run([str(py), "-m", "pip", "--version"]).returncode == 0


def ensure_pip(py: Path) -> bool:
    """venv 里必须有 pip：python -m venv 自带，uv 建的没有，用 ensurepip 补。"""
    if have_pip(py):
        return True
    run([str(py), "-m", "ensurepip", "--upgrade", "--default-pip"])
    if have_pip(py):
        return True
    uv = shutil.which("uv")          # 兜底：ensurepip 被裁掉的发行版
    if uv:
        run([uv, "pip", "install", "--python", str(py), "pip"])
    return have_pip(py)


def make_venv(venv: Path, recreate: bool) -> bool:
    say("\n[1/5] 虚拟环境 %s" % venv)
    if recreate and venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
        say("  已按要求删除旧 venv")
    if py_in(venv).exists():
        v = run([str(py_in(venv)), "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"])
        if not step("复用已有 venv（%s）" % v.stdout.strip(), v.returncode == 0):
            return False
    else:
        p = run([sys.executable, "-m", "venv", str(venv)])
        if not step("创建 venv", p.returncode == 0 and py_in(venv).exists(),
                    (p.stderr or p.stdout)[-200:]):
            return False
    return step("venv 里有 pip", ensure_pip(py_in(venv)),
                "" if have_pip(py_in(venv)) else "ensurepip 也失败：请用 PythonWin7 官方安装版（自带 pip）")


def pip_python() -> tuple[Path, Path | None]:
    """返回 (带 pip 的 python, 需要清理的临时 venv)。"""
    if have_pip(Path(sys.executable)):
        return Path(sys.executable), None
    tmp = Path(tempfile.mkdtemp(prefix="win7-pipdl-"))
    run([sys.executable, "-m", "venv", str(tmp)])
    return py_in(tmp), tmp


# ── 2. 依赖 ───────────────────────────────────────────────────────
def pip_args(index_url: str | None, offline: Path | None) -> list[str]:
    # 现场网络往往很慢：放宽 pip 的超时/重试（默认 15s 会直接 ReadTimeout）
    common = ["--timeout", "60", "--retries", "5"]
    if offline:
        return common + ["--no-index", "--find-links", str(offline)]
    return common + ["--index-url", index_url or TSINGHUA]


def download_wheels(req: Path, dest: Path, index_url: str | None) -> bool:
    say("\n[2/5] 下载离线 wheel → %s" % dest)
    dest.mkdir(parents=True, exist_ok=True)
    py, tmp = pip_python()
    try:
        p = run([str(py), "-m", "pip", "download", "-r", str(req), "-d", str(dest)]
                + pip_args(index_url, None))
        if p.returncode != 0:
            return step("pip download", False, (p.stderr or p.stdout)[-300:])
        n = len(list(dest.glob("*")))
        return step("下载完成（%d 个文件）" % n, n > 0)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def install_deps(venv: Path, req: Path, index_url: str | None, offline: Path | None) -> bool:
    say("\n[2/5] 安装依赖（%s）" % (offline and "离线 %s" % offline or (index_url or TSINGHUA)))
    cmd = [str(py_in(venv)), "-m", "pip", "install", "-r", str(req)] + pip_args(index_url, offline)
    p = run(cmd)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")
        if "UnicodeDecodeError" in err or "auto_decode" in err or "'gbk'" in err:
            err += ("\n  ↑ 这是 requirements 文件含非 ASCII（中文注释）导致的：pip 在 GBK 代码页下按本地编码读 -r。"
                    "换成本仓库的纯 ASCII requirements-win7.txt（或另存为带 BOM 的 UTF-8）即可。")
        return step("pip install", False, err[-400:])
    tail = [ln for ln in p.stdout.splitlines() if "Successfully installed" in ln]
    return step("pip install", True, tail[-1][:160] if tail else "已满足（幂等）")


# ── 3. 补丁 ───────────────────────────────────────────────────────
def py_eval(venv: Path, code: str) -> tuple[int, str]:
    """在目标 venv 里跑一小段 python，返回 (退出码, 合并输出)。"""
    p = run([str(py_in(venv)), "-c", code], cwd=str(REPO))
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def apply_patch(venv: Path) -> bool:
    say("\n[3/5] 打 mcp Win7 补丁（mcp<=1.12.4 的 Context 识别崩溃）")
    if py_eval(venv, "import mcp")[0] != 0:
        return step("跳过补丁", True, "该 venv 里没有 mcp（requirements 不是 route-E 那份？）—— 无对象可打")
    p = run([str(py_in(venv)), str(REPO / "scripts" / "win7" / "patch_mcp_win7.py")], cwd=str(REPO))
    out = (p.stdout + p.stderr).strip()
    chk = run([str(py_in(venv)), str(REPO / "scripts" / "win7" / "patch_mcp_win7.py"), "--check"], cwd=str(REPO))
    return step("补丁已生效", p.returncode == 0 and chk.returncode == 0, out[-160:])


# ── 4. 依赖能 import ──────────────────────────────────────────────
def check_imports(venv: Path) -> bool:
    say("\n[4/5] 依赖 import 关")
    rc, out = py_eval(venv, "import mcp, grpc, google.protobuf, pydantic, numpy, win32com, "
                            "matplotlib, psutil; print('OK', pydantic.VERSION)")
    return step("import mcp/grpc/protobuf/pydantic/numpy/win32com/matplotlib/psutil", rc == 0, out[-200:])


# ── 5. 应用层自检 ─────────────────────────────────────────────────
def check_extensions(venv: Path) -> bool:
    say("\n[5/5] Win7 红线静态扫描（全栈 .pyd 导入表）")
    # 直接调扫描器的 CLI 契约，别在这里重复实现扫描逻辑（DRY）
    p = run([str(py_in(venv)), str(REPO / "scripts" / "win7" / "check_pyd_imports.py")], cwd=str(REPO))
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    # 解析扫描器的 ASCII 契约行（回退到中文匹配，兼容被替换成旧版扫描器的情况）
    m = (re.search(r"SCAN_RESULT files=(\d+) hits=(\d+)", out)
         or re.search(r"扫描 (\d+) 个扩展，(\d+) 个命中 Win7 红线", out))
    if p.returncode != 0 or not m:
        return step("扫描扩展", False, out[-300:])
    files, hits = m.group(1), m.group(2)
    return step("%s 个扩展 %s 命中 Win7 红线" % (files, hits), hits == "0",
                out[-300:] if hits != "0" else "")


def check_tools(venv: Path) -> bool:
    # 用 sentinel 而不是「取最后一行」：servers 包会往 stderr 打诊断信息（tool descriptions trimmed=...），
    # 合并输出里它排在数字之后，取末行会取到垃圾。
    rc, out = py_eval(venv, "from servers import mcp; import servers.registry_server;"
                            "print('TOOLS=%d' % len(mcp._tool_manager.list_tools()))")
    m = re.search(r"TOOLS=(\d+)", out)
    n = m.group(1) if m else "?"
    good = rc == 0 and n.isdigit() and int(n) > 0
    return step("工具注册 = %s 个（>0）" % n, good, "" if good else out[-300:])


def smoke_server(venv: Path, port: int) -> bool:
    say("\n[冒烟] 起服务并探测 /ready（端口 %d）" % port)
    proc = subprocess.Popen([str(py_in(venv)), "start_servers.py",
                             "--transport", "streamable-http", "--port", str(port)],
                            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                return step("服务启动", False, "进程已退出：%s" % (proc.stdout.read() or "")[-200:])
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/ready" % port, timeout=3) as r:
                    body = r.read().decode("utf-8", "replace")
                return step("服务启动 + /ready 正常", '"tool_count":' in body and '"tool_count":0' not in body, body[:200])
            except Exception:
                continue
        return step("服务启动", False, "30 秒内 /ready 无响应")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


# ── 主流程 ────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="Win7 环境一键安装（路线⑤）")
    ap.add_argument("--venv", default=".venv-win7", help="venv 目录（默认 .venv-win7）")
    ap.add_argument("--requirements", default="requirements-win7.txt")
    ap.add_argument("--index-url", help="pip 源（默认清华镜像）")
    ap.add_argument("--offline", help="离线 wheel 目录（\u4e0e --download 配套）")
    ap.add_argument("--download", help="只下载 wheel 到该目录，然后退出")
    ap.add_argument("--recreate", action="store_true", help="删除并重建 venv")
    ap.add_argument("--check", action="store_true", help="只体检，不安装")
    ap.add_argument("--no-app-checks", action="store_true", help="跳过依赖/扩展/工具自检")
    ap.add_argument("--no-smoke", action="store_true", help="跳过起服务冒烟")
    ap.add_argument("--port", type=int, default=50026)
    ap.add_argument("--allow-any-python", action="store_true", help="不强制 Python 3.10")
    a = ap.parse_args()

    venv = Path(a.venv) if Path(a.venv).is_absolute() else REPO / a.venv
    req = Path(a.requirements) if Path(a.requirements).is_absolute() else REPO / a.requirements

    say("=" * 74)
    say("Win7 环境安装（路线⑤：mcp<=1.12.4 / pydantic<=2.10 / Rust<1.78 时代依赖）")
    say("仓库: %s\nvenv: %s\n解释器: %s" % (REPO, venv, sys.executable))
    say("=" * 74)
    if not check_host(a.allow_any_python, req):
        say("\n体检未过，先按上面的提示处理，再重跑本脚本。")
        return 1
    if a.check:
        say("\n--check 模式：体检全部通过。")
        return 0

    if a.download:
        ok = download_wheels(req, Path(a.download), a.index_url)
        return 0 if ok else 1

    if not make_venv(venv, a.recreate):
        return 1
    if not install_deps(venv, req, a.index_url, Path(a.offline) if a.offline else None):
        return 1
    if not apply_patch(venv):
        return 1
    if not a.no_app_checks:
        if not check_imports(venv) or not check_extensions(venv) or not check_tools(venv):
            return 1
        if not a.no_smoke and not smoke_server(venv, a.port):
            return 1

    say("\n" + "=" * 74)
    say("结果汇总")
    for name, ok, detail in results:
        say("  %-4s %s" % ("PASS" if ok else "FAIL", name))
    failed = [n for n, ok, _ in results if not ok]
    say("\n%s（%d 项，失败 %d）" % ("全部通过" if not failed else "有失败", len(results), len(failed)))
    if not failed:
        say("下一步: %s\\Scripts\\activate 之后跑 python start_servers.py --transport streamable-http --port %d"
            % (venv, a.port))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
