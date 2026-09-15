"""Turbocharts RAW 转图 Resource —— 把引擎自带的使用说明注入给上层 Agent。

  edi://reference/turbocharts-guide — 《RAW 转图像工具使用说明》原文（画图前先读）

说明文档就是 Turbocharts 引擎自带的 `RAW 转图像工具使用说明.txt`，本资源**直接读取原文件**
（保持单一事实源）；文件缺失时返回一份硬编码的兜底摘要，引擎升级后需同步该摘要。
"""

from __future__ import annotations

from pathlib import Path

from servers import mcp

GUIDE_FILENAME = "RAW 转图像工具使用说明.txt"


def guide_path() -> Path:
    """说明文件路径（与 turbocharts_app.exe 的说明同目录存放）。"""
    return Path(__file__).with_name(GUIDE_FILENAME)


@mcp.resource(
    "edi://reference/turbocharts-guide",
    name="Turbocharts RAW Guide",
    title="Turbocharts RAW 转图像说明",
    description="引擎自带《RAW 转图像工具使用说明》原文：命令行参数（--raw/--img/--type/--csv/"
                "--linename/--dependcy/--ac）、--linename 的单位与线段名写法（db、phase、real、"
                "vswr、aps、af、时延 delays）、各使用示例。调用 turbocharts_convert 前读本资源。",
    mime_type="text/plain",
)
def resource_turbocharts_guide() -> str:
    """返回说明文件原文；文件缺失时返回可操作的提示而不是抛异常。"""
    path = guide_path()
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        return (
            f"# 无法读取引擎自带说明\n\n"
            f"预期路径: {path}\n"
            f"错误: {exc}\n\n"
            "turbocharts_convert 的入参对应引擎命令行参数 "
            "(--raw/--img/--type/--csv/--linename/--dependcy/--ac)；"
            "linename 为「单位_线段名」，单位常用 db、phase、real(实数)，另有 vswr、aps、af，"
            "时延线段名为 delays[i,j]，多条用 & 分隔。"
        )
