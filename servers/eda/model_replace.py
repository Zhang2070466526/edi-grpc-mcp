r"""EDA 模型替换工具 — 按 CSV 文件批量替换工程中的元件模型。

调用 gRPC MODEL_REPLACE 事件，将 CSV 中指定的旧模型替换为新模型。
"""

from __future__ import annotations

from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_project_grpc
from servers.utils import require_file
from servers import mcp


@mcp.tool()
def replace_schematic_components_from_csv(
    project_path: str,
    input_csv_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """⚠️ 按 CSV 批量替换工程原理图下的器件。

    用法："用这个 CSV 替换工程里的模型"、"批量更新器件型号"

    该 CSV 前三列 original_model_type、original_model_name、original_model_id 由
    export_schematic_components_to_csv 导出；后三列 alternative_model_type、
    alternative_model_name、alternative_model_id 在利用 search_personal_models、
    search_public_models 选择合适的替换模型后一一填写。alternative_model_type 对应
    两个 search 接口返回数据中的 model_name；alternative_model_name 可在 model_name
    上增加数字，要求整个 CSV 中唯一不重复；alternative_model_id 对应两个 search 接口
    返回数据中的 model_uuid。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        input_csv_path: 模型替换 CSV 文件绝对路径（输入，读取此 CSV）。
        timeout_seconds: 最长等待时间，默认 60 秒。

    Returns:
        gRPC 统一返回结构：{"success": True, "completed": True, "status": "SUCCEEDED", ...}
    """
    resolved_csv, err = require_file(input_csv_path, (".csv",))
    if err:
        return err
    return call_project_grpc(ecserver_pb2.MODEL_REPLACE, project_path, timeout_seconds,
                             csv_path=resolved_csv)
