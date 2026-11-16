r"""mixer_simulation.exe 包装 — 混频链路仿真（上下变频/多通道/多级寻路，15 个指标）。

命令（见仓库 servers/simulation_tool/Mixer_Simulator使用说明.md；exe 目录下没有该 md）：
    mixer_simulation.exe --project <参数表json> --out <结果目录> [--workers n] [--turbocharts-exe <路径>]
stdout 是 **GBK(cp936)** JSON：成功时单行、失败时缩进多行（内容同 response.json）；`<out>/error.json` 是 UTF-8。
退出码：0 成功 / 2 参数或工程预检失败、仿真失败（写 error.json）/ 1 参数表文件读不了（traceback）。

参数表（--project JSON）结构：{name, epp, links:[{switch_bits, defaults, indicators}]}，
网表 netlist.log 与器件库 component.json 固定从 epp 同目录读取，不在命令行传。
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from servers import mcp
from servers.simulation_tool.config import (
    ExeNeedsElevation,
    MIXER_SIMULATION_PATH,
    normalize_dir,
    run_exe,
)
from servers.eda.config import EDA_GRPC_SERVER
from servers.utils import (
    collect_simulation_artifacts,
    error_response,
    per_tool_mutex,
    prune_empty_default_dirs,
    require_file,
    result_subdir,
    tcp_port_open,
)

_logger = logging.getLogger("simulation_tool.mixer_simulation")

# 结果目录里「值得单独给出下载链接」的文件后缀 → build_artifact 的 type
# 新版 exe 不生成 PDF/DOCX 报告，也不落 conditions.json/charts.json/CSV/ads_output.log。
_KEY_EXT = {".json": "json", ".png": "image", ".raw": "raw"}
_MAX_ARTIFACTS = 60

#: 复跑同一 out_dir 时先清掉的上一轮产物（exe 只会往这几个位置写）
_STALE_ENTRIES = ("response.json", "error.json", "indicator_results", "runs", "figs")

# ── 15 指标单一事实源（与 Mixer_Simulator使用说明.md「指标」表逐行对齐）────────
# 名称/顺序以使用说明为准；写错在 MCP 层就拦并给相似名建议，别等 exe 回含糊错误。
_MIXER_INDICATORS: tuple[str, ...] = (
    "变频增益", "增益平坦度", "镜像抑制", "本振泄漏",
    "输入 P1dB", "输出 P1dB", "Psat", "噪声",
    "带内杂散", "带内杂散抑制", "带外杂散", "带外杂散抑制",
    "通道间隔离度", "单通道线性动态范围", "交调抑制",
)

#: 指标 → 该指标必须由调用方给出的字段（写在指标对象里或放进该链路 defaults 均可）
#: 依据 Mixer_Simulator使用说明.md「indicators（15 个）」表；形状未固化的（sweep/tone）只校验存在性。
_INDICATOR_REQUIRES: dict[str, tuple[str, ...]] = {
    "输入 P1dB": ("input_freq_ghz", "input_power_sweep_dbm"),
    "输出 P1dB": ("input_freq_ghz", "input_power_sweep_dbm"),
    "Psat": ("input_freq_ghz", "input_power_sweep_dbm"),
    "带内杂散": ("input_power_dbm",),
    "通道间隔离度": ("peer_link",),
    "单通道线性动态范围": ("snr_min_db",),
    "交调抑制": ("tone_frequencies_ghz", "input_power_dbm"),
}

#: 指标级字段里「形状与 defaults 不同、不能从 defaults 继承」的：
#: defaults.input_freq_ghz 是 [min,max] 区间，而指标级 input_freq_ghz 必须是标量 GHz，
#: 拿区间顶替会被 exe 打回「必须位于链路输入频率范围内」。所以这个字段只认指标对象内的值，
#: 不能走「或放进 defaults」的豁免。
_INDICATOR_LOCAL_ONLY: frozenset[str] = frozenset({"input_freq_ghz", "peer_link"})
#: - input_freq_ghz：形状与 defaults 不同（标量 vs 区间）
#: - peer_link：各链路的「受害方」不同，放 defaults 会被所有链路共用（还能指向自己）


def _norm_name(s: Any) -> str:
    """指标名归一化：去空白 + 小写（吃 `输入P1dB` / `PSAT` / `输入 P1dB ` 这类写法差异）。"""
    return "".join(str(s).split()).lower()


#: 归一化指标名 → 15 项标准名
_INDICATOR_LOOKUP: dict[str, str] = {_norm_name(n): n for n in _MIXER_INDICATORS}

#: defaults 必填字段（使用说明「defaults」表）
_DEFAULTS_REQUIRED: tuple[str, ...] = ("in_port", "out_port", "input_freq_ghz", "input_power_dbm", "lo")


def _is_num(v: Any) -> bool:
    """有限数值（排除 bool、NaN、Inf）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _range_reason(value: Any) -> str:
    """校验 [min, max] 形式：合法返回空串，否则返回原因（**不含字段名**，由调用方拼 location）。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_is_num(v) for v in value):
        return f"必须是 [min, max] 两个数值（当前 {value!r}）"
    if value[0] > value[1]:
        return f"min 不能大于 max（当前 {value[0]} > {value[1]}）"
    return ""


def _missing_field(where: str, field: str) -> dict:
    """参数表缺字段 → LINK_MISSING_FIELD（点名补哪个）。"""
    resp = error_response(
        "LINK_MISSING_FIELD", f"{where} 缺少必填字段 {field!r}",
        hint=f"按 Mixer_Simulator使用说明.md 的参数表章节，在 {where} 里补 {field!r}",
    )
    resp["missing_field"] = field
    return resp


def _invalid(where: str, reason: str) -> dict:
    """参数表结构不合法 → LINK_JSON_INVALID（带定位）。"""
    resp = error_response(
        "LINK_JSON_INVALID", f"{where} {reason}",
        hint="按 Mixer_Simulator使用说明.md 的参数表章节逐项核对（links[i].defaults / switch_bits / indicators）",
    )
    resp["location"] = where
    return resp


def _aggregate(errors: list[dict]) -> dict | None:
    """多个预检错误 → 顶层取第一条 + error_count/all_errors（避免为 N 个错跑 N 轮）。"""
    if not errors:
        return None
    head = dict(errors[0])
    if len(errors) > 1:
        head["message"] = f"{head.get('message', '')}（另有 {len(errors) - 1} 处问题，见 all_errors）"
    head["error_count"] = len(errors)
    head["all_errors"] = errors
    return head


def _check_project(project: dict) -> dict | None:
    """按使用说明书校验参数表：defaults / switch_bits / lo / indicators（含必填字段与 peer_link 形状）。

    只拦「exe 报错也说不清怎么改」的项：结构、必填字段、指标名、peer_link 下标范围。
    扫频/单音等形状未固化的字段（frequency_sweep_ghz、noise_sweep_ghz、tone_frequencies_ghz）
    只校验存在性，形状交给 exe。

    **一次收集全部问题**：顶层 error_code / message 取第一条（与旧版单错行为兼容），
    另带 all_errors[] 列出全部，避免调用方为 N 个错跑 N 轮。

    副作用：指标名归一化后**就地写回** `ind["name"]`（`输入P1dB` → `输入 P1dB`），
    保证传给 exe 的是 15 项标准名。
    """
    import difflib

    errors: list[dict] = []

    # links 非空/类型由 run_mixer_simulation 入口先拦（NO_LINKS / LINK_JSON_INVALID），此处只做遍历
    links = project.get("links") or []
    for i, link in enumerate(links):
        label = f"links[{i}]"
        defaults = link.get("defaults")
        if not isinstance(defaults, dict):
            errors.append(_invalid(f"{label}.defaults", "必须是对象（该链路公共工况）"))
            continue

        # ---- defaults 必填字段与基本形状（使用说明「defaults」「lo」表）----
        missing_defaults = [f for f in _DEFAULTS_REQUIRED if f not in defaults]
        for field in missing_defaults:
            errors.append(_missing_field(f"{label}.defaults", field))
        if not missing_defaults:
            for field in ("in_port", "out_port"):
                v = defaults[field]
                if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                    errors.append(_invalid(f"{label}.defaults.{field}",
                                           f"必须是 >=1 的整数端口号（当前 {v!r}）"))
            if isinstance(defaults["in_port"], int) and isinstance(defaults["out_port"], int) \
                    and defaults["in_port"] == defaults["out_port"]:
                errors.append(_invalid(f"{label}.defaults.out_port",
                                       f"不能与 in_port 相同（当前都是 {defaults['in_port']}）"))
            if reason := _range_reason(defaults["input_freq_ghz"]):
                errors.append(_invalid(f"{label}.defaults.input_freq_ghz", reason))
            if not _is_num(defaults["input_power_dbm"]):
                errors.append(_invalid(f"{label}.defaults.input_power_dbm",
                                       f"必须是数值 dBm（当前 {defaults['input_power_dbm']!r}）"))
            lo = defaults["lo"]
            if not isinstance(lo, list) or not lo:
                errors.append(_invalid(f"{label}.defaults.lo",
                                       "必须是非空数组（按寻路得到的混频器顺序一一配对）"))
            else:
                for j, item in enumerate(lo):
                    loc = f"{label}.defaults.lo[{j}]"
                    if not isinstance(item, dict):
                        errors.append(_invalid(loc, "必须是对象 {freq_ghz, power_dbm, output_bw_ghz?}"))
                        continue
                    for field in ("freq_ghz", "power_dbm"):
                        if field not in item:
                            errors.append(_missing_field(loc, field))
                    if "freq_ghz" in item and (reason := _range_reason(item["freq_ghz"])):
                        errors.append(_invalid(f"{loc}.freq_ghz", reason))
                    if "power_dbm" in item and not _is_num(item["power_dbm"]):
                        errors.append(_invalid(f"{loc}.power_dbm",
                                               f"必须是数值 dBm（当前 {item['power_dbm']!r}）"))
                    bw = item.get("output_bw_ghz")
                    if bw is not None and (not _is_num(bw) or bw < 0):
                        errors.append(_invalid(f"{loc}.output_bw_ghz", f"必须是非负数 GHz（当前 {bw!r}）"))

        # ---- switch_bits：instance + 只含 0/1 的 bits ----
        switch_bits = link.get("switch_bits")
        if switch_bits is not None:
            if not isinstance(switch_bits, list):
                errors.append(_invalid(f"{label}.switch_bits", "必须是数组（无开关可省略）"))
            else:
                for j, item in enumerate(switch_bits):
                    loc = f"{label}.switch_bits[{j}]"
                    if not isinstance(item, dict) or "instance" not in item or "bits" not in item:
                        errors.append(_invalid(loc, "必须是对象 {instance, bits}"))
                        continue
                    if not str(item["instance"]).strip():
                        errors.append(_invalid(f"{loc}.instance", "不能为空（开关实例名）"))
                    bits = str(item["bits"])
                    if not bits or set(bits) - {"0", "1"}:
                        errors.append(_invalid(f"{loc}.bits", f"只能含 0/1 的 Bits 字符串（当前 {bits!r}）"))

        # ---- indicators：名称（归一化）+ 必填字段 + peer_link 形状 ----
        indicators = link.get("indicators")
        if indicators is None:
            indicators = []
        if not isinstance(indicators, list):
            errors.append(_invalid(f"{label}.indicators",
                                   f"必须是数组（当前 {indicators!r}），受害链路可为空数组"))
            continue
        for j, ind in enumerate(indicators):
            where = f"{label}.indicators[{j}]"
            if not isinstance(ind, dict) or not ind.get("name"):
                errors.append(error_response(
                    "LINK_JSON_INVALID",
                    f"{where} 必须是含 name 的对象（受害链路可为空数组）",
                    hint="按 Mixer_Simulator使用说明.md：{\"name\": \"<指标名>\", ...该指标字段}"))
                continue
            raw_name = str(ind["name"])
            name = _INDICATOR_LOOKUP.get(_norm_name(raw_name))
            if name is None:
                close = difflib.get_close_matches(_norm_name(raw_name), list(_INDICATOR_LOOKUP), n=1, cutoff=0.6)
                suggest = f"，是否想传 {_INDICATOR_LOOKUP[close[0]]!r}？" if close else ""
                resp = error_response(
                    "UNKNOWN_INDICATOR",
                    f"{where} 的指标名 {raw_name!r} 不存在{suggest}",
                    hint=f"合法指标共 {len(_MIXER_INDICATORS)} 个：{'、'.join(_MIXER_INDICATORS)}",
                )
                if close:
                    resp["did_you_mean"] = _INDICATOR_LOOKUP[close[0]]
                resp["valid_indicators"] = list(_MIXER_INDICATORS)
                errors.append(resp)
                continue
            if name != raw_name:      # 归一化命中（如 `输入P1dB`）：写回标准名，别让 exe 再拒一次
                ind["name"] = name
                _logger.info("指标名归一化: %r -> %r (%s)", raw_name, name, where)

            for field in _INDICATOR_REQUIRES.get(name, ()):
                local_only = field in _INDICATOR_LOCAL_ONLY
                if field in ind:
                    continue
                if not local_only and field in defaults:
                    continue
                resp = _missing_field(f"{label} 的指标 {name!r}", field)
                if field == "input_freq_ghz":
                    resp["message"] += "（指标级必须是标量 GHz，defaults 里的 [min,max] 不能替代）"
                    resp["hint"] = (f"在 {where} 里补 \"input_freq_ghz\": <数值 GHz>，"
                                    f"如 35.0；写区间或扫参对象会被 exe 打回")
                elif field == "peer_link":
                    resp["message"] += "（必须写在该指标对象里，不能放 defaults —— 各链路的受害方不同）"
                    resp["hint"] = (f"在 {where} 里补 \"peer_link\": {{\"link_index\": <受害链路下标>}}"
                                    "（对象，下标 0 基）")
                else:
                    resp["message"] += "（写在指标对象里，或放进该链路 defaults）"
                errors.append(resp)

            if "input_freq_ghz" in ind and not _is_num(ind["input_freq_ghz"]):
                errors.append(_invalid(f"{where}.input_freq_ghz",
                                       f"必须是标量 GHz 数值（当前 {ind['input_freq_ghz']!r}）；"
                                       f"区间 [min,max] 或 {{start,stop,step}} 都不合法"))

            if name == "通道间隔离度":
                peer = ind.get("peer_link")
                if not isinstance(peer, dict) or not isinstance(peer.get("link_index"), int) \
                        or isinstance(peer.get("link_index"), bool):
                    errors.append(_invalid(f"{where}.peer_link",
                                           "必须是对象 {link_index: <受害链路下标>}"))
                else:
                    idx = peer["link_index"]
                    if not 0 <= idx < len(links):
                        errors.append(_invalid(
                            f"{where}.peer_link.link_index",
                            f"下标超出范围（当前 {idx}，合法 0..{len(links) - 1}；0 基）"))
                    elif idx == i:
                        errors.append(_invalid(
                            f"{where}.peer_link.link_index",
                            f"不能指向本链路（当前 {idx}）；受害链路必须是另一条"))

    return _aggregate(errors)


# ── 网表一致性预检（端口 / 开关实例 / LO 数量）───────────────────────────────
#: 网表里出现即算「混频器」的器件类型
_MIXER_TYPES = ("TableMixer", "MixerDevice", "Mixer2", "Mixer")

#: component.json 的 type 命中这些词才算「开关类」器件（只用于 hint 排序，**不用于放行判断**）
_SWITCH_TYPE_HINTS = ("SP1T", "SP2T", "SP3T", "SP4T", "SPDT", "SPST", "开关", "SWITCH")


def _read_component_types(epp_dir: Path) -> dict[str, str]:
    """component.json: model → type（如 NC16103C_3040 → SP1T）。读不到返回空 dict。"""
    try:
        raw = json.loads((epp_dir / "component.json").read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}
    types: dict[str, str] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("model"):
                types[str(item["model"])] = str(item.get("type") or "")
    return types


def _read_netlist_facts(netlist_path: Path) -> dict[str, Any]:
    """从 netlist.log 抽端口号、带 Bits 的实例名、混频器数量；读不了返回空 dict（不拦）。

    `switches` 是**所有**带 `Bits=` 的实例，放行判断用它 —— 这个工程里衰减器
    （NC1314C_108A `Bits="00000"`、WSDQ300400 一位衰减）也是靠 Bits 控制的，
    收窄成「只认开关类」会把合法用法判死。`switch_like` 只是给 hint 用的真·开关子集。
    """
    try:
        text = netlist_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    comp_types = _read_component_types(netlist_path.parent)
    ports: set[int] = set()
    switches: set[str] = set()
    switch_like: set[str] = set()
    mixers = 0
    for line in text.splitlines():
        if line.startswith("Port:"):
            m = re.search(r"\bNum=(\d+)", line)
            if m:
                ports.add(int(m.group(1)))
            continue
        m = re.match(r"(\w+):(\S+)\s", line)
        if not m:
            continue
        dev_type, inst = m.group(1), m.group(2)
        if dev_type in _MIXER_TYPES:
            mixers += 1
        if ' Bits="' in line:
            switches.add(inst)
            mm = re.search(r'Model="([^"]+)"', line)
            model_type = comp_types.get(mm.group(1), "").upper() if mm else ""
            if any(h in model_type for h in _SWITCH_TYPE_HINTS):
                switch_like.add(inst)
    return {"ports": ports, "switches": switches, "switch_like": switch_like, "mixers": mixers}


def _check_netlist_consistency(project: dict, netlist_path: Path) -> dict | None:
    """端口 / 开关实例 / LO 数量与网表对不上一律提前拦。

    这三类错误 exe 侧表现很差：端口不存在会让 exe 直接崩（只有 traceback），
    开关实例名写错会被当成「没配开关」而默认全导通（结果看起来正常但是错的）。
    """
    facts = _read_netlist_facts(netlist_path)
    if not facts:
        return None
    errors: list[dict] = []
    for i, link in enumerate(project.get("links") or []):
        defaults = link.get("defaults") or {}
        for field in ("in_port", "out_port"):
            p = defaults.get(field)
            if facts["ports"] and p not in facts["ports"]:
                errors.append(error_response(
                    "PORT_NOT_FOUND",
                    f"links[{i}].defaults.{field}={p} 不在网表端口里",
                    hint=f"网表可用端口号：{sorted(facts['ports'])}（取自 Port 行的 Num= 值）",
                ))
        for j, item in enumerate(link.get("switch_bits") or []):
            inst = str(item.get("instance", "")).strip()
            if facts["switches"] and inst not in facts["switches"]:
                switch_like = facts.get("switch_like") or set()
                pool = sorted(switch_like) if switch_like else sorted(facts["switches"])
                non_switch = len(facts["switches"]) - len(switch_like) if switch_like else 0
                shown = pool[:20]
                more = f"（共 {len(pool)} 个，只列前 20）" if len(pool) > 20 else ""
                kind = "开关类实例" if switch_like else "带 Bits= 的实例"
                errors.append(error_response(
                    "SWITCH_NOT_FOUND",
                    f"links[{i}].switch_bits[{j}].instance={inst!r} 不在网表带 Bits 的实例里",
                    hint=f"网表里的{kind}：{shown}{more}（另有 {non_switch} 个带 Bits 的非开关器件，"
                         "如衰减器，也可用 switch_bits 控制）；"
                         "实例名写错会**沿用网表 Bits=**（这类工程常是断路，整条链路 ≈0 dB，"
                         "结果看起来正常但其实是错的）",
                ))
        lo = defaults.get("lo") or []
        if facts["mixers"] and len(lo) > facts["mixers"]:
            errors.append(error_response(
                "LO_COUNT_MISMATCH",
                f"links[{i}].defaults.lo 给了 {len(lo)} 个本振，但网表里只有 {facts['mixers']} 个混频器",
                hint="lo[] 按寻路得到的混频器顺序一一配对，多给的会被忽略；请核对数量",
            ))
    return _aggregate(errors)


@mcp.tool()
@per_tool_mutex
@prune_empty_default_dirs
def run_mixer_simulation(
    project: dict,
    out_dir: str = "",
    workers: int = 10,
    turbocharts_exe: str = "",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """执行混频链路仿真（含混频器、LO 本振、上下变频，15 个指标）。不含变频的纯链路请用 run_tr_simulation。

    用法："仿真这个混频工程的下变频链路，提取变频增益、镜像抑制、本振泄漏、噪声"

    前提：Windows + EDI 已运行（gRPC 127.0.0.1:50055 可用）；网表 netlist.log 与器件库
    component.json 必须在 epp 同目录（工具会先查这两份文件，缺了直接点名，不启动 exe）。

    project 是参数表 JSON（{name, epp, links}），结构见 servers/simulation_tool/Mixer_Simulator使用说明.md：
      links[i] = {switch_bits?, defaults, indicators}；defaults 必含 {in_port, out_port,
      input_freq_ghz:[min,max], input_power_dbm, lo:[{freq_ghz:[min,max], power_dbm, output_bw_ghz?}]}；
      indicators 每项带 name 及该指标字段（指标级字段写在指标里或放进该链路 defaults 均可，
      例外：指标级 input_freq_ghz 只认指标对象内的标量 GHz，不能用 defaults 的 [min,max] 顶替）。
    15 个合法指标名：变频增益, 增益平坦度, 镜像抑制, 本振泄漏, 输入 P1dB, 输出 P1dB, Psat, 噪声,
    带内杂散, 带内杂散抑制, 带外杂散, 带外杂散抑制, 通道间隔离度, 单通道线性动态范围, 交调抑制。
    指标名做归一化（去空白 + 忽略大小写），`输入P1dB` / `PSAT` 会自动纠正成标准名并写回参数表。
    必填字段：输入 P1dB / 输出 P1dB / Psat **各自都要写三项**
      input_freq_ghz（标量 GHz）+ input_power_sweep_dbm（{start,stop,step} dBm）+ orders（如 {FREQ:5, LO:5}）；
      后两者只复用输入 P1dB 的 RAW，字段不继承；
    带内杂散需 input_power_dbm；通道间隔离度需 peer_link 对象且下标 0 基（{"link_index": <受害链路下标>}）；
    单通道线性动态范围需 snr_min_db；交调抑制需 tone_frequencies_ghz + input_power_dbm + orders 的 FREQ/FREQ2/LO。
    参数表在调用 exe 前就会校验（结构/必填字段/指标名/peer_link 形状），**一次收集全部问题**：
    顶层 error_code/message 取第一条，另带 error_count 与 all_errors[]（含 location / missing_field）。
    已知限制（exe 侧，详见使用说明「已知问题」）：out_port 不是第一个输出端口时该链路指标取数错误，
    多通道工程目前只信首通道（out_port=2）；输入 P1dB 建议与 变频增益 放同一次请求。
    结果直接读返回的 indicators / artifacts，**新版不生成 PDF/DOCX 报告**，别再去翻报告。

    Args:
        project: 参数表 dict，必含 name（工程名）、epp（.epp 绝对路径）、links（链路数组，≥1）。
            详见 Mixer_Simulator使用说明.md 的参数表章节。
        out_dir: 结果目录；留空则落到默认产物目录下的子目录（<result_dir>/run_mixer_simulation_<时间戳>/）。response.json / indicator_results / runs / figs 都落这里。
        workers: 并发进程数（正整数），默认 10；EDI 不稳定时降到 5。
        turbocharts_exe: turbocharts_app.exe 路径，默认空 = 用 MCP 自动探测的 turbocharts（从 exe 往上找 EDI 根）。
        timeout_seconds: 最长等待秒数，默认 1800（上限 3600）。

    Returns:
        {"success": True, "out_dir": "...", "status": "ok", "condition_count": N,
         "indicators": [{indicator, status, unit, value|min|max|points, reason?}],
         "failures": [], "artifacts": [...]}
        indicators 是本次请求的指标摘要（≤15 个，未请求的不出现）；单值指标带 value、扫频指标带 min/max/points；
        status=incomplete 的指标带 reason（如「数值底」）。failures 为空表示成功。
    """
    # 1. project 参数表 + epp 校验
    if not isinstance(project, dict) or not project:
        return error_response("INVALID_PARAMETERS",
                              "project 必须是非空对象（{name, epp, links}），见 Mixer_Simulator使用说明.md")
    epp = project.get("epp")
    if not isinstance(epp, str) or not epp.strip():
        return error_response("INVALID_PARAMETERS", "project.epp 必须是 .epp 绝对路径字符串")
    resolved_epp, err = require_file(epp, (".epp",))
    if err:
        return err
    links = project.get("links")
    if not isinstance(links, list) or not links:
        return error_response("NO_LINKS", "project.links 必须是非空数组（至少一条链路）",
                              hint="至少给一条链路：{switch_bits?, defaults, indicators}，见使用说明「链路级」")
    if not all(isinstance(x, dict) for x in links):
        return error_response("LINK_JSON_INVALID", "project.links 每项必须是对象（含 defaults/indicators）")
    if err := _check_project(project):
        return err

    # 1b. 工程布局预检（使用说明「前置条件」）：netlist.log / component.json 必须在 epp 同目录，
    #     exe 固定从这里读、没有 CLI 覆盖；提前拦掉可省一次子进程启动与含糊的失败。
    epp_dir = Path(resolved_epp).parent
    for fname in ("netlist.log", "component.json"):
        if not (epp_dir / fname).is_file():
            return error_response(
                "FILE_NOT_FOUND",
                f"{epp_dir} 下缺少 {fname}（exe 固定从 epp 同目录读网表与器件库，无 CLI 覆盖）",
                hint="在 EDI 里导出工程后把 netlist.log 与 component.json 放到 .epp 同目录；"
                     "网表也可用 export_project_netlist 生成",
                expected_path=str(epp_dir / fname))

    # 1c. 网表一致性：端口 / 开关实例 / LO 数量（对不上就别启动 exe）
    if err := _check_netlist_consistency(project, epp_dir / "netlist.log"):
        return err

    # 2. out_dir / workers 校验
    if not isinstance(out_dir, str) or not out_dir.strip():
        out_dir = result_subdir("run_mixer_simulation")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        return error_response("INVALID_PARAMETERS", "workers 必须是正整数")

    # 3. turbocharts 覆盖路径：显式给了就校验存在；没给用 MCP 自动探测的 TURBOCHARTS_PATH
    #    （走 _find_edi_root 往上找），别交给 exe 的硬编码 C:\Program Files (x86)\EDI 兜底。
    if turbocharts_exe:
        tcx = normalize_dir(turbocharts_exe)
        if not Path(tcx).is_file():
            return error_response("PATH_NOT_FOUND", f"turbocharts_exe 不存在: {tcx}")
    else:
        from servers.eda.config import TURBOCHARTS_PATH  # noqa: E402 惰性导入，避免 eda.config↔simulation_tool 循环
        tcx = normalize_dir(TURBOCHARTS_PATH)

    # 4. 前置预检：EDI gRPC 必须就绪（exe 固定连 127.0.0.1:50055 取网表/跑仿真）
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
            f"EDI 的 gRPC 服务未就绪（{EDA_GRPC_SERVER} 连不上），混频仿真需要从 EDI 取网表，请先启动 EDI 软件",
            hint="启动 EDI（或先用 launch_edi）并确认 /ready 的 grpc 为 online 后重试")

    # 5. 参数表写临时 JSON（--project 要的是文件路径，不是内联 JSON）
    tmp_dir = tempfile.mkdtemp(prefix="edi_mixer_")
    project_file = Path(tmp_dir) / "project.json"
    try:
        project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    except (TypeError, ValueError) as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return error_response("LINK_JSON_INVALID", f"project 含无法 JSON 序列化的值: {exc}")

    # 6. 构造命令并运行
    args = ["--project", str(project_file), "--out", out_dir, "--workers", str(workers)]
    if tcx:
        args += ["--turbocharts-exe", tcx]

    # 5b. 复跑同一 out_dir 时先清掉上一轮产物（response/error + indicator_results/runs/figs）。
    #     只清这几个已知位置，不动用户自己放进来的东西；否则 collect_simulation_artifacts
    #     的 rglob 会把上一轮的指标 JSON 当成本次结果，噪声自检也会读到旧文件误报。
    out_path = Path(out_dir)
    if out_path.is_dir():
        for stale in _STALE_ENTRIES:
            target = out_path / stale
            try:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        run = run_exe(MIXER_SIMULATION_PATH, args, timeout_seconds)
    except ExeNeedsElevation as exc:
        return error_response(
            "EXE_NEEDS_ELEVATION", str(exc), retryable=True,
            exe=MIXER_SIMULATION_PATH, winerror=740,
            hint="以管理员身份重启 EDI MCP 服务后重试；服务未提权时 Windows 直接拒绝启动该 exe，不会弹 UAC")
    except RuntimeError as exc:
        return error_response("TOOL_TIMEOUT", str(exc), retryable=True,
                              hint="链路/指标多时把 timeout_seconds 调大（上限 3600）后重试")
    except ValueError as exc:
        return error_response("INVALID_PARAMETERS", str(exc))
    except FileNotFoundError as exc:
        return error_response("EXE_NOT_FOUND", str(exc), mixer_simulation_path=MIXER_SIMULATION_PATH,
                              hint="确认 EDI 安装目录下 simulation_tool/mixer_simulation/mixer_simulation.exe 存在")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # 7. 解析 response.json
    data = run["json"]
    if data is None:
        return error_response(
            "SIMULATION_EXECUTION_FAILED",
            run["json_error"] or "exe 未输出合法 JSON",
            stdout=run["stdout"], stderr=run["stderr"], return_code=run["returncode"],
        )
    if not isinstance(data, dict):
        return error_response("SIMULATION_EXECUTION_FAILED", "exe 返回格式异常", stdout=run["stdout"])

    failures = data.get("failures") or []
    status = data.get("status", "")

    # 8. 失败判定：退出码非 0，或 status 明确为 failed
    if run["returncode"] != 0 or status == "failed":
        out_path = Path(out_dir)
        error_tail = ""
        if out_path.is_dir():
            err_file = out_path / "error.json"
            if err_file.is_file():
                try:
                    error_tail = err_file.read_text(encoding="utf-8", errors="replace")[:2000]
                except OSError:
                    error_tail = ""
        # 优先用结构化 errors[]（exe 回在 stdout 或 error.json 里）把 location 透传出去
        err_items = data.get("errors") or []
        if not err_items and error_tail:
            try:
                err_items = (json.loads(error_tail) or {}).get("errors") or []
            except (json.JSONDecodeError, AttributeError):
                err_items = []
        fail_msg = _failure_message(data, failures, err_items, error_tail)
        resp = error_response(
            "SIMULATION_EXECUTION_FAILED",
            fail_msg,
            hint=("先看 failures 里各工况的 error（或 error.json）——器件/模型问题通常不可重试；"
                  "其次确认 EDI 已打开目标工程、component.json 存在、ADS 许可可用"),
            mixer_failures=failures, out_dir=out_dir, return_code=run["returncode"],
        )
        if err_items:
            resp["errors"] = err_items
        return resp

    # 9. 成功
    artifacts, _report_artifacts, truncated = collect_simulation_artifacts(
        out_dir, "run_mixer_simulation", _KEY_EXT, _MAX_ARTIFACTS)

    warnings: list[str] = []
    if failures:
        warnings.append(f"有 {len(failures)} 个工况失败，详见 failures 字段。")
    if truncated:
        warnings.append(f"结果目录文件较多，artifacts 只列了前 {_MAX_ARTIFACTS} 个关键文件（完整清单见 out_dir）。")
    if ssb_warn := _noise_ssb_warning(out_dir):
        warnings.append(ssb_warn)
    if imd_warn := _imd_negative_warning(data.get("indicators") or []):
        warnings.append(imd_warn)

    resp: dict[str, Any] = {
        "success": True,
        "out_dir": out_dir,
        "status": status or "ok",
        "condition_count": data.get("condition_count"),
        "indicators": data.get("indicators", []),
        "failures": failures,
        "artifacts": artifacts,
        "message": f"混频仿真完成：{data.get('condition_count', 0)} 个工况、{len(data.get('indicators', []))} 项指标"
                   + ("（有工况失败，详见 failures）" if failures else ""),
    }
    if warnings:
        resp["warnings"] = warnings
    return resp


def _failure_message(data: dict, failures: list, err_items: list, error_tail: str) -> str:
    """失败原因按可信度择优：errors[] > failures[] > error.json 原文 > 空工况推断 > 兜底。

    exe 对「频率规划推不出工况」不给 errors（实测 input_freq=[50,55] 时只回
    status=failed + condition_count=0），这时补一句可操作的判断，别只丢「混频仿真失败」。
    """
    if err_items:
        return _fmt_errors(err_items)
    if failures:
        return _fmt_failures(failures)
    if error_tail:
        return error_tail[:500]
    if isinstance(data, dict) and data.get("condition_count") == 0:
        return ("工况矩阵为空（condition_count=0）：频率规划没推出任何工况 —— "
                "通常是 input_freq_ghz 与 lo 的组合超出器件/滤波器范围，或 out_port 频段对不上")
    return "混频仿真失败（exe 未给出 errors/failures 明细）"


def _fmt_errors(errors: list) -> str:
    """把 exe 的 errors[{location, reason}] 拼成「字段: 原因」，最多 3 条。"""
    parts: list[str] = []
    for e in errors[:3]:
        if isinstance(e, dict):
            parts.append(f"{e.get('location', '?')}: {e.get('reason', '?')}")
        else:
            parts.append(str(e))
    tail = f"（另有 {len(errors) - 3} 条）" if len(errors) > 3 else ""
    return "；".join(parts) + tail


def _noise_ssb_warning(out_dir: str) -> str:
    """噪声 SSB 在带边可能是数值底（实测 3.70 GHz 出 437 dB），提示改用 DSB。"""
    for f in (Path(out_dir) / "indicator_results").glob("*噪声*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pts = data.get("per_point") or []
        bad = [p for p in pts if isinstance(p, dict)
               and isinstance(p.get("nfssb"), (int, float)) and p["nfssb"] > 100]
        if bad:
            worst = max(bad, key=lambda p: p["nfssb"])
            return (f"噪声 SSB 有 {len(bad)} 个频点数值异常（最大 {worst['nfssb']:.0f} dB @ "
                    f"{worst.get('noisefreq_ghz')} GHz），属数值底；看噪声请用 DSB（value_dsb / nfdsb）。")
    return ""


def _imd_negative_warning(indicators: list) -> str:
    """交调抑制为负值说明双音已把链路推入压缩，抑制比没有物理意义。"""
    for ind in indicators:
        if isinstance(ind, dict) and ind.get("indicator") == "交调抑制":
            v = ind.get("value")
            if isinstance(v, (int, float)) and v < 0:
                return ("交调抑制为负值：双音功率已把链路推入压缩，抑制比失去物理意义；"
                        "把 input_power_dbm 降到 −40 dBm 以下（或调小音间隔）后重测。")
    return ""


def _fmt_failures(failures: list) -> str:
    """把 failures 列表拼成可读字符串（最多前 3 条）。"""
    if not failures:
        return ""
    parts = []
    for f in failures[:3]:
        if isinstance(f, dict):
            ind = f.get("indicator", "")
            err = f.get("error") or f.get("message") or ""
            parts.append(f"[{ind}] {err}" if ind else str(err))
        else:
            parts.append(str(f))
    text = "；".join(p for p in parts if p)
    if len(failures) > 3:
        text += f"（共 {len(failures)} 条）"
    return text
