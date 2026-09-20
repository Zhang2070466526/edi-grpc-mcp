#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Win7 现场诊断：包内 ucrtbase.dll 的归属 + exe 在真机上能否跑起来。

用途：打包体检报 "_internal\\ucrtbase.dll 命中 Win7 红线" 时定案。
      Win7 上 PyInstaller 会把本机 UCRT 一起收进包，红线可能是误报；
      本脚本比对 md5 取证，并实际起一次 exe 探 /ready。

用法（现场，单行）：
    .venv-win7\\Scripts\\python.exe scripts\\win7\\diag_win7_exe.py
    .venv-win7\\Scripts\\python.exe scripts\\win7\\diag_win7_exe.py --dist dist\\edi-mcp --port 50027

末行 ASCII 契约：DIAG_RESULT checks=N failed=M     rc=0 全过 / rc=1 有失败
输出只用 ASCII + GBK 可编码字符，禁用 U+2713/U+2717/U+2705/U+274C 这类符号（GBK 控制台会 UnicodeEncodeError）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

CHECKS = []
FAILS = []


def say(msg=""):
    print(msg, flush=True)


def step(name, ok, detail=""):
    CHECKS.append(name)
    if not ok:
        FAILS.append(name)
    say("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  -- " + detail) if detail else ""))
    return ok


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(port, exe, cwd, timeout):
    """起 exe 并轮询 /ready。返回 (状态, 说明)。"""
    proc = subprocess.Popen([str(exe), "--transport", "streamable-http", "--port", str(port)],
                            cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    body = ""
    tail = ""
    exited = False
    try:
        for _ in range(timeout):
            time.sleep(1)
            if proc.poll() is not None:
                exited = True
                tail = (proc.stdout.read() or "")[-300:].replace("\n", " | ")
                break
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/ready" % port, timeout=3) as r:
                    body = r.read().decode("utf-8", "replace")
                break
            except Exception:
                continue
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return exited, proc.returncode, tail, body


def main():
    ap = argparse.ArgumentParser(description="Win7 现场诊断：ucrtbase 归属 + exe 实跑")
    ap.add_argument("--dist", default="dist/edi-mcp", help="产物目录（默认 dist/edi-mcp）")
    ap.add_argument("--port", type=int, default=50027, help="冒烟端口，默认 50027（避开现场 50026）")
    ap.add_argument("--timeout", type=int, default=45, help="等待 /ready 的秒数（默认 45）")
    a = ap.parse_args()

    dist = Path(a.dist)
    exe = dist / "edi_mcp_server.exe"
    bundle_ucrt = dist / "_internal" / "ucrtbase.dll"
    sys_ucrt = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "ucrtbase.dll"

    say("=" * 74)
    say("Win7 现场诊断（Python %s / %s）" % (sys.version.split()[0], sys.platform))
    say("产物: %s" % dist.resolve())
    say("=" * 74)

    say("\n[1/5] 产物与解释器")
    step("产物目录存在", dist.is_dir(), str(dist.resolve()))
    has_exe = step("edi_mcp_server.exe 存在", exe.is_file(),
                   "%.1f MB" % (exe.stat().st_size / 1048576) if exe.is_file() else "缺失")

    say("\n[2/5] 包内 ucrtbase.dll 归属（与系统同源 = 红线属误报）")
    if not bundle_ucrt.is_file():
        step("包内 ucrtbase.dll", True, "包里没有该文件（UCRT 由系统提供，正常）")
    elif not sys_ucrt.is_file():
        step("包内 ucrtbase.dll", False, "系统 %s 不存在，无法比对" % sys_ucrt)
    else:
        hb, hs = md5_of(bundle_ucrt), md5_of(sys_ucrt)
        same = hb == hs
        step("包内 ucrtbase.dll 与系统同源", same,
             "bundle=%s system=%s %s" % (hb[:16], hs[:16],
                                         "一致（本机 UCRT，扫描红线=误报）" if same else "不一致（包带来的是别的版本）"))

    say("\n[3/5] 包内 CRT/UCRT 相关 DLL 清单（仅信息，不影响判定）")
    internal = dist / "_internal"
    if internal.is_dir():
        names = sorted(p.name for p in internal.glob("*.dll")
                       if p.name.lower().startswith(("api-ms-", "ucrt", "vcruntime", "msvcp")))
        say("  共 %d 个" % len(names))
        for n in names:
            say("    " + n)
    else:
        say("  没有 _internal 目录（一体式包？）")
    say("  [信息] 清单已列出")

    say("\n[4/5] 实机跑 exe：/ready 探活（端口 %d，最多 %d 秒）" % (a.port, a.timeout))
    if not has_exe:
        step("exe 启动 + /ready", False, "没有 exe，跳过")
    else:
        exited, rc, tail, body = probe(a.port, exe, dist.resolve(), a.timeout)
        if exited:
            step("exe 进程存活", False, "启动后退出 rc=%s %s" % (rc, tail))
        else:
            step("exe 启动 + /ready", bool(body),
                 body[:220] if body else "%d 秒内 /ready 无响应" % a.timeout)
            if body:
                try:
                    j = json.loads(body)
                except Exception:
                    j = {}
                step("status=ready", j.get("status") == "ready", "status=%s" % j.get("status"))
                step("tool_count>0", int(j.get("tool_count") or 0) > 0,
                     "tool_count=%s tools_hash=%s grpc=%s" % (j.get("tool_count"),
                                                              j.get("tools_hash"), j.get("grpc")))

    say("\n[5/5] 收尾")
    say("  [信息] 冒烟进程已结束，--port %d 已释放" % a.port)

    say("\n" + "=" * 74)
    if FAILS:
        say("有失败（%d 项）: %s" % (len(FAILS), " / ".join(FAILS)))
    else:
        say("全部通过（%d 项）" % len(CHECKS))
    say("DIAG_RESULT checks=%d failed=%d" % (len(CHECKS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
