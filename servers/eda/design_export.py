r"""EDA 分析工具 — 网表导出 + 原理图截图（gRPC 调用）。

capture_schematic  截取工程原理图并保存为图片 返回 artifacts（含 build_file_link），图片生成后自动添加 file:// 链接。
export_project_netlist  查看/导出 .epp 工程的网表文件

自然语言使用示例：
  帮我查看 EDA 工程 C:\...\EDI_TEST.epp 的网表
  帮我截取这个工程的原理图，保存到 C:\screenshots\circuit.png
  帮我导出这个工程的网表，超时设为 120 秒

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  img_path         截图输出路径，支持 PNG/JPG 等（capture_schematic）
  timeout_seconds  最长等待秒数，无上限，默认 60 秒
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_project_grpc
from servers.utils import build_file_link, error_response
from servers import mcp


@mcp.tool()
def export_project_netlist(
    project_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """查看 EDA .epp 工程的网表，返回网表文件路径。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    return call_project_grpc(ecserver_pb2.VIEW_PROJECT_NETLIST, project_path, timeout_seconds)


@mcp.tool()
def capture_schematic(
    project_path: str,
    img_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """截取 EDA 工程原理图为图片。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        img_path: 输出图片路径，支持 PNG/JPG 等。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    # Basic path validation: resolve and check output extension
    img_resolved = str(Path(img_path).expanduser().resolve())
    img_ext = Path(img_resolved).suffix.lower()
    if img_ext not in (".png", ".jpg", ".jpeg", ".bmp", ".svg"):
        return error_response("INVALID_PATH", f"img_path 扩展名不支持: {img_ext}，请使用 PNG/JPG/BMP/SVG")

    result = call_project_grpc(ecserver_pb2.CAPTURE_SCHEMATIC, project_path, timeout_seconds,
                               img_path=img_resolved)
    img_ok = Path(img_resolved).is_file()
    if result.get("success") and img_ok:
        result["img_generated"] = True
        result["artifacts"] = [{"type": "image", "path": img_resolved,
                                "name": Path(img_resolved).name,
                                "generated_by": "capture_schematic"}]
        result["message"] = "原理图已截图。"
        result.update(build_file_link(img_resolved, "打开原理图"))
    return result
