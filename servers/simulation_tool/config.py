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
TR_SIMULATOR_PATH = str(_SIM_TOOL_DIR / "tr_simulation" / "TR_Simulator.exe")
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


def detect_ads_path() -> str:
    """探测 ADS 安装目录，找不到返回空串。"""
    import glob

    for pattern in _ADS_GLOB_CANDIDATES:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
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
