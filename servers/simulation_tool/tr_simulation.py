r"""TR_Simulator.exe 包装 — TR 纯硬编码仿真（网表→路径分析→ADS→结果提取）。

命令（见 exe 同目录 Report_Exporter使用说明.md）：
    TR_Simulator.exe --epp <epp> --edi <edi> --ads <ads> [--indicators <csv>] [--threads <n>] --json <links>
stdout 单行 UTF-8 JSON：{"simulation_path","errors":[],"results":[...]}
退出码 0/1。results 的 electrical_parameters 可喂给 export_simulation_report
（TR_Simulator 本身不生成 PDF/DOCX 报告，需要正式报告时走那条路）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from servers import mcp
from servers.simulation_tool.config import (
    EDI_INSTALL_DIR,
    TR_SIMULATOR_PATH,
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

_logger = logging.getLogger("simulation_tool.tr_simulation")

_DEFAULT_INDICATORS = "工作频率,输出增益,噪声系数"

#: 契约版本：指标表/链路字段任何变动都要改这里，智能体可据此判断自己记的是旧版。
TR_CONTRACT_VERSION = "TR-1.0 (23 indicators, 13 link fields)"

# ── 指标权威目录（与 TR_Simulator使用说明.md「完整指标表」逐行对齐）────────────
# 单一事实源：_TR_INDICATORS 由此派生，describe_tool 与 edi://reference/tr-simulation-guide
# 都读它，避免「文档一套、代码一套」。
_INDICATOR_CATALOG: dict[str, dict[str, str]] = {
    "工作频率":     {"unit": "GHz", "method": "直接输出，自动加入", "extra": "无", "output": "频段上下限"},
    "反向增益":     {"unit": "dB", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "输出增益":     {"unit": "dB", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "增益平坦度":   {"unit": "dB", "method": "S 参数后处理", "extra": "无", "output": "典型值，±形式"},
    "噪声系数":     {"unit": "dB", "method": "S 参数噪声分析", "extra": "无", "output": "最小值、最大值"},
    "输入回波损耗": {"unit": "dB", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "输出回波损耗": {"unit": "dB", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "输入驻波":     {"unit": "无量纲", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "输出驻波":     {"unit": "无量纲", "method": "S 参数", "extra": "无", "output": "最小值、最大值"},
    "移相步进":     {"unit": "°", "method": "直接输出", "extra": "phase_step", "output": "典型值"},
    "衰减步进":     {"unit": "dB", "method": "直接输出", "extra": "atten_step", "output": "典型值"},
    "移相误差RMS":  {"unit": "°", "method": "S 参数＋移相状态扫描", "extra": "phase_devices、phase_step", "output": "RMS 典型值"},
    "衰减误差RMS":  {"unit": "dB", "method": "S 参数＋衰减状态扫描", "extra": "atten_devices、atten_step", "output": "RMS 典型值"},
    "衰减附加相移": {"unit": "°", "method": "S 参数＋衰减状态扫描", "extra": "atten_devices", "output": "最小值、最大值"},
    "移相幅度波动": {"unit": "dB", "method": "S 参数＋移相状态扫描", "extra": "phase_devices", "output": "最小值、最大值"},
    "移相态":       {"unit": "°", "method": "S 参数＋移相状态扫描", "extra": "phase_devices", "output": "仅曲线、CSV（不进结果 JSON）"},
    "衰减态":       {"unit": "dB", "method": "S 参数＋衰减状态扫描", "extra": "atten_devices", "output": "仅曲线、CSV（不进结果 JSON）"},
    "延时步进":     {"unit": "s", "method": "群时延＋移相状态扫描", "extra": "phase_devices", "output": "最小值、最大值"},
    "杂散抑制":     {"unit": "dBc", "method": "HB 谐波平衡", "extra": "freq_rx、pwr_rx", "output": "典型值"},
    "输出功率":     {"unit": "dBm", "method": "HB 谐波平衡", "extra": "freq_pout、pwr_pout", "output": "典型值"},
    "OP1dB":        {"unit": "dBm", "method": "XDB，输出 1dB 压缩点", "extra": "freq_rx、pwr_rx", "output": "最小值、最大值"},
    "IP1dB":        {"unit": "dBm", "method": "XDB，输入 1dB 压缩点", "extra": "freq_rx、pwr_rx", "output": "最小值、最大值"},
    "Psat":         {"unit": "dBm", "method": "XDB，按 5dB 压缩配置实现", "extra": "freq_rx、pwr_rx", "output": "最小值、最大值"},
}

# 23 个合法指标名（顺序 = 使用说明表顺序；exe 不接受简称/别名）
_TR_INDICATORS: tuple[str, ...] = tuple(_INDICATOR_CATALOG)

#: 用户常用名（归一化后）→ 精确指标名，仅用于报错提示，不自动纠正。
_INDICATOR_ALIASES: dict[str, str] = {
    "输入p-1": "IP1dB", "输入p1db": "IP1dB", "输入1db压缩点": "IP1dB",
    "输出p1db": "OP1dB", "输出1db压缩点": "OP1dB",
    "移相精度": "移相误差RMS", "移相精度rms": "移相误差RMS",
    "衰减精度": "衰减误差RMS", "衰减精度rms": "衰减误差RMS",
}

#: 指标 → 必须由调用方给出的链路字段（扫描类指标没有器件实例名跑不了）
_INDICATOR_REQUIRES: dict[str, str] = {
    "移相误差RMS": "phase_devices", "移相幅度波动": "phase_devices",
    "移相态": "phase_devices", "延时步进": "phase_devices",
    "衰减误差RMS": "atten_devices", "衰减附加相移": "atten_devices",
    "衰减态": "atten_devices",
}

# ── 链路字段权威表（与 TR_Simulator使用说明.md「链路 JSON」表逐行对齐）──────────
# exe 侧「不接受表外字段」，在 MCP 层先拦并给出正确字段名，别等 exe 回 UNKNOWN_LINK_FIELD。
_LINK_FIELDS: dict[str, dict[str, Any]] = {
    "name": {"type": "string", "required": True, "default": None,
             "doc": "唯一非空字符串，合法 Windows 目录名"},
    "min_freq": {"type": "number", "required": False, "default": 0,
                 "doc": "GHz，有限数值，须 < max_freq；默认 0"},
    "max_freq": {"type": "number", "required": False, "default": 100,
                 "doc": "GHz，有限数值，须 > min_freq；默认 100"},
    "input_port": {"type": "integer", "required": False, "default": 1,
                   "doc": "正整数端口号，须与 output_port 不同且在工程中连通；默认 1"},
    "output_port": {"type": "integer", "required": False, "default": 2,
                    "doc": "正整数端口号，须与 input_port 不同；默认 2"},
    "phase_step": {"type": "number", "required": False, "default": 5.0,
                   "doc": "度，有限正数；移相扫描指标使用；默认 5.0"},
    "atten_step": {"type": "number", "required": False, "default": 0.5,
                   "doc": "dB，有限正数；衰减扫描指标使用；默认 0.5"},
    "phase_devices": {"type": "array", "required": False, "default": [],
                      "doc": "移相器实例名数组；移相类指标必须提供；默认 []"},
    "atten_devices": {"type": "array", "required": False, "default": [],
                      "doc": "衰减器实例名数组；衰减类指标必须提供；默认 []"},
    "freq_rx": {"type": "number", "required": False, "default": "频段中点取整",
                "doc": "GHz，须在频段内；杂散抑制/OP1dB/IP1dB/Psat 使用"},
    "pwr_rx": {"type": "number", "required": False, "default": -20,
               "doc": "dBm，大信号输入功率；默认 -20"},
    "freq_pout": {"type": "number", "required": False, "default": "频段中点取整",
                  "doc": "GHz，须在频段内；输出功率使用"},
    "pwr_pout": {"type": "number", "required": False, "default": -20,
                 "doc": "dBm，输出功率的输入功率；默认 -20"},
}

_NUMERIC_LINK_FIELDS = ("min_freq", "max_freq", "phase_step", "atten_step",
                        "freq_rx", "pwr_rx", "freq_pout", "pwr_pout")


class TrLink(BaseModel):
    """一条收发链路。字段与 TR_Simulator使用说明.md「链路 JSON」表一致，**不接受表外字段**。

    放进 MCP inputSchema 的意义：智能体不必猜 links 里能写什么——字段名、类型、单位、
    默认值都在 schema 里；写错类型/表外字段在调用前就被拒，并给出正确字段名。
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    name: str = Field(..., description="必填。唯一非空字符串，合法 Windows 目录名")
    min_freq: float = Field(0, description=_LINK_FIELDS["min_freq"]["doc"])
    max_freq: float = Field(100, description=_LINK_FIELDS["max_freq"]["doc"])
    input_port: int = Field(1, description=_LINK_FIELDS["input_port"]["doc"])
    output_port: int = Field(2, description=_LINK_FIELDS["output_port"]["doc"])
    phase_step: float = Field(5.0, description=_LINK_FIELDS["phase_step"]["doc"])
    atten_step: float = Field(0.5, description=_LINK_FIELDS["atten_step"]["doc"])
    phase_devices: list[str] = Field(default_factory=list,
                                     description=_LINK_FIELDS["phase_devices"]["doc"])
    atten_devices: list[str] = Field(default_factory=list,
                                     description=_LINK_FIELDS["atten_devices"]["doc"])
    freq_rx: float | None = Field(None, description=_LINK_FIELDS["freq_rx"]["doc"])
    pwr_rx: float = Field(-20, description=_LINK_FIELDS["pwr_rx"]["doc"])
    freq_pout: float | None = Field(None, description=_LINK_FIELDS["freq_pout"]["doc"])
    pwr_pout: float = Field(-20, description=_LINK_FIELDS["pwr_pout"]["doc"])

    @field_validator(*_NUMERIC_LINK_FIELDS, mode="before")
    @classmethod
    def _require_json_number(cls, value: Any, info) -> Any:
        """exe 拒绝字符串数字/布尔/NaN/Infinity，这里提前拦（否则会被 pydantic 悄悄转换）。"""
        if value is None:
            return None  # freq_rx/freq_pout 可选，null 与「不传」等价，交给 exe 默认中点
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{info.field_name} 必须是 JSON 数字"
                             "（字符串数字、布尔、NaN、Infinity 均不接受）")
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            raise ValueError(f"{info.field_name} 必须是有限数值（不接受 NaN/Infinity）")
        return value

    @field_validator("input_port", "output_port", mode="before")
    @classmethod
    def _require_json_int(cls, value: Any, info) -> Any:
        """校验端口字段是 JSON 整数。"""
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{info.field_name} 必须是 JSON 整数（字符串或小数不接受）")
        return value

    @field_validator("phase_devices", "atten_devices", mode="before")
    @classmethod
    def _require_instance_names(cls, value: Any, info) -> Any:
        """校验器件数组是非空字符串 list。"""
        if value is None:
            return []
        if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
            raise ValueError(f"{info.field_name} 必须是非空字符串数组"
                             "（工程里的器件实例名，如 [\"NC12246C_812PD1\"]）")
        return value

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        """校验 name 非空。"""
        if not value or not value.strip():
            raise ValueError("name 不能为空")
        return value


def _parse_indicators(indicators: str) -> list[str]:
    """按逗号拆指标名（去空白、保序去重）。"""
    out: list[str] = []
    for token in (indicators or "").split(","):
        name = token.strip()
        if name and name not in out:
            out.append(name)
    return out


def _check_indicators(indicators: str) -> dict | None:
    """校验 indicators 里每个指标名，表外名/别名返回错误（含正确名提示），合法返回 None。"""
    import difflib

    for name in _parse_indicators(indicators):
        if name in _TR_INDICATORS:
            continue
        norm = name.lower().replace(" ", "")
        correct = _INDICATOR_ALIASES.get(norm)
        if correct:
            resp = error_response(
                "UNKNOWN_INDICATOR",
                f"指标名 {name!r} 不存在；请用精确名称 {correct!r}（别名不自动转换）",
                hint=f"合法指标共 {len(_TR_INDICATORS)} 个：{'、'.join(_TR_INDICATORS)}",
            )
            resp["did_you_mean"] = correct
            resp["valid_indicators"] = list(_TR_INDICATORS)
            return resp
        close = difflib.get_close_matches(name, _TR_INDICATORS, n=1, cutoff=0.6)
        suggest = f"，是否想传 {close[0]!r}？" if close else ""
        resp = error_response(
            "UNKNOWN_INDICATOR",
            f"指标名 {name!r} 不存在{suggest}",
            hint=f"合法指标共 {len(_TR_INDICATORS)} 个：{'、'.join(_TR_INDICATORS)}",
        )
        if close:
            resp["did_you_mean"] = close[0]
        resp["valid_indicators"] = list(_TR_INDICATORS)
        return resp
    return None


def _link_dict(item: Any) -> dict[str, Any]:
    """TrLink 实例或 dict → dict（TrLink 只保留调用方显式给出的字段，其余交给 exe 默认）。"""
    if isinstance(item, TrLink):
        return item.model_dump(exclude_unset=True)
    return dict(item)


def _check_links(links: list, indicators: list[str]) -> dict | None:
    """校验每条链路的表外字段与「指标↔扫描器件」一致性；不合法返回结构化错误。

    exe 侧也会拦（UNKNOWN_LINK_FIELD / ARGUMENT_ERROR），但那要等仿真流程启动，且不告诉
    调用方「正确字段名是哪几个 / 这个指标该配哪个设备数组」——那正是智能体瞎猜的温床。
    """
    import difflib

    valid = sorted(_LINK_FIELDS)
    needed: dict[str, list[str]] = {}
    for ind in indicators:
        field = _INDICATOR_REQUIRES.get(ind)
        if field:
            needed.setdefault(field, []).append(ind)

    for idx, item in enumerate(links):
        if isinstance(item, dict):
            unknown = [k for k in item if k not in _LINK_FIELDS]
        else:
            unknown = list(getattr(item, "model_extra", None) or {})
        if unknown:
            guesses = {k: (difflib.get_close_matches(k, valid, n=1, cutoff=0.6) or [None])[0]
                       for k in unknown}
            detail = "、".join(f"{k}（是否想传 {v}？）" if v else k for k, v in guesses.items())
            resp = error_response(
                "UNKNOWN_LINK_FIELD",
                f"links[{idx}] 含表外字段：{detail}",
                hint=(f"链路合法字段共 {len(valid)} 个：{'、'.join(valid)}；"
                      "完整说明见 describe_tool(\"run_tr_simulation\")"),
            )
            resp["unknown_fields"] = guesses
            resp["valid_link_fields"] = valid
            return resp

        data = _link_dict(item)
        label = data.get("name") or f"links[{idx}]"
        for field, inds in needed.items():
            if not data.get(field):
                resp = error_response(
                    "LINK_MISSING_DEVICES",
                    f"{label} 缺少 {field}：指标 {'、'.join(inds)} 需要扫描该字段列出的器件实例名",
                    hint=(f"在 links[{idx}] 里补 {field}: [\"<器件实例名>\"]；"
                          "实例名必须与工程一致、位于有效链路上且类型正确"),
                )
                resp["missing_field"] = field
                return resp
    return None

# 结果目录里「值得单独给出下载链接」的文件后缀 → build_artifact 的 type
# .pdf/.docx 是 exe 每次运行都会产出的仿真报告，必须收进来（原来漏了，报告永远不出现在 artifacts）。
_KEY_EXT = {".csv": "csv", ".png": "image", ".jpg": "image", ".raw": "raw", ".log": "log",
            ".pdf": "pdf", ".docx": "docx"}
_MAX_ARTIFACTS = 40


@mcp.tool()
@per_tool_mutex
def run_tr_simulation(
    epp_path: str,
    links: list[TrLink],
    indicators: str = _DEFAULT_INDICATORS,
    threads: int = 5,
    edi_path: str = "",
    ads_path: str = "",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """跑 TR 收发链路仿真（纯链路：增益/噪声/P−1dB，不含混频器与变频）。有混频/本振/镜像指标请用 run_mixer_simulation。

    用法："仿真这个 TR 工程的两条链路"、"提取输出增益和噪声系数"

    前提：Windows + EDI 已运行（gRPC 127.0.0.1:50055 可用）+ ADS 许可可用。
    TR_Simulator 本身不生成报告；需要正式报告时，把 results[].electrical_parameters
    作为 export_simulation_report 的 report.links[].electrical_parameters 传入即可
    （TR 指标在其 indicator_catalog 内；混频指标不在，走 mixer 自带报告）。

    Args:
        epp_path: .epp 工程文件绝对路径（必填，同目录需存在 schematics/main/schematic.ep）。
        links: 链路数组（至少一条）。每条链路只能写这 13 个字段：name（必填）、min_freq、max_freq、input_port、output_port、phase_step、atten_step、phase_devices、atten_devices、freq_rx、pwr_rx、freq_pout、pwr_pout；表外字段直接拒绝（exe 同样不接受）。字段类型/单位/默认值见 inputSchema 或 describe_tool。
        indicators: 逗号分隔的精确指标名，默认 "工作频率,输出增益,噪声系数"。23 个合法名：工作频率, 反向增益, 输出增益, 增益平坦度, 噪声系数, 输入回波损耗, 输出回波损耗, 输入驻波, 输出驻波, 移相步进, 衰减步进, 移相误差RMS, 衰减误差RMS, 衰减附加相移, 移相幅度波动, 移相态, 衰减态, 延时步进, 杂散抑制, 输出功率, OP1dB, IP1dB, Psat。单位与实现方式见 edi://reference/tr-simulation-guide。简称/别名不自动转换（"输入P1dB"要传"IP1dB"）；移相类指标必须在对应链路给 phase_devices，衰减类必须给 atten_devices。
        threads: 指标并发线程数（正整数），默认 5。
        edi_path: EDI 安装目录，默认用固定目录（simulation_tool 的上一级）。
        ads_path: ADS 安装目录，为空时自动探测（C:/Program Files/Keysight/ADS*）。
        timeout_seconds: 最长等待秒数，默认 1800（多链路 × 多指标跑 ADS 可能超过 10 分钟；上限 3600）。

    Returns:
        {"success": True, "simulation_path": "...", "results": [...], "errors": [],
         "artifacts": [...], "report_artifacts": [{"type": "pdf"|"docx", "path": "...", "name": "..."}],
         "echo": {"indicators": [...], "links": [...]}}
        results 每项 {link, electrical_parameters:[{indicator,minimum,typical,maximum,unit}]}。
        echo 回显本次实际生效的指标名与链路字段（防止「传了 5 个指标、只跑了 3 个」的假成功）。
        部分指标失败时 success 仍为 True 但 errors 非空，必须检查。

        ⚠️ 若结果目录里存在 PDF/DOCX，它们会排在 artifacts 最前，并单独列在
        ``report_artifacts``，message 会写明「已生成仿真报告：…」。
        注意：**TR_Simulator 本身不生成报告**（只出 PNG/CSV/RAW 供独立报告工具用），
        所以正常情况下 report_artifacts 为空；需要正式报告请再调 export_simulation_report。
        混频链路不同 —— run_mixer_simulation 每次必生成 PDF/DOCX，直接交付即可。
    """
    # 1. epp 存在
    resolved_epp, err = require_file(epp_path, (".epp",))
    if err:
        return err

    # 2. links 非空数组（元素类型已由 schema 层 TrLink 校验；直接 Python 调用仍兼容 dict）
    if not isinstance(links, list) or not links:
        return error_response("NO_LINKS", "links 必须是非空数组（至少一条链路）")
    if not all(isinstance(x, (dict, TrLink)) for x in links):
        return error_response("LINK_JSON_INVALID",
                              "links 每项必须是对象（链路字段见 describe_tool(\"run_tr_simulation\")）")
    link_dicts = [_link_dict(x) for x in links]

    # 3. threads 正整数
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        return error_response("INVALID_PARAMETERS", "threads 必须是正整数")

    # 3.5 指标名 + 链路字段严格校验：exe 不接受简称/别名与表外字段，在这里拦比等 exe 报错更友好
    requested = _parse_indicators(indicators or _DEFAULT_INDICATORS)
    if err := _check_indicators(indicators or _DEFAULT_INDICATORS):
        return err
    if err := _check_links(links, requested):
        return err

    # 4. edi / ads 路径（显式传参也要规范化：混合斜杠会让 exe 回退硬编码 ADS2020）
    edi = normalize_dir(edi_path) or normalize_dir(EDI_INSTALL_DIR)
    if not Path(edi).is_dir():
        return error_response("PATH_NOT_FOUND", f"EDI 安装目录不存在: {edi}（可传 edi_path 覆盖）")
    ads = normalize_dir(ads_path) or detect_ads_path()
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
        links_json = json.dumps(link_dicts, ensure_ascii=False)
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
    partial = [r for r in (data.get("results") or []) if r]
    # returncode != 0 但 exe 已给出部分 results ⇒ 降级为「部分成功」，别把已有结果吞掉。
    partial_failure = run["returncode"] != 0 and bool(partial)
    if run["returncode"] != 0 and not partial:
        return error_response(
            "SIMULATION_EXECUTION_FAILED",
            fmt_errors(errors) or "TR 仿真失败",
            hint="先确认 EDI 已启动、目标工程已打开；单一指标失败时可减少 indicators 后重试（详见 details.tr_errors）",
            tr_errors=errors, simulation_path=data.get("simulation_path", ""),
        )

    # 8. 成功（部分指标失败时 errors 非空，须提示调用方检查）
    sim_path = data.get("simulation_path", "")
    results = convert_ep_numeric(data.get("results", []))
    artifacts, report_artifacts, truncated = collect_simulation_artifacts(
        sim_path, "run_tr_simulation", _KEY_EXT, _MAX_ARTIFACTS)

    warnings: list[str] = []
    if errors:
        warnings.append(f"有 {len(errors)} 项指标失败，仿真数据可能不完整，详见 errors 字段。")
    if partial_failure:
        warnings.append(f"exe 退出码 {run['returncode']}：有指标失败，已按「部分成功」返回 "
                        f"{len(results)} 条可用结果，失败工况诊断见 errors。")
    if truncated:
        warnings.append(f"结果目录文件较多，artifacts 只列了前 {_MAX_ARTIFACTS} 个关键文件（完整清单见 simulation_path）。")

    # 「请求了但结果里没有」的指标 → 显式告警。
    # 移相态/衰减态按 exe 文档只出曲线和 CSV、不进结果 JSON，不算缺失。
    _curve_only = {"移相态", "衰减态"}
    wanted = [i for i in requested if i not in _curve_only]
    for item in results:
        if not isinstance(item, dict):
            continue
        got = {str(p.get("indicator", "")).strip()
               for p in (item.get("electrical_parameters") or []) if isinstance(p, dict)}
        absent = [i for i in wanted if i not in got]
        if absent:
            warnings.append(
                f"链路「{item.get('link') or '未命名'}」的返回里缺少指标：{'、'.join(absent)}"
                f"（请求 {len(wanted)} 项，实际返回 {len(got & set(wanted))} 项）"
                "——先看 errors；errors 为空说明 exe 没执行该指标，别当成已测。")

    # 混频仿真每次都会在 simulation_path 顶层生成 <工程名>_仿真报告.pdf/.docx；
    # TR 本身不生成报告（目录里若有 PDF/DOCX 同样在这里暴露）。必须主动告知调用方。
    report_note = ""
    if report_artifacts:
        names = "、".join(a["name"] for a in report_artifacts)
        report_note = f"；已生成仿真报告：{names}"
        warnings.insert(0, f"本次运行已生成仿真报告（{names}），路径见 report_artifacts；"
                           f"可直接交付或存档，无需再调报告工具重跑。")

    resp: dict[str, Any] = {
        "success": True,
        "simulation_path": sim_path,
        "echo": {
            "contract": TR_CONTRACT_VERSION,
            "indicators": requested,
            "links": link_dicts,
            "threads": threads,
        },
        "results": results,
        "errors": errors,
        "artifacts": artifacts,
        "report_artifacts": report_artifacts,
        "message": (f"TR 仿真部分完成：{len(results)} 条链路结果（exe 退出码 {run['returncode']}，"
                    f"有指标失败但已返回可用结果）"
                    if partial_failure else f"TR 仿真完成：{len(results)} 条链路结果")
                   + ("（部分指标失败，详见 errors）" if errors else "")
                   + report_note,
    }
    if warnings:
        resp["warnings"] = warnings
    return resp
