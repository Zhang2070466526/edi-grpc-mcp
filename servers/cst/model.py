"""CST 模型级只读工具 —— 体检（info）与解包（open）。

两件事都交给 CST 自带解释器的独立进程执行（见 cst_worker.py），并通过 cst_runner
串行执行，避免与求解/导出会话并发。都是同步调用（阻塞到完成），秒级返回。
"""

from __future__ import annotations

import os
from typing import Any

from servers import mcp
from servers.cst.cst_api import cst_runner, run_cst_job
from servers.utils import error_response, require_file


class CstModelOps:
    """CST 模型级操作 —— 体检与解包，均为同步串行作业。"""

    def info(self, model_path: str) -> dict[str, Any]:
        return run_cst_job("info", model_path=os.path.abspath(str(model_path)))

    def open_project(self, model_path: str) -> dict[str, Any]:
        return run_cst_job("open", model_path=os.path.abspath(str(model_path)))


_model_ops = CstModelOps()


@mcp.tool()
def cst_get_model_info(model_path: str) -> dict[str, Any]:
    """只读查看 CST 模型状态：是否已解、是否解包、端口数、远场监视器有无。

    用法："这个 CST 模型里有什么"、"它有没有远场结果"、"有没有配 farfield 监视器"

    不解算、不开 GUI 会话，秒级返回。适合在求解/导出前先确认模型状态，避免盲跑。

    注意：归档（未解包）的 .cst 读不了结果树，会返回 unpacked=false 并带原因原文，
    可先用 cst_open_project 解包。monitors 是从**结果树**推断的（判据：Farfield 分支
    / Gain* 模式表 / 效率表）；结果树里看不到远场信号时 monitors 为空并在 notes 说明。

    Args:
        model_path: .cst 模型文件绝对路径。

    Returns:
        {"success": True, "solved": bool, "unpacked": bool, "ports": int,
         "freq_range": [min, max] | None, "monitors": ["Farfield"], "result_items": [...],
         "notes": "..."}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    try:
        info = cst_runner.run_sync(
            _model_ops.info, resolved,
            metadata={"op": "info", "model_path": resolved},
            timeout=120,
        )
    except Exception as exc:
        return error_response("CST_INFO_FAILED", str(exc))
    return {"success": True, **info}


@mcp.tool()
def cst_open_project(model_path: str) -> dict[str, Any]:
    """打开并保存 CST 模型（= 解包归档文件），不求解。

    用法："把这个归档模型解包"

    归档的 .cst 必须先开一次 CST 才能解包变成可读，否则只能靠求解顺带解包。
    本工具只 open + save，秒级，不跑求解。

    Args:
        model_path: .cst 模型文件绝对路径（归档文件也行）。

    Returns:
        {"success": True, "model_path": "...", "unpacked": True, "message": "..."}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    try:
        result = cst_runner.run_sync(
            _model_ops.open_project, resolved,
            metadata={"op": "open", "model_path": resolved},
            timeout=300,
        )
    except Exception as exc:
        return error_response("CST_OPEN_FAILED", str(exc))
    return {"success": True, **result, "message": "模型已打开并保存（解包完成）"}


@mcp.tool()
def cst_list_results(model_path: str) -> dict[str, Any]:
    """列出 CST 模型结果树里的全部条目（S 参数 / 远场等）与监视器。

    用法："这个模型有哪些结果"、"有没有远场结果"

    只读，不解算、不开 GUI 会话，秒级返回。与 cst_get_model_info 共用读取层，
    但只回结果清单（不含 solved/ports/freq_range 等体检字段）。

    注意：归档（未解包）的 .cst 读不了结果树，会返回空清单并带原因原文，
    可先用 cst_open_project 解包。

    Args:
        model_path: .cst 模型文件绝对路径。

    Returns:
        {"success": True, "result_items": [...], "monitors": ["Farfield"], "notes": "..."}
    """
    resolved, err = require_file(model_path, (".cst",))
    if err:
        return err
    try:
        info = cst_runner.run_sync(
            _model_ops.info, resolved,
            metadata={"op": "info", "model_path": resolved},
            timeout=120,
        )
    except Exception as exc:
        return error_response("CST_INFO_FAILED", str(exc))
    return {
        "success": True,
        "result_items": info["result_items"],
        "monitors": info["monitors"],
        "notes": info.get("notes", ""),
    }
