r"""mixer_simulation.exe 包装 — 混频链路仿真（上下变频/多通道/任意级变频）。

命令（见 exe 同目录 Mixer_Simulator使用说明.md）：
    mixer_simulation.exe --epp <epp> --edi <edi> --ads <ads> [--indicators <csv>] [--threads <n>] [--components <json>] --json <links>
stdout 单行 UTF-8 JSON：{"simulation_path","errors":[],"results":[...]}
退出码 0/1。**exe 每次运行都会自己生成 PDF/DOCX 报告**（在 simulation_path 顶层），
无需再调 export_simulation_report；results 的 electrical_parameters 仅供程序消费。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import (
    EDI_INSTALL_DIR,
    MIXER_SIMULATION_PATH,
    convert_ep_numeric,
    detect_ads_path,
    fmt_errors,
    normalize_dir,
    run_exe,
)
from servers.eda.config import EDA_GRPC_SERVER
from servers.utils import (
    collect_simulation_artifacts,
    error_response,
    per_tool_mutex,
    require_file,
    tcp_port_open,
)

_logger = logging.getLogger("simulation_tool.mixer_simulation")

_DEFAULT_INDICATORS = "变频增益"

# 结果目录里「值得单独给出下载链接」的文件后缀 → build_artifact 的 type
_KEY_EXT = {".csv": "csv", ".png": "image", ".jpg": "image", ".raw": "raw", ".log": "log",
            ".pdf": "pdf", ".docx": "docx"}
_MAX_ARTIFACTS = 40

#: 失败时带出的诊断量（控制响应体大小：最多 3 个失败工况 × 各 20 行 + 主日志 25 行）
_MAX_DIAG_CONDITIONS = 3
_MAX_ADS_LINES = 20
_MAX_MAIN_LOG_LINES = 25


def _tail_lines(path: Path, limit: int) -> str:
    """取文件尾部若干行；读不到就返回空串（诊断永远不能反过来把工具搞挂）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-limit:])


#: ads_output.log 里出现过的「不利」关键字（大小写不敏感）
_FAIL_KEYWORDS = ("error", "incorrect", "fail", "invalid", "not found",
                  "can not", "cannot", "missing", "no such", "denied")


def _looks_failed(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _FAIL_KEYWORDS)


def _collect_ads_diagnostics(sim_path: str) -> dict[str, Any]:
    """失败时把「真实错误」捞出来给调用方，而不是只留 exe 的归纳结论。

    背景（2026-10-21 实测）：ADS 报的是
        "The bin file of the model are incorrect" / "User-compiled model's analyze_nl
         function returned error"（模型 DLL 与 ADS 版本不匹配），
    而 exe 把它归纳成「模型 .ds 文件缺失，需 open_edi_project 补模型后重跑」——
    照那句去补 .ds 会白忙（模型库其实齐全）。所以这里直接读落盘的
    ads_output.log / simulation.log 原文，让模型与人都能一眼看到真因。

    返回 {} 表示没有可读诊断（目录不存在/无日志），不抛异常。
    """
    if not sim_path:
        return {}
    root = Path(sim_path)
    if not root.is_dir():
        return {}

    out: dict[str, Any] = {}

    # exe 写进 ads_output.log 的文本五花八门：hpeesofsim 的 "Error …"、
    # "The bin file of the model are incorrect"、以及 "Can not find .../ADS2020"
    # 这类路径问题。只认 "Error" 会漏掉一半真因，所以先按关键字挑，挑不到再兜底。
    all_logs: list[dict[str, str]] = []
    for log in sorted(root.rglob("ads_output.log")):
        tail = _tail_lines(log, _MAX_ADS_LINES)
        if tail:
            all_logs.append({"condition": log.parent.name, "ads_output": tail})

    failed = [a for a in all_logs if _looks_failed(a["ads_output"])]
    if not failed:
        failed = [a for a in all_logs if "finished" not in a["ads_output"].lower()][:1]
    failed = failed[:_MAX_DIAG_CONDITIONS]

    if failed:
        out["ads_output"] = failed
        # 把第一条「像真因」的行连同紧随的缩进续行一起提上来：
        # ADS 的写法是   <一句话> \n    <细节>，只取一行会丢掉 "analyze_nl returned error" 这种关键行。
        lines = failed[0]["ads_output"].splitlines()
        for i, line in enumerate(lines):
            if not _looks_failed(line):
                continue
            block = [line.strip()]
            for nxt in lines[i + 1:i + 3]:
                if nxt[:1] in (" ", "\t"):
                    block.append(nxt.strip())
                else:
                    break
            out["first_error"] = " / ".join(b for b in block if b)
            break

    main_log = _tail_lines(root / "simulation.log", _MAX_MAIN_LOG_LINES)
    if main_log:
        out["simulation_log_tail"] = main_log
    return out



@mcp.tool()
@per_tool_mutex
def run_mixer_simulation(
    epp_path: str,
    links: list,
    indicators: str = _DEFAULT_INDICATORS,
    threads: int = 5,
    components: list | None = None,
    edi_path: str = "",
    ads_path: str = "",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """执行混频链路仿真（含混频器、LO 本振、上下变频、镜像抑制/本振泄漏）。不含变频的纯链路请用 run_tr_simulation。

    用法："仿真这个混频工程的下变频链路，提取变频增益和本振泄漏"

    前提：Windows + EDI 已运行（gRPC 127.0.0.1:50055 可用）+ ADS 许可可用。
    links 为混频链路数组，每项含 name/direction/f_rf/f_if/bw/pin/p_lo 及可选的
    channels/stages/simulation/control（详见 exe 说明文档）。
    结果 results[].electrical_parameters 供程序消费；**本次运行已自带报告**（见 Returns 的
    report_artifacts），不要再调 export_simulation_report 重生成 —— 它的指标目录
    （indicator_catalog）不含混频指标，变频增益/镜像抑制/本振泄漏等会被拒。

    Args:
        epp_path: .epp 工程文件绝对路径（必填，同目录需存在 schematics/main/schematic.ep）。
        links: 混频链路数组（至少一条），每项 {name, direction, f_rf, f_if, bw, pin, p_lo, ...}，详见 exe 说明文档。
        indicators: 逗号分隔的指标，默认 "变频增益"（支持变频增益/增益平坦度/带内杂散/本振泄漏/镜像抑制/输入P−1dB/输出P−1dB/Psat/变频噪声系数）。
        threads: 指标并发线程数（正整数），默认 5。
        components: 器件选型 JSON 数组（可选），默认不传。
        edi_path: EDI 安装目录，默认用固定目录（simulation_tool 的上一级）。
        ads_path: ADS 安装目录，为空时自动探测（C:/Program Files/Keysight/ADS*）。
        timeout_seconds: 最长等待秒数，默认 1800（多链路 × 多指标跑 ADS 可能超过 10 分钟；上限 3600）。

    Returns:
        {"success": True, "simulation_path": "...", "results": [...], "errors": [],
         "artifacts": [...], "report_artifacts": [{"type": "pdf"|"docx", "path": "...", "name": "..."}]}
        results 每项 {link, electrical_parameters:[{indicator,minimum,typical,maximum,unit}]}。
        部分指标失败时 success 仍为 True 但 errors 非空，必须检查。

        ⚠️ **exe 每次运行都会在 simulation_path 顶层生成报告**：
        ``<工程名>_仿真报告.pdf`` 与 ``.docx``（含指标判定表、曲线、拓扑图）。
        报告条目固定放在 report_artifacts，artifacts 里也排在最前，message 会写明
        「已生成仿真报告：…」，warnings 里同样有一条提示 —— 跑完直接把报告交付给用户，
        不需要再调 export_simulation_report 重跑一遍。
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

    # 4. edi / ads 路径（显式传参也要规范化：混合斜杠会让 exe 回退硬编码 ADS2020）
    edi = normalize_dir(edi_path) or normalize_dir(EDI_INSTALL_DIR)
    if not Path(edi).is_dir():
        return error_response("PATH_NOT_FOUND", f"EDI 安装目录不存在: {edi}（可传 edi_path 覆盖）")
    ads = normalize_dir(ads_path) or detect_ads_path()
    if not ads or not Path(ads).is_dir():
        return error_response("PATH_NOT_FOUND", f"未找到 ADS 安装目录（已探测：{ads or '无'}），请显式传 ads_path")

    # 4.5 前置预检：EDI gRPC 与 EDI 目录必需组件（同 run_tr_simulation，避免白等 + 留空目录）。
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
            f"EDI 的 gRPC 服务未就绪（{EDA_GRPC_SERVER} 连不上），混频仿真第一步需要从 EDI 取网表，请先启动 EDI 软件",
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
        components_json = json.dumps(components, ensure_ascii=False) if components else None
    except (TypeError, ValueError) as exc:
        return error_response("LINK_JSON_INVALID", f"links/components 含无法 JSON 序列化的值: {exc}")

    args = [
        "--epp", resolved_epp,
        "--edi", edi,
        "--ads", ads,
        "--indicators", (indicators or "").strip() or _DEFAULT_INDICATORS,
        "--threads", str(threads),
        "--json", links_json,
    ]
    if components_json is not None:
        args += ["--components", components_json]

    # 6. 运行
    try:
        run = run_exe(MIXER_SIMULATION_PATH, args, timeout_seconds)
    except RuntimeError as exc:
        return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                              hint="链路/指标多时把 timeout_seconds 调大（上限 3600）后重试")
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    except FileNotFoundError as exc:
        return error_response("EXE_NOT_FOUND", str(exc), mixer_simulation_path=MIXER_SIMULATION_PATH,
                              hint="确认 EDI 安装目录下 simulation_tool/mixer_simulation/mixer_simulation.exe 存在")

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
    partial = [r for r in (data.get("results") or []) if r]
    # ⚠️ returncode != 0 但 exe 已给出部分 results ⇒ 降级为「部分成功」。
    # 实测（2026-10-21）：5 个指标里「带内杂散」崩溃导致 exe 退出码 1，但另外 4 个指标
    # 的结果与 194 KB 的报告 PDF/DOCX 都已在 simulation_path 下生成 —— 整单判失败会把这些
    # 成果全部吞掉，调用方一无所获。只有「一条结果都没有」才算真失败。
    partial_failure = run["returncode"] != 0 and bool(partial)
    if run["returncode"] != 0 and not partial:
        sim_path = data.get("simulation_path", "")
        # 捞真实错误（ADS/HSPICE 原文 + 主日志尾部）：exe 的归纳结论可能是误报
        diag = _collect_ads_diagnostics(sim_path)
        message = fmt_errors(errors) or "混频仿真失败"
        if diag.get("first_error"):
            message = f"{message}；ADS 原文：{diag['first_error']}"
        return error_response(
            "SIMULATION_EXECUTION_FAILED",
            message,
            hint=("先看 details.ads_output 里的 ADS 原文 —— 「模型 bin/DLL 与 ADS 版本不匹配」"
                  "这类不可重试；其次确认 EDI 已打开目标工程、ADS 许可可用"),
            mixer_errors=errors, simulation_path=sim_path, **diag,
        )

    # 8. 成功（部分指标失败时 errors 非空，须提示调用方检查）
    sim_path = data.get("simulation_path", "")
    results = convert_ep_numeric(data.get("results", []))
    artifacts, report_artifacts, truncated = collect_simulation_artifacts(
        sim_path, "run_mixer_simulation", _KEY_EXT, _MAX_ARTIFACTS)

    warnings: list[str] = []
    if errors:
        warnings.append(f"有 {len(errors)} 项指标失败，仿真数据可能不完整，详见 errors 字段。")
    if partial_failure:
        warnings.append(f"exe 退出码 {run['returncode']}：有指标失败，已按「部分成功」返回 "
                        f"{len(results)} 条可用结果（报告同样已生成），失败工况诊断见 errors。")
    if truncated:
        warnings.append(f"结果目录文件较多，artifacts 只列了前 {_MAX_ARTIFACTS} 个关键文件（完整清单见 simulation_path）。")

    # exe 每次运行都会在 simulation_path 顶层生成 <工程名>_仿真报告.pdf/.docx。
    # 必须主动告知调用方 —— 否则没人会想到去翻磁盘目录（2026-10-21 实证：报告被
    # artifacts 截断静默吞掉，用户问「你没告诉我啊」）。
    report_note = ""
    if report_artifacts:
        names = "、".join(a["name"] for a in report_artifacts)
        report_note = f"；已生成仿真报告：{names}"
        warnings.insert(0, f"本次运行已生成仿真报告（{names}），路径见 report_artifacts；"
                           f"可直接交付或存档，无需再调报告工具重跑。")

    resp: dict[str, Any] = {
        "success": True,
        "simulation_path": sim_path,
        "results": results,
        "errors": errors,
        "artifacts": artifacts,
        "report_artifacts": report_artifacts,
        "message": (f"混频仿真部分完成：{len(results)} 条链路结果（exe 退出码 {run['returncode']}，"
                    f"有指标失败但已返回可用结果）"
                    if partial_failure else f"混频仿真完成：{len(results)} 条链路结果")
                   + ("（部分指标失败，详见 errors）" if errors else "")
                   + report_note,
    }
    if warnings:
        resp["warnings"] = warnings
    return resp
