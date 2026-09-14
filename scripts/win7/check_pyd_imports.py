#!/usr/bin/env python
"""定位 Windows 扩展模块 (.pyd/.dll) 加载失败的真实原因。

用法（在被检查的那个 Python 环境里跑，位数必须一致）:
    python check_pyd_imports.py                 # 全栈静态体检：扫 site-packages 全部 .pyd，
                                               #   只打印命中 Win7 红线的 + 一句话汇总（零误报）
    python check_pyd_imports.py 某扩展.pyd       # 定点取证：带加载/符号探测，打印[缺符号]与各依赖版本

    为什么不提供「批量加载探测」：LoadLibrary 会执行每个扩展的 DllMain，对整棵 site-packages
    批量做实测会以 "Fatal Python error: PyThreadState_Get ... the GIL is released (finalizing)"
    崩溃退出（rc=0xC0000409）。加载探测只对明确指定的单个 .pyd 做。

为什么需要它:
    "ImportError: DLL load failed while importing _pydantic_core: 找不到指定的程序"
    == WinError 127 (ERROR_PROC_NOT_FOUND)。含义不是「缺 DLL」，而是
       **DLL 找到了、依赖 DLL 也找到了，但某个依赖 DLL 里没有导出所需函数**
       —— 典型原因就是依赖的运行时 DLL 版本过旧（如 vcruntime140.dll 是 VS2015 的 14.0，
       而扩展由 VS2022 编译，要求 14.21+ 才导出的符号）。
    本脚本解析目标 PE 的导入表：静态部分判断「导入了 Win7 根本没有的 Win8+/Win10 符号」
    （这条在 Win7 上必然 WinError 127），定点模式再逐个 LoadLibrary + GetProcAddress
    打出「哪个 DLL 缺哪个符号」，并给出实际加载到的 DLL 版本号与路径。
"""
from __future__ import annotations

import ctypes
import glob
import os
import struct
import sys
import sysconfig
from ctypes import wintypes

# Win7 是 GBK 控制台：Dingbats / Emoji 之类的字符（U+2713 对勾、U+2717 叉、U+2705 等）
# 不在 GBK 里，print 会直接抛 UnicodeEncodeError 把现场流程崩掉。
# 策略：① 输出只用 GBK 安全字符（本文件全文件已按此约束写）；
#       ② 这里再加一层网，遇不可编码字符降级成 '?'，绝不让诊断脚本自己先挂。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

MACHINES = {0x014C: "x86(32位)", 0x8664: "x64(64位)", 0xAA64: "ARM64"}
LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008

# Win7 跑不了 / 属于 Win8+ 的导入（在 Win10 上能加载，但 Win7 上必然 WinError 127）
WIN7_REDLINE = {
    "processprng": "bcryptprimitives!ProcessPrng —— Rust ≥1.78 的 std 引入，Windows 10 1809+ 才有",
    "waitonaddress": "Win8+ (api-ms-win-core-synch-l1-2-0)，Win7 无此 API set",
    "wakebyaddressall": "Win8+ (api-ms-win-core-synch-l1-2-0)",
    "wakebyaddresssingle": "Win8+ (api-ms-win-core-synch-l1-2-0)",
    "getsystemtimepreciseasfiletime": "Win8+，Win7 的 kernel32 未导出",
    "setthreaddescription": "Windows 10 1607+",
    "getthreaddescription": "Windows 10 1607+",
    "discardvirtualmemory": "Win8.1+",
    "getcurrentpackageid": "Win8+",
    "prefetchvirtualmemory": "Win8+",
}
# Win7 上「存在且够用」的 API set（除去这些，其余 api-ms-win-core-* 都是 Win8+ 引入）
WIN7_OK_APISETS = {
    "api-ms-win-crt-", "api-ms-win-core-path-l1-1-0.dll",
    "api-ms-win-core-file-l1-1-0.dll", "api-ms-win-core-handle-l1-1-0.dll",
    "api-ms-win-core-synch-l1-1-0.dll", "api-ms-win-core-libraryloader-l1-1-0.dll",
    "api-ms-win-core-errorhandling-l1-1-0.dll", "api-ms-win-core-heap-l1-1-0.dll",
    "api-ms-win-core-interlocked-l1-1-0.dll", "api-ms-win-core-memory-l1-1-0.dll",
    "api-ms-win-core-processenvironment-l1-1-0.dll", "api-ms-win-core-processthreads-l1-1-0.dll",
    "api-ms-win-core-string-l1-1-0.dll", "api-ms-win-core-sysinfo-l1-1-0.dll",
    "api-ms-win-core-debug-l1-1-0.dll", "api-ms-win-core-rtlsupport-l1-1-0.dll",
    "api-ms-win-core-localization-l1-2-0.dll", "api-ms-win-core-console-l1-1-0.dll",
    "api-ms-win-core-profile-l1-1-0.dll", "api-ms-win-core-util-l1-1-0.dll",
}


def win7_risks(deps):
    """从导入表里挑出 Win7 上会炸的项，返回 [(dll, 符号或 None, 说明)]"""
    out = []
    for dll, syms in deps:
        low = dll.lower()
        for s in syms:
            key = s.lstrip("_").lower()
            if key in WIN7_REDLINE:
                out.append((dll, s, WIN7_REDLINE[key]))
        if low.startswith("api-ms-win-") and not any(
                low.startswith(ok) for ok in WIN7_OK_APISETS):
            out.append((dll, None, "Win7 不存在这个 API set（Win8+ 才引入）"))
    return out

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.LoadLibraryExW.restype = ctypes.c_void_p
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD]
k32.LoadLibraryW.restype = ctypes.c_void_p
k32.LoadLibraryW.argtypes = [wintypes.LPCWSTR]
k32.GetModuleHandleW.restype = ctypes.c_void_p
k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
k32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
k32.GetProcAddress.restype = ctypes.c_void_p
k32.GetProcAddress.argtypes = [ctypes.c_void_p, wintypes.LPCSTR]


def mod_path(hm) -> str:
    buf = ctypes.create_unicode_buffer(32768)
    if k32.GetModuleFileNameW(hm, buf, 32768):
        return buf.value
    return "?"


def file_version(path: str) -> str:
    try:
        ver = ctypes.WinDLL("version")
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return "-"
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, ctypes.cast(buf, ctypes.c_void_p)):
            return "-"
        ptr, ln = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(ctypes.cast(buf, ctypes.c_void_p), "\\",
                                 ctypes.byref(ptr), ctypes.byref(ln)):
            return "-"

        class VS_FIXEDFILEINFO(ctypes.Structure):
            _fields_ = [("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
                        ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
                        ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
                        ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
                        ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD),
                        ("dwFileSubtype", wintypes.DWORD), ("dwFileDateMS", wintypes.DWORD),
                        ("dwFileDateLS", wintypes.DWORD)]

        fi = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception as exc:  # pragma: no cover
        return "<ver err: %s>" % exc


# ---------------------------------------------------------------- PE 解析
def _sections(data: bytes):
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("不是有效的 PE 文件")
    coff = e_lfanew + 4
    machine, nsec = struct.unpack_from("<HH", data, coff)
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    pe32p = magic == 0x20B
    dd_off = opt + (112 if pe32p else 96)
    sections = []
    for i in range(nsec):
        off = opt + opt_size + i * 40
        name = data[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((name, va, vsize, rawptr, rawsize))
    return machine, pe32p, dd_off, sections


def _rva2off(sections, rva: int):
    for _n, va, vsize, rawptr, rawsize in sections:
        if va <= rva < va + max(vsize, rawsize):
            return rawptr + (rva - va)
    return None


def parse_imports(path: str):
    """返回 (machine, [(dllname, [符号名...]) ...])"""
    with open(path, "rb") as fh:
        data = fh.read()
    machine, pe32p, dd_off, sections = _sections(data)
    imp_rva, imp_size = struct.unpack_from("<II", data, dd_off + 8)  # dir[1] = import
    if not imp_rva:
        return machine, []
    off = _rva2off(sections, imp_rva)
    out, seen = [], set()
    while True:
        oft, _ts, _fc, name_rva, ft = struct.unpack_from("<IIIII", data, off)
        if not (oft or name_rva or ft):
            break
        off += 20
        no = _rva2off(sections, name_rva)
        dll = data[no:data.index(b"\0", no)].decode("ascii", "replace") if no is not None else "?"
        if dll in seen:
            continue
        seen.add(dll)
        syms, thunk = [], _rva2off(sections, oft or ft)
        step = 8 if pe32p else 4
        fmt = "<Q" if pe32p else "<I"
        hi = 1 << 63 if pe32p else 1 << 31
        while True:
            val = struct.unpack_from(fmt, data, thunk)[0]
            if not val:
                break
            if not (val & hi):
                so = _rva2off(sections, val)
                syms.append(data[so + 2:data.index(b"\0", so + 2)].decode("ascii", "replace"))
            else:  # 按序号导入
                syms.append("#%d" % (val & 0xFFFF))
            thunk += step
        out.append((dll, syms))
    return machine, out


# ---------------------------------------------------------------- 主流程
def site_packages() -> list[str]:
    paths = {sysconfig.get_paths().get("platlib"), sysconfig.get_paths().get("purelib")}
    return [d for d in paths if d and os.path.isdir(d)]


def all_extensions() -> list[str]:
    """site-packages 下全部 .pyd —— 全栈体检，不挑包（早期版本用通配符，漏了 cygrpc 等）。"""
    out = []
    for d in site_packages():
        out += glob.glob(os.path.join(d, "**", "*.pyd"), recursive=True)
    return sorted(set(out))


def bulk() -> int:
    """全栈静态体检：只打印命中红线的扩展 + 一句话汇总（现场出结论用这个）。"""
    files = all_extensions()
    hits = []
    for f in files:
        try:
            risks = win7_risks(parse_imports(f)[1])
        except Exception as exc:
            print("  !! 解析失败 %s: %s" % (f, exc))
            continue
        if risks:
            hits.append(f)
            print("-" * 78)
            print("目标: %s" % f)
            _print_risks(risks)
    print("=" * 78)
    print("静态体检: 扫描 %d 个扩展，%d 个命中 Win7 红线" % (len(files), len(hits)))
    # 机器可读契约行（纯 ASCII）：给调用方解析用。别让调用方去 match 中文——
    # 控制台编码与解码不一致时会变成乱码，进而误判成失败。
    print("SCAN_RESULT files=%d hits=%d" % (len(files), len(hits)))
    if hits:
        print("这些在 Win7 上必崩（换版本或别装）:")
        for f in hits:
            print("  - %s" % f)
        return 1
    print("[OK] 无已知的 Win8+/Win10 专用导入 —— 可进入现场加载实证"
          "（指定具体 .pyd 跑本脚本即可）")
    return 0


def _print_risks(risks) -> None:
    if risks:
        print("\n  [!] Win7 红线导入（在 Win10/11 上能加载，Win7 上必然 WinError 127 找不到指定的程序）：")
        for dll, sym, why in risks:
            print("      - %-34s %-28s %s" % (dll, sym or "(整个 API set)", why))
    else:
        print("\n  [OK] 静态扫描未发现已知的 Win8+/Win10 专用导入")


def check(target: str, load: bool = True) -> int:
    print("=" * 78)
    print("目标: %s" % target)
    if not os.path.exists(target):
        print("  !! 文件不存在")
        return 1
    machine, imports = parse_imports(target)
    bits = 64 if sys.maxsize > 2 ** 32 else 32
    print("  PE 机器码: %s ; 当前解释器: %s %s (%d位)"
          % (MACHINES.get(machine, hex(machine)), sys.version.split()[0],
             sys.executable, bits))
    if (machine == 0x8664) != (bits == 64):
        print("  !! 位数不匹配：这个扩展无法被当前解释器加载，请换同位数 Python 重跑本脚本")

    risks = win7_risks(imports)

    # bulk 模式（默认）：只做静态判定。逐个 .pyd 真去加载会误报——例如 pywin32 的
    # pywintypes310.dll 在 site-packages\pywin32_system32 下，不在加载器的默认搜索路径里，
    # 直接 LoadLibrary 会报「缺 DLL」，但 Python 里 import win32com 完全正常。
    if not load:
        print("  静态导入: %d 个 DLL / %d 个符号（未做加载探测）"
              % (len(imports), sum(len(s) for _, s in imports)))
        _print_risks(risks)
        print("\n  结论: " + ("%d 项 Win7 红线 —— Win7 上必崩" % len(risks) if risks
                            else "静态扫描干净（现场加载实证请加 --load 或直接指定该 .pyd）"))
        return 1 if risks else 0

    # 先按 Python 的方式加载目标本身（CPython 用的是 LOAD_WITH_ALTERED_SEARCH_PATH）
    hm = k32.LoadLibraryExW(target, None, LOAD_WITH_ALTERED_SEARCH_PATH)
    if hm:
        print("  加载目标本身: OK  (%s)" % mod_path(hm))
    else:
        err = ctypes.get_last_error()
        print("  加载目标本身: 失败  WinError %d = %s" % (err, ctypes.FormatError(err)))

    _print_risks(risks)

    bad = 0        # 只统计「符号缺失」——这正是 WinError 127 的成因
    warn = 0       # 依赖 DLL 找不到（WinError 126）：打印出来供人工判断，不计入退出码
    print("\n  导入依赖 %d 个 DLL：" % len(imports))
    for dll, syms in imports:
        dm = k32.GetModuleHandleW(dll) or k32.LoadLibraryExW(dll, None, LOAD_WITH_ALTERED_SEARCH_PATH)
        if not dm:
            err = ctypes.get_last_error()
            print("  [缺 DLL]  %-28s %s（若该 DLL 由相邻目录提供，属正常）"
                  % (dll, ctypes.FormatError(err)))
            warn += 1
            continue
        p = mod_path(dm)
        missing = [s for s in syms if not s.startswith("#") and not k32.GetProcAddress(dm, s.encode())]
        tag = "[缺符号]" if missing else "[ OK ]"
        print("  %s  %-28s %-16s %s" % (tag, dll, file_version(p), p))
        for s in missing:
            print("            - 未导出: %s" % s)
        bad += len(missing)

    print("\n  结论: ", end="")
    if bad:
        print("%d 处缺符号 —— 上面 [缺符号] 的就是根因；带版本号的 DLL 若版本明显偏旧，就是它。" % bad)
    elif warn:
        print("无缺符号；另有 %d 个依赖 DLL 未解析（多为相邻目录提供，属正常）。" % warn)
    else:
        print("全部导入可解析。若目标仍加载失败，问题在延迟导入/CRT 版本兼容或依赖的 C++ 运行时行为层面。")
    return 1 if (bad or risks) else 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--load"]
    if "--load" in sys.argv[1:] and not args:
        # 刻意不支持批量加载探测：LoadLibrary 会执行每个扩展的 DllMain，对整棵 site-packages
        # 批量做实测会以 "Fatal Python error: PyThreadState_Get ... finalizing" 崩溃（rc=0xC0000409）。
        print("--load 必须配合具体 .pyd 使用（加载探测会执行该扩展的 DllMain，不能批量做）。")
        print("用法: python %s [某个 .pyd ...]" % os.path.basename(__file__))
        return 2
    if args:
        # 定点取证：逐文件详报（加载/符号探测 + 各依赖 DLL 版本）
        rc = 0
        for t in args:
            rc |= check(t, load=True)
        print("\n提示: 同样方法检查 python.exe 同目录下的 vcruntime140.dll / vcruntime140_1.dll / "
              "msvcp140.dll 版本；Win7 上 VS2015 的 14.0 版 vcruntime140.dll 无法满足 VS2022 编译的扩展。")
        return rc
    return bulk()


if __name__ == "__main__":
    sys.exit(main())
