r"""EDA 分析工具 — 网表导出 + 原理图截图 + 器件 CSV 导出（gRPC 调用）。

export_project_netlist  查看/导出 .epp 工程的网表文件
capture_schematic       截取工程原理图并保存为图片 返回 artifacts（含 build_file_link）
export_schematic_components_to_csv  导出原理图有效器件为 CSV（供模型替换）

自然语言使用示例：
  帮我查看 EDA 工程 C:\...\EDI_TEST.epp 的网表
  帮我截取这个工程的原理图，保存到 C:\screenshots\circuit.png
  帮我导出这个工程的网表，超时设为 120 秒
  把这个工程的器件信息导出成 CSV

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  output_path      截图输出路径，支持 PNG/JPG 等（capture_schematic）
  csv_path         CSV 输出路径（export_schematic_components_to_csv）
  timeout_seconds  最长等待秒数，无上限，默认 60 秒
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_project_grpc
from servers.utils import build_artifact, build_file_link, error_response, require_nonempty
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
    result = call_project_grpc(ecserver_pb2.VIEW_PROJECT_NETLIST, project_path, timeout_seconds)
    if result.get("success"):
        result["message"] = "网表导出成功"
    return result


@mcp.tool()
def capture_schematic(
    project_path: str,
    output_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """截取 EDA 工程原理图为图片。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        output_path: 输出图片路径，支持 PNG/JPG 等。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    # Basic path validation: resolve and check output extension
    img_resolved = str(Path(output_path).expanduser().resolve())
    img_ext = Path(img_resolved).suffix.lower()
    if img_ext not in (".png", ".jpg", ".jpeg", ".bmp", ".svg"):
        return error_response("INVALID_PATH", f"output_path 扩展名不支持: {img_ext}，请使用 PNG/JPG/BMP/SVG")

    result = call_project_grpc(ecserver_pb2.CAPTURE_SCHEMATIC, project_path, timeout_seconds,
                               img_path=img_resolved)
    img_ok = Path(img_resolved).is_file()
    if result.get("success") and img_ok:
        result["img_generated"] = True
        result["artifacts"] = [build_artifact("image", img_resolved, "capture_schematic")]
        result["message"] = "原理图已截图。"
        result.update(build_file_link(img_resolved, "打开原理图"))
    return result


@mcp.tool()
def export_schematic_components_to_csv(
    project_path: str,
    csv_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """将工程原理图中的有效器件信息导出为 CSV。

    用法："把这个工程的器件信息导出成 CSV"

    导出列为 original_model_type/name/id 与 alternative_model_type/name/id（后三列
    留空），供 replace_models_from_csv 后续模型替换填写。仿真控制器、端口、变量等
    EXCLUDED_TYPES 不导出。csv_path 未以 .csv 结尾时服务端自动追加后缀；父目录
    必须已存在；同名文件直接覆盖。

    Args:
        project_path: .epp 工程文件绝对路径。
        csv_path: CSV 输出文件路径（非空，父目录必须已存在）。
        timeout_seconds: 最长等待秒数，默认 60。

    Returns:
        gRPC 统一返回结构，成功时 details 含 csv_path（最终绝对路径）。
    """
    csv_path, err = require_nonempty(csv_path, label="csv_path")
    if err:
        return err
    return call_project_grpc(
        ecserver_pb2.EXPORT_SCHEMATIC_COMPONENTS_TO_CSV,
        project_path,
        timeout_seconds,
        save_path=csv_path,
    )
