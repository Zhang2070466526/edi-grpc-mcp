r"""drawtool.exe 包装 — XY 数据绘图（读 JSON 配置 → 画曲线图 → 存图片）。

命令（见 exe 同目录 xy_chart(drawtool)使用说明.md）：
    drawtool.exe <配置文件路径>
stdout 单行 UTF-8 JSON：{"status":"success","message":"绘图完成","image_path":"...","error_code":null}
退出码 0/1。输入是 JSON 配置文件（chart_title/series/marks/show_window/image_path/image_format），
输出图片文件名 = 配置文件名去掉 .json 再拼扩展名。

⚠️ exe 带管理员权限清单（requireAdministrator），启动时必须提权：服务本身以管理员运行时
Windows 不会再问；服务未提权时 Windows 直接拒绝启动（WinError 740，不弹 UAC），本工具转为
返回 EXE_NEEDS_ELEVATION + hint，不会抛原始异常。
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import DRAWTOOL_PATH, run_exe
from servers.utils import build_artifact, build_file_link, error_response, per_tool_mutex

_logger = logging.getLogger("simulation_tool.xy_chart")

_FORMATS = ("png", "jpg", "jpeg")

# Windows 文件名禁令：保留字符 + 控制字符 + 保留设备名 + 结尾空格/点。
# 配置文件名直接决定输出图片名，file_stem 过不了这关就必然在落盘时炸。
_WIN_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_WIN_BAD_FILENAME_CHARS = frozenset('<>:"|?*') | frozenset(chr(c) for c in range(32))


def _file_stem_error(stem: Any) -> str:
    """校验 file_stem 能安全当 Windows 文件名用；返回错误文案，空串=通过。"""
    if not isinstance(stem, str) or not stem.strip():
        return "file_stem 必须是非空文件名（不含路径分隔符）"
    if "/" in stem or "\\" in stem:
        return "file_stem 必须是非空文件名（不含路径分隔符）"
    bad = sorted({ch for ch in stem if ch in _WIN_BAD_FILENAME_CHARS})
    if bad:
        shown = " ".join(repr(ch) for ch in bad)
        return (f"file_stem 含 Windows 不允许的字符 {shown}"
                f"（禁止 < > : \" | ? * 与控制字符）")
    if stem != stem.rstrip(" ."):
        return "file_stem 不能以空格或点结尾（Windows 会静默截断，实际文件名不可预期）"
    if stem.split(".")[0].lower() in _WIN_RESERVED_NAMES:
        return "file_stem 不能使用 Windows 保留设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）"
    return ""


@mcp.tool()
@per_tool_mutex
def draw_chart(
    series: list[dict],
    image_path: str,
    chart_title: str = "",
    x_axis_title: str = "",
    y_axis_title: str = "",
    marks: list[dict] | None = None,
    file_stem: str = "chart",
    image_format: str = "png",
    show_window: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """把一组或多组 XY 数据绘制为曲线图并保存为图片（PNG/JPG，固定 1600×1000）。

    用法："把这三条增益曲线画成一张 PNG 存到 D:/plots"

    Args:
        series: 数据组数组（至少一组），每项 {name, points: [[x,y],...]}；
            name 缺失/为空则不显示图例，已提供的 name 必须唯一，points 为非空有限数值数组。
        image_path: 输出目录绝对路径（不存在时自动创建）。
        chart_title: 图表标题（空串=不显示）。
        x_axis_title: X 轴标题（空串=不显示）。
        y_axis_title: Y 轴标题（空串=不显示）。
        marks: 标记数组（可选），每项 {series, point: [x,y], label}；series/point 必须精确匹配已有曲线。
        file_stem: 输出文件名（不含扩展名），默认 "chart" → 输出 <image_path>/chart.png；
            不能含 <>:"|?* 或控制字符、不能以空格或点结尾、不能用 CON/PRN/AUX/NUL/COM1-9/LPT1-9
            （配置文件名直接决定输出图片名，过不了 Windows 文件名规则就是必然落盘失败）。
        image_format: png/jpg/jpeg，默认 png。
        show_window: 是否在图片落盘后弹窗显示（默认 False=后台，自动化场景不要开）。
        timeout_seconds: 最长等待秒数，默认 120。

    Returns:
        {"success": True, "image_path": "完整图片路径", "message": "...",
         "artifacts": [...], "links": {"image": {...}}}

        失败时 success=False + error_code：INVALID_PARAMETERS（series/image_path/image_format/
        file_stem 不合法）、EXE_NOT_FOUND（drawtool.exe 缺失）、EXE_NEEDS_ELEVATION（服务未以
        管理员身份运行，exe 清单要求提权，Windows 直接拒绝启动）、EXE_START_FAILED、
        TOOL_TIMEOUT、DRAW_FAILED（exe 自己报的错，原文在 message）。
    """
    # 1. series 非空数组、每项是对象
    if not isinstance(series, list) or not series:
        return error_response("INVALID_PARAMETERS", "series 必须是非空数组")
    if not all(isinstance(s, dict) for s in series):
        return error_response("INVALID_PARAMETERS", "series 每项必须是对象 {name, points}")

    # 2. image_path 非空、转绝对路径（相对路径按服务进程 cwd 解析，这里统一转绝对）
    if not isinstance(image_path, str) or not image_path.strip():
        return error_response("INVALID_PARAMETERS", "image_path 不能为空")
    out_dir = Path(image_path).expanduser().resolve()

    # 3. image_format 合法
    image_format = (image_format or "png").lower()
    if image_format not in _FORMATS:
        return error_response("INVALID_PARAMETERS",
                              f"image_format 必须是 {', '.join(_FORMATS)} 之一")

    # 4. file_stem 必须是能当 Windows 文件名用的字符串（它决定临时配置名→输出图片名）
    stem_error = _file_stem_error(file_stem)
    if stem_error:
        return error_response("INVALID_PARAMETERS", stem_error)

    # 5. 写 JSON 配置文件到临时目录（文件名 = file_stem.json，决定输出图片名）
    config = {
        "chart_title": chart_title,
        "x_axis_title": x_axis_title,
        "y_axis_title": y_axis_title,
        "series": series,
        "marks": marks or [],
        "show_window": bool(show_window),
        "image_path": str(out_dir),
        "image_format": image_format,
    }
    try:
        payload = json.dumps(config, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return error_response("INVALID_PARAMETERS", f"series/marks 含无法 JSON 序列化的值: {exc}")

    # 6. 写临时配置 → 跑 exe。tmp_dir 必须在 try 里创建、finally 里无条件清理，
    #    否则写配置失败（file_stem 含系统不认的字符）时会把空临时目录漏在 %TEMP%。
    tmp_dir = Path(tempfile.mkdtemp(prefix="edi_drawtool_"))
    try:
        tmp_json = tmp_dir / f"{file_stem}.json"
        try:
            tmp_json.write_text(payload, encoding="utf-8")
        except OSError as exc:
            return error_response(
                "INVALID_PARAMETERS", f"临时配置文件写入失败: {exc}",
                file_stem=file_stem,
                hint="file_stem 含系统不接受的字符时无法落盘，换成字母/数字/下划线")

        # 7. 运行 exe（位置参数传配置文件路径）
        try:
            run = run_exe(DRAWTOOL_PATH, [str(tmp_json)], timeout_seconds)
        except RuntimeError as exc:
            return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                                  hint="曲线较多时可调大 timeout_seconds 后重试")
        except ValueError as exc:
            return error_response("INVALID_PARAMETERS", str(exc))
        except FileNotFoundError as exc:
            return error_response("EXE_NOT_FOUND", str(exc), drawtool_path=DRAWTOOL_PATH,
                                  hint="确认 EDI 安装目录下 simulation_tool/simulation_report/drawtool.exe 存在")
        except OSError as exc:
            # WinError 740 = exe 清单要求提权，而当前服务进程未提权。
            # Windows 是直接拒绝启动（不弹 UAC），所以必须给结构化错误 + 可执行 hint。
            if getattr(exc, "winerror", None) == 740:
                return error_response(
                    "EXE_NEEDS_ELEVATION",
                    "drawtool.exe 的清单要求管理员权限，而当前 EDI MCP 服务进程不是以管理员身份运行的",
                    exe=DRAWTOOL_PATH, winerror=740, retryable=True,
                    hint=("以管理员身份重启 EDI MCP 服务后重试；"
                          "服务未提权时 Windows 直接拒绝启动 drawtool.exe，不会弹 UAC 让用户确认"))
            return error_response(
                "EXE_START_FAILED", f"无法启动 drawtool.exe: {exc}",
                exe=DRAWTOOL_PATH,
                hint="检查 exe 是否被占用或权限不足；若报 WinError 740 需管理员运行服务")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 7. 解析 stdout JSON
    data = run["json"]
    if data is None:
        return error_response(
            "DRAW_FAILED",
            run["json_error"] or "exe 未输出合法 JSON",
            stdout=run["stdout"], stderr=run["stderr"], return_code=run["returncode"],
        )
    if not isinstance(data, dict):
        return error_response("DRAW_FAILED", "exe 返回格式异常", stdout=run["stdout"])

    if run["returncode"] != 0 or data.get("status") != "success":
        return error_response(
            "DRAW_FAILED",
            data.get("message") or "绘图失败",
            hint="详见 message 里的 exe 原文；修正 series/marks 后重试",
            draw_errors=data, stdout=run["stdout"], stderr=run["stderr"],
            return_code=run["returncode"],
        )

    # 8. 成功：图片已落盘（exe 返回完整路径）
    img = data.get("image_path", "")
    artifacts: list[dict] = []
    if img and Path(img).is_file():
        artifacts.append(build_artifact("image", img, "draw_chart"))
    links: dict[str, dict] = {}
    if img:
        links["image"] = build_file_link(img, "打开图片", disposition="inline")

    return {
        "success": True,
        "image_path": img,
        "message": data.get("message") or "绘图完成",
        "artifacts": artifacts,
        "links": links,
    }
