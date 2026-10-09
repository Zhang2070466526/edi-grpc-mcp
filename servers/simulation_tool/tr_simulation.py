r"""TR_Simulator.exe 包装 — TR 纯硬编码仿真（网表→路径分析→ADS→结果提取）。

命令（见 exe 同目录 Report_Exporter使用说明.md）：
    TR_Simulator.exe --epp <epp> --edi <edi> --ads <ads> [--indicators <csv>] [--threads <n>] --json <links>
stdout 单行 UTF-8 JSON：{"simulation_path","errors":[],"results":[...]}
退出码 0/1。results 的 electrical_parameters 结构可直接喂给 export_simulation_report。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import (
    EDI_INSTALL_DIR,
    TR_SIMULATOR_PATH,
    detect_ads_path,
    fmt_errors,
    run_exe,
)
from servers.eda.config import EDA_GRPC_SERVER
from servers.utils import (
    build_artifact,
    error_response,
    per_tool_mutex,
    require_file,
    tcp_port_open,
)

_logger = logging.getLogger("simulation_tool.tr_simulation")

_DEFAULT_INDICATORS = "工作频率,输出增益,噪声系数"

# 结果目录里「值得单独给出下载链接」的文件后缀 → build_artifact 的 type
_KEY_EXT = {".csv": "csv", ".png": "image", ".jpg": "image", ".raw": "raw", ".log": "log"}
_MAX_ARTIFACTS = 40


@mcp.tool()
@per_tool_mutex
def run_tr_simulation(
    epp_path: str,
    links: list,
    indicators: str = _DEFAULT_INDICATORS,
    threads: int = 5,
    edi_path: str = "",
    ads_path: str = "",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """用 TR_Simulator.exe 跑 TR 链路仿真（网表→路径分析→ADS→结果提取），返回各链路电参数。

    用法："仿真这个 TR 工程的两条链路"、"提取输出增益和噪声系数"

    前提：Windows + EDI 已运行（gRPC 127.0.0.1:50055 可用）+ ADS 许可可用。
    结果 results[].electrical_parameters 可直接作为 export_simulation_report 的
    report.links[].electrical_parameters 生成报告。

    Args:
        epp_path: .epp 工程文件绝对路径（必填，同目录需存在 schematics/main/schematic.ep）。
        links: 链路数组（至少一条），每项 {name, min_freq, max_freq, input_port, output_port, ...}，详见 exe 使用说明。
        indicators: 逗号分隔的指标，默认 "工作频率,输出增益,噪声系数"。
        threads: 指标并发线程数（正整数），默认 5。
        edi_path: EDI 安装目录，默认用固定目录（simulation_tool 的上一级）。
        ads_path: ADS 安装目录，为空时自动探测（C:/Program Files/Keysight/ADS*）。
        timeout_seconds: 最长等待秒数，默认 1800（多链路 × 多指标跑 ADS 可能超过 10 分钟；上限 3600）。

    Returns:
        {"success": True, "simulation_path": "...", "results": [...], "errors": [],
         "artifacts": [...]}
        results 每项 {link, electrical_parameters:[{indicator,minimum,typical,maximum,unit}]}。
        部分指标失败时 success 仍为 True 但 errors 非空，必须检查。
    """
    # 1. epp 存在
    resolved_epp, err = require_file(epp_path, (".epp",))
    if err:
        return err

    # 2. links 非空数组
    if not isinstance(links, list) or not links:
        return error_response("NO_LINKS", "links 必须是非空数组（至少一条链路）")
    if not all(isinstance(x, dict) for x in links):
        return error_response("LINK_JSON_INVALID", "links 每项必须是对象")

    # 3. threads 正整数
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        return error_response("INVALID_PARAMETERS", "threads 必须是正整数")

    # 4. edi / ads 路径
    edi = (edi_path or "").strip() or EDI_INSTALL_DIR
    if not Path(edi).is_dir():
        return error_response("PATH_NOT_FOUND", f"EDI 安装目录不存在: {edi}（可传 edi_path 覆盖）")
    ads = (ads_path or "").strip() or detect_ads_path()
    if not ads or not Path(ads).is_dir():
        return error_response("PATH_NOT_FOUND", f"未找到 ADS 安装目录（已探测：{ads or '无'}），请显式传 ads_path")

    # 4.5 前置预检：EDI gRPC 与 EDI 目录必需组件。
    #     实测（EDI 未启动）：直接进 exe 要白等 11.2 s 才回 [NETLIST_NOT_FOUND]（看不出真因），
    #     且失败时 exe 会在自己的 data/ 目录里留一个空目录 ⇒ 这里提前拦掉最常见的前置失败。
    host, sep, port = EDA_GRPC_SERVER.rpartition(":")
    if not sep:
        host, port = EDA_GRPC_SERVER, "50055"
    try:
        grpc_port = int(port)
    except ValueError:
        grpc_port = 0
    if grpc_port and not tcp_port_open(host.strip("[]") or "127.0.0.1", grpc_port):
        return error_response(
            "EDA_GRPC_OFFLINE",
            f"EDI 的 gRPC 服务未就绪（{EDA_GRPC_SERVER} 连不上），TR 仿真第一步需要从 EDI 取网表，请先启动 EDI 软件",
            hint="启动 EDI（或先用 launch_edi）并确认 /ready 的 grpc 为 online 后重试")
    turbocharts_exe = Path(edi) / "turbocharts_app.exe"
    if not turbocharts_exe.is_file():
        return error_response(
            "PATH_NOT_FOUND",
            f"EDI 安装目录缺少 turbocharts_app.exe：{turbocharts_exe}",
            hint="确认 edi_path 指向 EDI 安装目录（内含 turbocharts_app.exe），或用 edi_path 显式指定")

    # 5. 构造命令
    try:
        links_json = json.dumps(links, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        return error_response("LINK_JSON_INVALID", f"links 含无法 JSON 序列化的值: {exc}")

    args = [
        "--epp", resolved_epp,
        "--edi", edi,
        "--ads", ads,
        "--indicators", (indicators or "").strip() or _DEFAULT_INDICATORS,
        "--threads", str(threads),
        "--json", links_json,
    ]

    # 6. 运行
    try:
        run = run_exe(TR_SIMULATOR_PATH, args, timeout_seconds)
    except RuntimeError as exc:
        return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                              hint="链路/指标多时把 timeout_seconds 调大（上限 3600）后重试")
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    except FileNotFoundError as exc:
        return error_response("EXE_NOT_FOUND", str(exc), tr_simulator_path=TR_SIMULATOR_PATH,
                              hint="确认 EDI 安装目录下 simulation_tool/tr_simulation/TR_Simulator.exe 存在")

    # 7. 解析
    data = run["json"]
    if data is None:
        return error_response(
            "SIMULATION_EXECUTION_FAILED",
            run["json_error"] or "exe 未输出合法 JSON",
            stdout=run["stdout"], stderr=run["stderr"], return_code=run["returncode"],
        )
    if not isinstance(data, dict):
        return error_response("SIMULATION_EXECUTION_FAILED", "exe 返回格式异常", stdout=run["stdout"])

    errors = data.get("errors") or []
    if run["returncode"] != 0:
        return error_response(
            "SIMULATION_EXECUTION_FAILED",
            fmt_errors(errors) or "TR 仿真失败",
            hint="先确认 EDI 已启动、目标工程已打开；单一指标失败时可减少 indicators 后重试（详见 details.tr_errors）",
            tr_errors=errors, simulation_path=data.get("simulation_path", ""),
        )

    # 8. 成功（部分指标失败时 errors 非空，须提示调用方检查）
    sim_path = data.get("simulation_path", "")
    results = data.get("results", [])
    artifacts: list[dict] = []
    truncated = False
    if sim_path and Path(sim_path).is_dir():
        artifacts.append(build_artifact("directory", sim_path, "run_tr_simulation"))
        # 目录本身拿不到 token 下载链接（build_file_link 只认文件）⇒ 把关键文件也列出来，
        # 远程客户端才能下载仿真产物（CSV/PNG/RAW/日志）。
        key_files = sorted(
            (p for p in Path(sim_path).rglob("*") if p.is_file() and p.suffix.lower() in _KEY_EXT),
            key=lambda p: str(p),
        )
        for f in key_files[:_MAX_ARTIFACTS]:
            artifacts.append(build_artifact(_KEY_EXT[f.suffix.lower()], str(f), "run_tr_simulation"))
        truncated = len(key_files) > _MAX_ARTIFACTS

    warnings: list[str] = []
    if errors:
        warnings.append(f"有 {len(errors)} 项指标失败，仿真数据可能不完整，详见 errors 字段。")
    if truncated:
        warnings.append(f"结果目录文件较多，artifacts 只列了前 {_MAX_ARTIFACTS} 个关键文件（完整清单见 simulation_path）。")

    resp: dict[str, Any] = {
        "success": True,
        "simulation_path": sim_path,
        "results": results,
        "errors": errors,
        "artifacts": artifacts,
        "message": f"TR 仿真完成：{len(results)} 条链路结果"
                   + ("（部分指标失败，详见 errors）" if errors else ""),
    }
    if warnings:
        resp["warnings"] = warnings
    return resp
