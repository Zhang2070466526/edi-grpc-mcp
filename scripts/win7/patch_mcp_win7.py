#!/usr/bin/env python
"""Win7 部署补丁：修 mcp<=1.12.4 在「PEP 563 + Py3.10」项目上的工具注册崩溃。

问题（实测于本仓库 + PythonWin7 3.10 + mcp 1.12.4）：
    servers.registry_server 导入即崩：
        TypeError: issubclass() arg 1 must be a class
    根因在 mcp/server/fastmcp/tools/base.py 的 Tool.from_function：

        for param_name, param in sig.parameters.items():
            if get_origin(param.annotation) is not None: continue
            if issubclass(param.annotation, Context):    # ← 字符串注解送进 issubclass
                context_kwarg = param_name

    本仓库 49/59 个模块带 `from __future__ import annotations`，注解全是字符串：
    get_origin("某个类型") 返回 None → 落到 issubclass("某个类型", Context) → TypeError。
    mcp 1.14.1 才换成 find_context_parameter()，但 1.13.0 起 pydantic 下限抬到 >=2.11，
    而 pydantic 2.11 = pydantic-core>=2.33 = Rust 1.8x 编译（Win7 红线），
    所以 Win7 线上只能用 <=1.12.4，必须打这个补丁。

补丁内容（等价于上游 find_context_parameter）：
    先 get_type_hints() 解析字符串注解，再递归识别 Context（含 `Context | None`）；
    非类型注解一律跳过而不是喂给 issubclass。
    注意 get_origin 必须放在 isinstance 之前：Py3.9/3.10 的 isinstance(list[str], type)
    为 True，先判 isinstance 会把 GenericAlias 送进 issubclass 再次崩。

用法：
    python scripts/win7/patch_mcp_win7.py             # 打补丁（幂等）
    python scripts/win7/patch_mcp_win7.py --check     # 只检查，未打补丁返回 1
    python scripts/win7/patch_mcp_win7.py --file X.py # 对指定文件操作（自测/离线包用）
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

OLD = """        if context_kwarg is None:
            sig = inspect.signature(fn)
            for param_name, param in sig.parameters.items():
                if get_origin(param.annotation) is not None:
                    continue
                if issubclass(param.annotation, Context):
                    context_kwarg = param_name
                    break
"""

NEW = '''        if context_kwarg is None:
            sig = inspect.signature(fn)
            # ── Win7 补丁（上游 mcp>=1.14 find_context_parameter 的等价实现）──
            # 旧逻辑把 get_origin() 为 None 的注解直接喂给 issubclass()，遇到字符串
            # 注解（PEP 563）就抛 TypeError: issubclass() arg 1 must be a class。
            # 这里先用 get_type_hints 解析，再递归识别 Context（含 Context | None）。
            try:
                hints = get_type_hints(fn)
            except Exception:
                hints = {}

            def _is_context(annotation: Any) -> bool:
                # get_origin 必须先判：Py3.9/3.10 的 isinstance(list[str], type) 为 True，
                # 直接送进 issubclass 会被 ABCMeta 判为「不是类」而抛 TypeError。
                if get_origin(annotation) is not None:
                    return any(_is_context(arg) for arg in get_args(annotation))
                try:
                    return isinstance(annotation, type) and issubclass(annotation, Context)
                except TypeError:
                    return False

            for param_name, param in sig.parameters.items():
                if _is_context(hints.get(param_name, param.annotation)):
                    context_kwarg = param_name
                    break
'''

IMPORT_OLD = "from typing import TYPE_CHECKING, Any, get_origin\n"
IMPORT_NEW = "from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints\n"
MARKER = "def _is_context("


def target_file() -> Path:
    spec = importlib.util.find_spec("mcp")
    if spec is None or not spec.origin:
        sys.exit("错误：当前解释器里找不到 mcp，请在目标 venv 里运行本脚本。")
    path = Path(spec.origin).parent / "server" / "fastmcp" / "tools" / "base.py"
    if not path.exists():
        sys.exit("错误：找不到 %s（mcp 版本结构不符）" % path)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="给 mcp<=1.12.4 打 Win7/PEP563 补丁")
    ap.add_argument("--check", action="store_true", help="只检查是否已打补丁")
    ap.add_argument("--file", help="直接指定 base.py 路径（默认从已安装的 mcp 里找）")
    args = ap.parse_args()

    path = Path(args.file) if args.file else target_file()
    if not path.exists():
        sys.exit("错误：文件不存在 %s" % path)
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("[已打补丁] %s" % path)
        return 0
    if args.check:
        print("[未打补丁] %s —— 服务端会以 TypeError: issubclass() arg 1 must be a class 启动失败" % path)
        return 1
    if OLD not in text:
        sys.exit("错误：%s 里找不到待替换的旧代码块（mcp 版本可能不是 <=1.12.4，或已被其它补丁改过）。" % path)
    if IMPORT_OLD not in text:
        sys.exit("错误：%s 缺少预期的 typing import 行，拒绝改动。" % path)

    path.write_text(text.replace(IMPORT_OLD, IMPORT_NEW).replace(OLD, NEW), encoding="utf-8")
    print("[已打补丁] %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
