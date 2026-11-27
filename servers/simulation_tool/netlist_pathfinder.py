r"""Netlist_Pathfinder.exe 包装 — 独立网表寻路（纯本地，不需 EDI/ADS/gRPC）。

输入 netlist.log + schematic.ep + 有向端口对，输出每个端口对的全部信号路径，
以及每条路径上每个开关的目标 Bits（required_bits）。源文件只读。

命令（完整参数表见 Resource edi://reference/netlist-pathfinder-guide）：
    Netlist_Pathfinder.exe --netlist <netlist> --ep <ep> --pairs "1-2,2-1" \
        [--max-paths N] [--max-states N] [--timeout N] [--encoding auto]
stdout 单行 UTF-8 JSON：{"netlist_path","ep_path","ports","results","errors"}。
退出码 0 = 每个端口对均完成且无状态计算错误（no_path 也是正常结果）；1 = 参数/文件/
解析/端口/开关状态错误或搜索不完整（此时 results 可能带部分路径，见各层 errors）。

限制（拓扑 ≠ 物理）：唯一一份说明在 find_netlist_paths 的 docstring「限制」段
（agent 经 describe_tool 读到），器件规则全表见说明书「现有器件规则」。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import (
    NETLIST_PATHFINDER_PATH,
    ExeNeedsElevation,
    run_exe,
)
from servers.utils import (
    default_result_path,
    error_response,
    per_tool_mutex,
    require_file,
)

_logger = logging.getLogger("simulation_tool.netlist_pathfinder")


def _normalize_pairs(pairs: list[dict]) -> tuple[list[tuple[int, int]], dict | None]:
    """校验 pairs（[{input, output}]）并归一化成 [(in, out)]；返回 (pairs, 错误)。"""
    if not isinstance(pairs, list) or not pairs:
        return [], error_response("INVALID_PARAMETERS", "pairs 必须是非空数组")
    out: list[tuple[int, int]] = []
    for p in pairs:
        if not isinstance(p, dict):
            return [], error_response("INVALID_PARAMETERS", "pairs 每项必须是对象 {input, output}")
        inp, outp = p.get("input"), p.get("output")
        for v, label in ((inp, "input"), (outp, "output")):
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                return [], error_response("INVALID_PARAMETERS", f"pairs 的 {label} 必须是正整数")
        if inp == outp:
            return [], error_response("INVALID_PARAMETERS", "pairs 的 input 与 output 必须不同")
        out.append((inp, outp))
    return out, None


@mcp.tool()
def find_netlist_paths(
    netlist_path: str,
    ep_path: str,
    pairs: list[dict],
    max_paths: int = 10000,
    max_states: int = 1000000,
    timeout: int = 30,
    encoding: str = "auto",
    output_path: str = "",
) -> dict[str, Any]:
    """寻路（仅限 TR 收发链路，不支持混频链路）：给定网表 + 原理图 + 有向端口对，枚举每个端口对的全部信号路径，并给出每条路径上每个开关的目标 Bits。

    用法："找出 1 到 2 端口之间的所有信号路径和开关状态"、"这个 TR 组件收发路径上开关要怎么设"

    纯本地计算，不需要 EDI 运行 / ADS / gRPC。输入网表与 EP 只读。结果可能很大
    （分支/回环让路径数指数增长），默认 max_paths=10000 / max_states=1000000 / timeout=30
    控制预算；达到预算时该端口对 status=incomplete、complete=false，path_count 只是已返回数。

    Args:
        netlist_path: 网表文件路径（netlist.log，无扩展名限制）。
        ep_path: 与该网表对应的 schematic.ep 路径（不能用其他工程的 EP）。
        pairs: 有向端口对数组，每项 {input, output}（正整数、二者不同），如
            [{input:1, output:2}, {input:2, output:1}] 分别查正向和反向。
        max_paths: 每个端口对最多保存路径数，0 不限，默认 10000。
        max_states: 每个端口对搜索边数预算，0 不限，默认 1000000。
        timeout: 每个端口对搜索时间预算（秒），0 不限，默认 30；不含文件解析和 JSON 序列化。
        encoding: 源文件编码，默认 auto（先 UTF-8/BOM，失败再 GB18030）。
        output_path: 结果 JSON 落盘路径；留空则落到默认产物目录
            （`MCP_RESULT_DIR`，未配置时 `%TEMP%/mcp_result`）下的
            `find_netlist_paths_<时间戳>.json`。父目录不存在会自动创建。

    Returns:
        {"success": True, "message": "...", "output_path": "<结果 JSON 绝对路径>",
         "summary": {"ports": [...], "pairs": [{input_port, output_port, status, complete, path_count}, ...],
                     "total_paths": N},
         "result": {...exe 完整 JSON（netlist_path/ep_path/ports/results/errors）...}}

        每条路径含 path_id / nodes / steps（器件序列）/ switch_settings（每个开关的
        throw_index/current_bits/required_bits）/ configurable / errors。
        required_bits 是该路径上开关需设成的目标状态（字符串，保留位宽）。
        success 仅看退出码 0（搜索完整且无状态错误）；某端口对 incomplete 或某路径
        configuration_error 时，仍需看 summary 各对的 status/complete 与 result.errors。

    限制（拓扑 ≠ 物理）：
        这是拓扑寻路，不是完整射频物理模型，且只面向 TR 链路——混频链路不支持：
        混频器等「其余多端口器件」按双向直通处理，不区分 RF/LO/IF，不判断频率转换、
        隔离度、增益、频段或路径独占（因此某条路径「存在」不等于该路径在物理上可用，
        混频链路的上下变频/镜像等指标请用 run_mixer_simulation）。switch_settings 只含
        EP genl_type=27 的开关；
        Bits 存在不代表器件是开关，移相器/衰减器的 Bits 不由寻路决定，保持原值。
        （上为拓扑寻路的关键规则，已足够构造调用；逐器件连通细节不再展开。）
    """
    netlist, err = require_file(netlist_path)
    if err:
        return err
    ep, err = require_file(ep_path)
    if err:
        return err

    pair_list, perr = _normalize_pairs(pairs)
    if perr:
        return perr

    for v, label in ((max_paths, "max_paths"), (max_states, "max_states"), (timeout, "timeout")):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return error_response("INVALID_PARAMETERS", f"{label} 必须是非负整数")

    return _find_netlist_paths_exec(
        netlist, ep, pair_list, max_paths, max_states, timeout, encoding, output_path,
    )


@per_tool_mutex
def _find_netlist_paths_exec(
    netlist: str,
    ep: str,
    pair_list: list[tuple[int, int]],
    max_paths: int,
    max_states: int,
    timeout: int,
    encoding: str,
    output_path: str,
) -> dict[str, Any]:
    args = [
        "--netlist", netlist,
        "--ep", ep,
        "--pairs", ",".join(f"{i}-{o}" for i, o in pair_list),
        "--max-paths", str(max_paths),
        "--max-states", str(max_states),
        "--timeout", str(timeout),
        "--encoding", (encoding or "auto").strip() or "auto",
    ]

    # 子进程总超时 = 每对搜索预算 × 端口对数 + 解析/序列化余量；钳到 run_exe 上限 3600
    proc_timeout = 3600 if timeout == 0 else min(3600, max(300, len(pair_list) * timeout + 120))
    _logger.info("find_netlist_paths pairs=%d max_paths=%d max_states=%d timeout=%d",
                 len(pair_list), max_paths, max_states, timeout)
    try:
        run = run_exe(NETLIST_PATHFINDER_PATH, args, proc_timeout)
    except ExeNeedsElevation as exc:
        return error_response(
            "EXE_NEEDS_ELEVATION", str(exc), retryable=True,
            exe=NETLIST_PATHFINDER_PATH, winerror=740,
            hint="以管理员身份重启 EDI MCP 服务后重试；服务未提权时 Windows 直接拒绝启动该 exe，不会弹 UAC")
    except RuntimeError as exc:
        return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                              hint="路径分支多时调大 timeout，或减小 max_paths/max_states 提前截断")
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    except FileNotFoundError as exc:
        return error_response("EXE_NOT_FOUND", str(exc), netlist_pathfinder_path=NETLIST_PATHFINDER_PATH,
                              hint="确认 EDI 安装目录下 simulation_tool/tr_simulation/Netlist_Pathfinder.exe 存在")

    data = run["json"]
    if data is None:
        return error_response(
            "PATHFINDER_FAILED",
            run["json_error"] or "exe 未输出合法 JSON",
            stdout=run["stdout"], stderr=run["stderr"], return_code=run["returncode"],
        )

    # 结果落盘：默认写默认产物目录（MCP_RESULT_DIR，未配时 %TEMP%/mcp_result）。
    # 无论 rc=0/1 都写 —— rc=1 时 results 里常带部分路径与逐层 errors，排障要靠这份原文。
    target = (output_path or "").strip()
    if target:
        if Path(target).expanduser().is_dir():
            return error_response("INVALID_PARAMETERS",
                                  f"output_path 是目录，需要文件路径: {target}")
        target = str(Path(target).expanduser().resolve())
    else:
        target = default_result_path("find_netlist_paths", ".json")
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as exc:
        return error_response("WRITE_FAILED", f"结果写入失败: {exc}", output_path=target)

    results = data.get("results") or []
    summary = [
        {"input_port": r.get("input_port"), "output_port": r.get("output_port"),
         "status": r.get("status"), "complete": r.get("complete"),
         "path_count": r.get("path_count")}
        for r in results if isinstance(r, dict)
    ]
    total_paths = sum((r.get("path_count") or 0) for r in results if isinstance(r, dict))

    ok = run["returncode"] == 0
    if ok:
        message = f"寻路完成：{len(pair_list)} 个端口对，共 {total_paths} 条路径"
    else:
        failed = [s for s in summary if s.get("status") == "error"]
        incomplete = [s for s in summary if s.get("status") == "incomplete"]
        config_err = [s for s in summary if s.get("status") == "configuration_error"]
        errs = data.get("errors") or []
        if not results:
            message = f"寻路失败（退出码 {run['returncode']}，无任何端口对结果，见 result.errors）"
        else:
            message = f"寻路未完全成功（退出码 {run['returncode']}）"
            if failed:
                message += f"；{len(failed)} 个端口对失败（端口不存在等）"
            if incomplete:
                message += f"；{len(incomplete)} 个端口对搜索不完整"
            if config_err:
                message += (f"；{len(config_err)} 个端口对有路径但开关状态无法确定"
                            "（要求位宽不足等，原因见该路径 errors）")
            if errs:
                message += f"；{len(errs)} 条顶层错误"
    return {"success": ok, "message": message, "output_path": target,
            "summary": {"ports": data.get("ports"), "pairs": summary, "total_paths": total_paths},
            "result": data}
