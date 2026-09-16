"""Turbocharts 串行执行器 — 使用信号量限制并发进程数。

turbocharts_app.exe 一次只能运行一个实例，本模块封装 subprocess 调用，
确保同一时间只有一个 turbocharts 进程在运行。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from collections.abc import Sequence

_logger = logging.getLogger("turbocharts")
_TURBOCHARTS_SEMAPHORE = threading.BoundedSemaphore(1)


def _decode_console(raw: bytes | None) -> str:
    """Windows 控制台 / Qt 程序的中文输出是 ANSI(GBK)，英文提示是 ASCII。

    先按 utf-8 试（英文提示 / 上游哪天改 UTF-8 都对），失败回退 cp936。
    不依赖 locale 或 PYTHONUTF8，两种环境下都稳定。
    """
    if not raw:
        return ""
    for enc in ("utf-8", "cp936"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_turbocharts(
    command: Sequence[str],
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    """串行执行 turbocharts_app.exe，同一时间只允许一个进程。"""
    if timeout_seconds < 1 or timeout_seconds > 600:
        raise ValueError("timeout_seconds 必须在 1 到 600 之间")

    with _TURBOCHARTS_SEMAPHORE:
        try:
            kwargs = dict(capture_output=True, text=False,
                          timeout=timeout_seconds, check=False)
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            t0 = time.monotonic()
            result = subprocess.run(list(command), **kwargs)
            # 先拿 bytes 再解码为 str（下游调用方无需改）
            result.stdout = _decode_console(result.stdout)
            result.stderr = _decode_console(result.stderr)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            _logger.info("turbocharts done rc=%d elapsed=%dms",
                         result.returncode, elapsed_ms)
            if result.returncode != 0:
                _logger.error("turbocharts failed rc=%d stderr=%s",
                             result.returncode, (result.stderr or "")[:300])
            return result
        except subprocess.TimeoutExpired:
            _logger.error("turbocharts timeout after %ds", timeout_seconds)
            raise RuntimeError(f"Turbocharts 执行超时（{timeout_seconds} 秒）")
