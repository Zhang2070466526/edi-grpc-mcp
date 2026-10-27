"""独立仿真工具 — 固定目录 exe 路径与子进程执行器。

两个 exe 固定在 EDI 安装目录的 simulation_tool 子目录下（settings.simulation_tool_dir），
stdout 均为单行 UTF-8 JSON、退出码 0/1，比 turbocharts 简单：判成败直接看退出码 +
JSON 里的 success/errors，不需要靠 stderr 措辞反推。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from servers.settings import get_settings

_logger = logging.getLogger("simulation_tool")

_settings = get_settings()
_SIM_TOOL_DIR = Path(_settings.simulation_tool_dir)
REPORT_EXPORTER_PATH = str(_SIM_TOOL_DIR / "simulation_report" / "Report_Exporter.exe")
DRAWTOOL_PATH = str(_SIM_TOOL_DIR / "simulation_report" / "drawtool.exe")
TR_SIMULATOR_PATH = str(_SIM_TOOL_DIR / "tr_simulation" / "TR_Simulator.exe")
MIXER_SIMULATION_PATH = str(_SIM_TOOL_DIR / "mixer_simulation" / "mixer_simulation.exe")
# --edi 传 EDI 安装目录（simulation_tool 的上一级）
EDI_INSTALL_DIR = str(_SIM_TOOL_DIR.parent)

# ADS 安装目录候选（--ads 为空时按此顺序探测）
_ADS_GLOB_CANDIDATES = (
    "C:/Program Files/Keysight/ADS*",
    "D:/Program Files/Keysight/ADS*",
)


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


def detect_ads_path() -> str:
    """探测 ADS 安装目录，找不到返回空串。

    ⚠️ 必须规范化：Windows 上 glob 返回的路径是混合斜杠
    （实测 'C:/Program Files/Keysight\\ADS2020_update2'，首个分隔符保留 '/'、
    其余变 '\\'），直接传给 mixer_simulation.exe 会让它解析失败并回退到硬编码的
    'C:/Program Files/Keysight/ADS2020'，于是报「Can not find ...ADS2020」
    （实际装的是 ADS2020_update2）。
    """
    import glob

    for pattern in _ADS_GLOB_CANDIDATES:
        hits = sorted(glob.glob(pattern))
        if hits:
            return normalize_dir(hits[0])
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
