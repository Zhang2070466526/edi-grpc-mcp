#!/usr/bin/env python
"""Win7 exe 一键打包（路线⑤：PythonWin7 3.10 + 冻结依赖）。

等价于 scripts/build.ps1，但**不依赖 uv / PowerShell**（Win7 上都没有）：
    打包 → 生成 dist/edi-mcp/.env → 拷 start_server.bat → 产物 Win7 体检 →（可选）冒烟

用法：
    python scripts/win7/build_win7_exe.py                 # 打包 + 体检
    python scripts/win7/build_win7_exe.py --smoke         # 再起一次 exe 探 /ready
    python scripts/win7/build_win7_exe.py --smoke --port 50027
    python scripts/win7/build_win7_exe.py --no-build      # 只补 .env / 拷 bat / 体检
    python scripts/win7/build_win7_exe.py --dist dist-win7/edi-mcp   # 产物另放，别和开发机构建混一起

必须用 **Python 3.10** 跑（本脚本会拒绝其它版本）：3.12 下打出来的 exe 在 Win7 上必崩
（pydantic-core 2.46 的 Rust 扩展导入 Win8+/Win10 符号）。用：
    .venv-win7\\Scripts\\python.exe scripts\\build_win7_exe.py --smoke

前置（一次性）：
    .venv-win7\\Scripts\\python.exe -m pip install pyinstaller==6.22.3
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent   # scripts/win7/ -> 仓库根
DIST = REPO / "dist" / "edi-mcp"
EXE = DIST / "edi_mcp_server.exe"
PYINSTALLER_PIN = "6.22.3"

# 产物自带的 .env 模板（同 build.ps1；纯 ASCII 之外的中文注释只在 Python 里生成，不落文件）
ENV_TEMPLATE = """# EDI gRPC MCP configuration - edit paths for this computer
EDA_GRPC_SERVER=127.0.0.1:50055
# EDI exe path: leave empty to auto-detect (EDI.exe > EDA-PMDS.exe > CAIS.exe)
EDI_PATH=
# TurboCharts path: leave empty to auto-detect (turbocharts_app.exe > TurboCharts.exe)
TURBOCHARTS_PATH=
MCP_TRANSPORT=streamable-http
MCP_PORT={port}
# Process whitelist: only allow these processes to access /mcp (comma-separated substrings). Leave empty to disable.
MCP_ALLOWED_PROCESSES=edi-agent-service.exe
# Optional: image vision analysis (enabled when all three are configured)
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
# Optional: Chat AI (LLM multi-round tool calling). Leave empty to disable.
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
# Optional: simulation report rendering
REPORT_RENDER_URL=http://127.0.0.1:17867/api/v1/reports/render
"""

try:  # Win7 是 GBK 控制台
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# 注：下面的 say/step/run + 上面那段 GBK 兜底网，与 install_win7_env.py 是**刻意重复**的 ——
#     现场是按单文件拷贝的（只带需要的那几个脚本过去），所以各自独立可用，不抽共享模块。

results: list[tuple[str, bool, str]] = []


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    say("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  —— " + detail) if detail else ""))
    return ok


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=str(REPO), **kw)


def rel_dist() -> str:
    """产物目录相对仓库的路径（PASS/FAIL 标签要如实反映 --dist 给的位置）。"""
    try:
        return os.path.relpath(DIST, REPO).replace("\\", "/")
    except Exception:
        return str(DIST)


def pyinstaller_version(py: Path) -> str:
    p = run([str(py), "-m", "PyInstaller", "--version"])
    return (p.stdout or "").strip().splitlines()[-1] if p.returncode == 0 else ""


def do_build(py: Path) -> bool:
    say("\n[1/4] PyInstaller 打包（spec: scripts/edi_mcp_server.spec）")
    ver = pyinstaller_version(py)
    if not ver:
        return step("PyInstaller 可用", False,
                    "没装：%s -m pip install pyinstaller==%s" % (py, PYINSTALLER_PIN))
    step("PyInstaller 版本 %s" % ver, True,
         "" if ver == PYINSTALLER_PIN else "本仓库只在 %s 上实测过（bootloader Win7 0 红线）" % PYINSTALLER_PIN)
    # --distpath/--workpath 跟着 --dist 走：开发机那套 3.12 构建也在用 dist/ 与 build/，别互相覆盖
    p = run([str(py), "-m", "PyInstaller", "--clean", "--noconfirm",
             "--distpath", str(DIST.parent), "--workpath", str(REPO / "build" / DIST.parent.name),
             "scripts/edi_mcp_server.spec"])
    if not EXE.exists():
        tail = (p.stdout or "")[-400:] + (p.stderr or "")[-400:]
        return step("生成 %s" % EXE.name, False, tail.replace("\n", " | "))
    size = EXE.stat().st_size / 1048576
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1048576
    return step("生成 %s" % EXE.name, True, "exe %.1f MB / 目录 %.1f MB" % (size, total))


def write_env(port: int) -> bool:
    say("\n[2/4] 生成产物配置 .env（build.ps1 的 [6/7] 步，这里不依赖 PowerShell）")
    (DIST / ".env").write_text(ENV_TEMPLATE.format(port=port), encoding="utf-8", newline="\n")
    return step("写 %s/.env" % rel_dist(), (DIST / ".env").exists(), "MCP_PORT=%d" % port)


def copy_launcher() -> bool:
    say("\n[3/4] 拷启动脚本 start_local.bat / start_remote.bat")
    ok = True
    for name in ("start_local.bat", "start_remote.bat"):
        src = REPO / "scripts" / name
        dst = DIST / name
        if not src.exists():
            ok = step("拷 %s" % name, False, "找不到 %s" % src)
            continue
        shutil.copyfile(src, dst)
        ok = step("%s/%s" % (rel_dist(), name), dst.exists()) and ok
    return ok


def check_dist(py: Path) -> bool:
    say("\n[4/4] 产物 Win7 体检（红线 + CRT 清单）")
    p = run([str(py), "scripts/win7/check_dist_win7.py", "--dir", str(DIST)])
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in out.splitlines():          # 原样透传，别在这里复制扫描器的输出格式
        say("  " + line.rstrip())
    return step("产物 0 红线 + CRT 齐备", p.returncode == 0, "" if p.returncode == 0 else out[-300:])


def smoke(port: int) -> bool:
    say("\n[冒烟] 起 exe 探 /ready（端口 %d）" % port)
    proc = subprocess.Popen([str(EXE), "--transport", "streamable-http", "--port", str(port)],
                            cwd=str(DIST), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(45):
            time.sleep(1)
            if proc.poll() is not None:
                return step("exe 启动", False, "进程已退出：%s" % ((proc.stdout.read() or "")[-200:]))
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/ready" % port, timeout=3) as r:
                    body = r.read().decode("utf-8", "replace")
                return step("exe 启动 + /ready", '"tool_count":' in body and '"tool_count":0' not in body, body[:200])
            except Exception:
                continue
        return step("exe 启动", False, "45 秒内 /ready 无响应")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main() -> int:
    global DIST, EXE          # 让 --dist 对整个脚本的 helper 生效（须在首次使用 DIST 之前声明）
    ap = argparse.ArgumentParser(description="Win7 exe 一键打包（不依赖 uv/PowerShell）")
    ap.add_argument("--port", type=int, default=50026, help="写进 .env 的 MCP_PORT（默认 50026）")
    ap.add_argument("--dist", default=str(DIST), help="产物目录（默认 dist/edi-mcp；给 Win7 打包建议 dist-win7/edi-mcp，别和开发机构建混）")
    ap.add_argument("--no-build", action="store_true", help="跳过 PyInstaller，只补 .env/拷 bat/体检")
    ap.add_argument("--smoke", action="store_true", help="打包后起一次 exe 探 /ready")
    a = ap.parse_args()
    DIST = Path(a.dist) if Path(a.dist).is_absolute() else (REPO / a.dist)
    EXE = DIST / "edi_mcp_server.exe"
    py = Path(sys.executable)

    say("=" * 74)
    say("Win7 exe 打包（PythonWin7 3.10 + 冻结依赖）")
    say("仓库: %s\n解释器: %s" % (REPO, py))
    say("=" * 74)

    if sys.version_info[:2] != (3, 10):
        say("")
        say("[FAIL] 必须用 Python 3.10 打包（当前 %d.%d）—— 3.12 打出来的 exe 在 Win7 上必崩。" % sys.version_info[:2])
        say("       请改用: .venv-win7\\Scripts\\python.exe scripts\\build_win7_exe.py ...")
        return 1

    ok = True
    if not a.no_build:
        ok = do_build(py)
    elif not EXE.exists():
        ok = step("已有产物 %s" % EXE.name, False, "--no-build 但 dist 里没有 exe")
    if ok:
        ok = write_env(a.port)
    if ok:
        ok = copy_launcher()
    if ok:
        ok = check_dist(py)
    if ok and a.smoke:
        ok = smoke(a.port)

    say("\n" + "=" * 74)
    say("结果汇总")
    for name, good, _ in results:
        say("  %-4s %s" % ("PASS" if good else "FAIL", name))
    failed = [n for n, good, _ in results if not good]
    say("\n%s（%d 项，失败 %d）" % ("全部通过" if not failed else "有失败", len(results), len(failed)))
    if ok:
        say("产物: %s" % DIST)
        say("上机: 整个目录拷到 Win7 → 跑 start_server.bat（或 edi_mcp_server.exe --port %d）" % a.port)
    say("BUILD_RESULT steps=%d failed=%d" % (len(results), len(failed)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
