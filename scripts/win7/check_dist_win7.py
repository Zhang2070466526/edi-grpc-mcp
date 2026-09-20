#!/usr/bin/env python
"""打包产物体检 —— 判断 dist 目录打出来的东西能不能在 Win7 跑（不用把 exe 搬过去试）。

用法：
    python scripts/win7/check_dist_win7.py [dist 目录，默认 dist/edi-mcp]
    python scripts/win7/check_dist_win7.py --dir dist/edi-mcp

查三件事（对应现场三类报错）：
  1. 每个 PE 文件（bootloader 本体 + 所有 .pyd/.dll）是否导入了 Win8+/Win10 才有的符号
     → 命中 = Win7 上必崩，报 "DLL load failed ... 找不到指定的程序"(WinError 127)
  2. CRT 运行时（vcruntime140 / vcruntime140_1 / msvcp140*）是否随包自带
     → 缺失 = Win7 上报 "找不到指定的模块"(WinError 126)，需装 VC++ 2015-2022 x64
  3. api-ms-win-crt-* 由目标机 UCRT 提供（Win7 需 KB3118401）—— 本脚本无法代验，只列出
  4. 在 Win7 上打包时 PyInstaller 会把**本机 UCRT**（`_internal/ucrtbase.dll`）一起收进包：
     它与本机 System32 那份逐字节相同时**降级为提示**（Win7 的 UCRT 本就由 KB3118401 提供，
     且本机 Python 此刻就在用同一文件）→ 2026-09-14 现场实测为误报；只有"外来版本"才计入红线

输出末行是纯 ASCII 契约行，便于脚本/CI 解析：
    DIST_RESULT files=N redlines=M crt_missing=K
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent   # scripts/win7/ -> 仓库根
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_pyd_imports import parse_imports, win7_risks  # noqa: E402

# Win7 上由 UCRT(KB3118401) 提供，不该指望随包自带
UCRT_PREFIX = "api-ms-win-crt-"
CRT_PREFIXES = ("vcruntime", "msvcp", "concrt", "ucrtbase")
UCRT_DLL = "ucrtbase.dll"


def _md5(path) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_local_system_ucrt(path) -> bool:
    """包内 ucrtbase.dll 是否就是本机 System32 那份（逐字节相同）。

    相同 = 目标机上当然能加载（本机 Python 此刻正在用它）→ 不算红线，降级为提示。
    不同 = 包里带的是外来 UCRT 版本，仍按红线报（老系统可能加载不了）。
    """
    if os.path.basename(path).lower() != UCRT_DLL:
        return False
    ref = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / UCRT_DLL
    try:
        return ref.is_file() and _md5(path) == _md5(ref)
    except Exception:
        return False

try:  # 现场是 GBK 控制台：不可编码字符降级，别崩在 print 上
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass


def main() -> int:
    ap = argparse.ArgumentParser(description="dist 产物的 Win7 兼容性体检")
    ap.add_argument("--dir", default=str(REPO / "dist" / "edi-mcp"), help="dist 目录")
    a = ap.parse_args()
    root = Path(a.dir)
    if not root.is_dir():
        print("目录不存在: %s（先跑 pyinstaller 打包）" % root)
        return 2

    files = []
    for ext in ("*.exe", "*.dll", "*.pyd"):
        files += glob.glob(str(root / "**" / ext), recursive=True)
    files = sorted(set(files))
    print("体检目录: %s" % root)
    print("PE 文件: %d 个（exe/dll/pyd）\n" % len(files))

    hits, noted, needed = [], [], set()
    for f in files:
        try:
            _machine, imports = parse_imports(f)
        except Exception as exc:
            print("  !! 解析失败 %s: %s" % (os.path.basename(f), exc))
            continue
        risks = win7_risks(imports)
        if risks:
            why = sorted({"{}!{}".format(d.split('.')[0], s or "APIset") for d, s, _ in risks})
            (noted if _is_local_system_ucrt(f) else hits).append((os.path.relpath(f, root), why))
        for dll, _syms in imports:
            low = dll.lower()
            if low.startswith(CRT_PREFIXES) or low.startswith(UCRT_PREFIX):
                needed.add(low)

    present = {os.path.basename(f).lower() for f in files}
    crt_present = sorted(x for x in present if x.startswith(CRT_PREFIXES))
    crt_missing = sorted(x for x in needed - present if not x.startswith(UCRT_PREFIX))
    ucrt_needed = sorted(x for x in needed if x.startswith(UCRT_PREFIX))

    if hits:
        print("[!] Win7 红线命中（Win7 上必崩，WinError 127 找不到指定的程序）:")
        for path, why in hits:
            print("    %s\n        %s" % (path, "; ".join(why)))
    else:
        print("[OK] %d 个 PE 文件 0 命中 Win7 红线（含 bootloader 本体）" % len(files))

    if noted:
        print("[i] 包内 UCRT 与本机 System32 同源，降级为提示（不计红线）:")
        for path, why in noted:
            print("    %s（%d 个 API set 导入，与本机同源故可加载）" % (path, len(why)))
        print("    理由: Win7 的 UCRT 由系统 KB3118401 提供；本机 Python 此刻就在用同一文件")

    print("\nCRT 运行时:")
    print("  dist 自带: %s" % (crt_present or "（无）"))
    print("  仍需系统提供: %s" % (crt_missing or "（无）"))
    print("  UCRT API set（Win7 需 KB3118401，脚本无法代验）: %d 个"
          % len(ucrt_needed))
    if crt_missing:
        print("  -> 缺 CRT：Win7 上装 VC++ 2015-2022 x64 运行库，或把它们补进 spec 的 binaries=[]")

    exe = root / "edi_mcp_server.exe"
    if exe.exists():
        total = sum(os.path.getsize(p) for p in glob.glob(str(root / "**" / "*"), recursive=True)
                    if os.path.isfile(p))
        print("\n体积: exe %.1f MB / 目录 %.1f MB" % (os.path.getsize(exe) / 1048576, total / 1048576))

    print("\nDIST_RESULT files=%d redlines=%d crt_missing=%d" % (len(files), len(hits), len(crt_missing)))
    print("结论: %s" % ("可进入 Win7 实机验证" if not hits and not crt_missing
                      else "先修上面的问题再上 Win7"))
    return 1 if (hits or crt_missing) else 0


if __name__ == "__main__":
    sys.exit(main())
