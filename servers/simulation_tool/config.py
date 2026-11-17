"""独立仿真工具 — 固定目录 exe 路径与子进程执行器。

两个 exe 固定在 EDI 安装目录的 simulation_tool 子目录下（settings.simulation_tool_dir），
stdout 均为单行 UTF-8 JSON、退出码 0/1，比 turbocharts 简单：判成败直接看退出码 +
JSON 里的 success/errors，不需要靠 stderr 措辞反推。
"""

from __future__ import annotations

import configparser
import glob
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from servers.settings import get_settings

_logger = logging.getLogger("simulation_tool")

_settings = get_settings()


def _find_edi_root() -> str:
    """打包（frozen）下从 exe 目录往上找 EDI 安装根（含 simulation_tool 子目录）。

    打包后 exe 部署在 EDI 根的任意深度（如 EDI\\edi-agent\\service\\optional-mcps\\edi-mcp），
    往上逐级找 simulation_tool 目录即可定位 EDI 根，不必硬编码 C:\\Program Files (x86)\\EDI。
    只在 frozen 下启用：源码态仓库自身就有 servers/simulation_tool 目录会误判，
    故源码态返回空串，交给调用方回退硬编码。
    """
    if not getattr(sys, "frozen", False):
        return ""
    start = Path(sys.executable).resolve().parent
    for d in (start, *start.parents):
        if (d / "simulation_tool").is_dir():
            return str(d)
    return ""


_HARDCODED_EDI_ROOT = r"C:\Program Files (x86)\EDI"

_configured = (_settings.simulation_tool_dir or "").strip()
if _configured:
    _SIM_TOOL_DIR = Path(_configured)
    EDI_INSTALL_DIR = str(_SIM_TOOL_DIR.parent)
elif (_settings.edi_path or "").strip():
    # 从 EDI_PATH（EDI.exe 路径）反推 EDI 安装根 —— 重装到非默认目录（如 D:\EDI）时，
    # 不再依赖硬编码 C:\Program Files (x86)\EDI（2026-11-16 重装后自动探测失灵）。
    EDI_INSTALL_DIR = str(Path(_settings.edi_path).expanduser().parent)
    _SIM_TOOL_DIR = Path(EDI_INSTALL_DIR) / "simulation_tool"
else:
    # 自动检测：往上找 EDI 根，找不到回退硬编码
    _root = _find_edi_root() or _HARDCODED_EDI_ROOT
    EDI_INSTALL_DIR = _root
    _SIM_TOOL_DIR = Path(_root) / "simulation_tool"

REPORT_EXPORTER_PATH = str(_SIM_TOOL_DIR / "simulation_report" / "Report_Exporter.exe")
DRAWTOOL_PATH = str(_SIM_TOOL_DIR / "simulation_report" / "drawtool.exe")
TR_SIMULATOR_PATH = str(_SIM_TOOL_DIR / "tr_simulation" / "TR_Simulator.exe")
NETLIST_PATHFINDER_PATH = str(_SIM_TOOL_DIR / "tr_simulation" / "Netlist_Pathfinder.exe")
MIXER_SIMULATION_PATH = str(_SIM_TOOL_DIR / "mixer_simulation" / "mixer_simulation.exe")

# ADS 安装目录兜底候选（最后一级：显式 ads_path / EDI_ADS_PATH / config.ini 都拿不到时才用）
_ADS_GLOB_CANDIDATES = (
    "C:/Program Files/Keysight/ADS*",
    "D:/Program Files/Keysight/ADS*",
)

# 运维级覆盖：环境变量优先于 config.ini 与 glob（显式 ads_path 工具参数仍最高）
_ADS_ENV = "EDI_ADS_PATH"


def _decode_text(raw: bytes) -> str:
    """解码 exe 输出。JSON 约定 UTF-8；UTF-8 解出替换字符说明实为 GBK，退回 cp936。"""
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    text = raw.decode("utf-8", errors="replace")
    if "�" in text:
        return raw.decode("cp936", errors="replace")
    return text


def normalize_dir(path: str) -> str:
    """把目录路径规范化成 exe 认的形式（Windows 混合斜杠 → 统一分隔符）。

    ⚠️ 不只是自动探测结果需要规范化 —— **显式传参同样会踩坑**：
    agent 很容易拼出 `C:/Program Files\\Keysight/ADS2020_update2` 这种混合斜杠，
    mixer_simulation.exe / TR_Simulator.exe 解析失败后会**回退到硬编码
    `C:/Program Files/Keysight/ADS2020`**，于是报「Can not find ...ADS2020」
    （实际装的是 ADS2020_update2）。空串原样返回，交给调用方决定回退策略。
    """
    from pathlib import Path

    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw))
    except (OSError, ValueError):
        return raw


def _ads_version_key(path: str) -> tuple[int, int]:
    """ADS 目录名 → 可比较的版本号（ADS2026_Update1 → (2026, 1)）。

    多版本并存时用 max(key=...) 取最高版本。解析不出（如自建目录 ADS_custom）返回
    (0, 0)：垫底但仍可能被选中，比返回空串让上层报错好。
    """
    m = re.match(r"ADS(\d{2,4})(?:_?update(\d+))?", Path(path).name, re.IGNORECASE)
    if not m:
        return (0, 0)
    return (int(m.group(1)), int(m.group(2) or 0))


def _ads_from_edi_config() -> str:
    """读 EDI 安装目录 config/config.ini 的 [ads] hpeesof_dir（即 EDI GUI 里配的 ADS）。

    2026-11-10 事故：config.ini 写 ADS2026、MCP 却一直传 ADS2020 —— 因为这里原本不读
    config.ini，探测只看 glob 且取字典序最小。读同一份配置让 GUI 与 MCP 有单一事实源。

    配置缺失 / 解析失败 / 指向不存在的目录一律返回空串（交给 glob 兜底），绝不抛错：
    客户机上有人把 hpeesof_dir 改成装坏或被删的版本时，MCP 不该跟着一起坏。
    """
    try:
        raw = (Path(EDI_INSTALL_DIR) / "config" / "config.ini").read_bytes()
    except OSError:
        return ""
    parser = configparser.ConfigParser(interpolation=None)  # token 里有 % 也不能炸
    try:
        parser.read_string(_decode_text(raw))
        value = normalize_dir(parser.get("ads", "hpeesof_dir", fallback=""))
    except configparser.Error:
        return ""
    return value if value and Path(value).is_dir() else ""


def detect_ads_path() -> str:
    """探测 ADS 安装目录，找不到返回空串。

    取值优先级（自高到低；工具参数 ads_path 在调用侧比本函数更优先）：
      1. 环境变量 EDI_ADS_PATH   —— 运维临时覆盖，不改文件；
      2. EDI 的 config/config.ini `[ads] hpeesof_dir` —— 与 EDI GUI 同一事实源；
      3. glob `C:/Program Files/Keysight/ADS*`、`D:/Program Files/Keysight/ADS*`，
         多个命中取**版本号最高**的那个。
    第 3 级原来写的是 `sorted(hits)[0]`（字典序最小）：机器上同时留着 ADS2020_update2
    和 ADS2026_Update1 时永远选 2020，装了新版也不生效。

    ⚠️ 必须规范化：Windows 上 glob 返回的路径是混合斜杠
    （实测 'C:/Program Files/Keysight\\ADS2020_update2'，首个分隔符保留 '/'、
    其余变 '\\'），直接传给 mixer_simulation.exe 会让它解析失败并回退到硬编码的
    'C:/Program Files/Keysight/ADS2020'，于是报「Can not find ...ADS2020」
    （实际装的是 ADS2020_update2）。
    """
    # 1) 环境变量覆盖：空串 / 指向不存在的目录都当「未设置」，继续往下走
    env_path = normalize_dir(os.environ.get(_ADS_ENV, ""))
    if env_path and Path(env_path).is_dir():
        return env_path

    # 2) EDI GUI 里配的那个 ADS（config/config.ini 的 [ads] hpeesof_dir）
    if from_config := _ads_from_edi_config():
        return from_config

    # 3) 兜底：glob 探测，多版本取版本号最高的（原来 sorted()[0] 永远选中最老的 2020）
    hits = [h for pattern in _ADS_GLOB_CANDIDATES for h in glob.glob(pattern)]
    if hits:
        return normalize_dir(max(hits, key=_ads_version_key))
    return ""


def fmt_errors(errors: list[Any]) -> str:
    """把 exe 返回的 errors（[{code, stage, message, ...}]）拼成可读字符串。"""
    if not errors:
        return ""
    parts: list[str] = []
    for e in errors:
        if isinstance(e, dict):
            msg = e.get("message") or e.get("code") or "未知错误"
            parts.append(f"[{e.get('code', '?')}] {msg}")
        else:
            parts.append(str(e))
    return "；".join(parts)


def convert_ep_numeric(results: list) -> list:
    """把 exe 透传的数值字符串（electrical_parameters 的 minimum/typical/maximum）转 float。

    exe 返回的数值是字符串（实测 `"typical": "26.44..."`），agent 拿来做数值比较会
    TypeError。空串（无值）保留；非数值串（如 `"±0.6"`）保留原样。
    """
    out: list = []
    for r in results:
        if not isinstance(r, dict):
            out.append(r)
            continue
        eps = r.get("electrical_parameters")
        if not isinstance(eps, list):
            out.append(r)
            continue
        new_eps = []
        for ep in eps:
            if not isinstance(ep, dict):
                new_eps.append(ep)
                continue
            new_ep = dict(ep)
            for key in ("minimum", "typical", "maximum"):
                v = new_ep.get(key)
                if isinstance(v, str) and v.strip():
                    try:
                        new_ep[key] = float(v)
                    except ValueError:
                        pass  # 非数值（如 "±0.6"）保留字符串
            new_eps.append(new_ep)
        out.append({**r, "electrical_parameters": new_eps})
    return out


class ExeNeedsElevation(OSError):
    """子进程因清单要求管理员权限而被 Windows 拒绝启动（WinError 740）。

    继承 OSError 并保留 winerror=740，让老的 `except OSError` + winerror 判断
    （如 xy_chart 的 drawtool 提权分支）继续识别，同时 mixer 用更具体的
    `except ExeNeedsElevation` 命中。
    """

    def __init__(self, exe_path: str):
        super().__init__(f"{Path(exe_path).name} 的清单要求管理员权限，而当前进程不是以管理员身份运行的")
        self.exe_path = exe_path
        self.winerror = 740


def run_exe(exe_path: str, args: list[str], timeout_seconds: int) -> dict[str, Any]:
    """运行 exe，返回统一执行记录 {returncode, stdout, stderr, json, json_error}。

    超时抛 RuntimeError（调用方转 TOOL_TIMEOUT）；exe 不存在抛 FileNotFoundError。
    """
    if timeout_seconds < 1 or timeout_seconds > 3600:
        raise ValueError("timeout_seconds 必须在 1 到 3600 之间")
    if not Path(exe_path).is_file():
        raise FileNotFoundError(f"exe 不存在: {exe_path}")
    try:
        kwargs = dict(capture_output=True, text=False, timeout=timeout_seconds, check=False)
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([exe_path, *args], **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{Path(exe_path).name} 执行超时（{timeout_seconds} 秒）")
    except OSError as exc:
        # WinError 740 = ERROR_ELEVATION_REQUIRED：exe 清单要求管理员，Windows 直接拒绝，
        # 不会弹 UAC。原样抛 OSError 会让上层只能回一句 "[WinError 740] 请求的操作需要提升"。
        if getattr(exc, "winerror", None) == 740:
            raise ExeNeedsElevation(exe_path) from exc
        raise

    stdout = _decode_text(result.stdout).strip()
    stderr = _decode_text(result.stderr).strip()
    parsed: Any = None
    parse_err = ""
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            parse_err = f"stdout 不是合法 JSON: {exc}"

    _logger.info("run_exe %s rc=%d", Path(exe_path).name, result.returncode)
    return {
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "json": parsed,
        "json_error": parse_err,
    }
