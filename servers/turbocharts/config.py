"""Turbocharts 串行执行器 — 使用信号量限制并发进程数。

turbocharts_app.exe 一次只能运行一个实例，本模块封装 subprocess 调用，
确保同一时间只有一个 turbocharts 进程在运行。
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from servers.utils import decode_local_text

_logger = logging.getLogger("turbocharts")
_TURBOCHARTS_SEMAPHORE = threading.BoundedSemaphore(1)


def record_run(
    command: Sequence[str],
    result: subprocess.CompletedProcess[str] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    """把一次 turbocharts_app.exe 调用记成一条可回传的执行记录。

    引擎的进度/诊断输出现在**走 stderr(fd 2)**（2026-09-30 11:19 起的构建；改法 = `qInstallMessageHandler`
    + `fwrite(..., stderr)`），`subprocess` 管道可捕获 ⇒ 这里记录的 stdout/stderr 一般是真实内容；
    更早的构建写控制台设备（CONOUT$）⇒ 两路恒为空。⚠️ 无论如何 **`return_code` 不能当判据**：数据层失败
    （RAW 不存在 / --type 非法 / 曲线无数据 / 假 RAW）依旧 rc=0 且只写空图 —— 所以这里如实记录命令与
    两路输出供事后对照，成败判据仍必须看产物文件。超时场景拿不到 CompletedProcess：result=None、
    error=异常文本。
    """
    stdout = (getattr(result, "stdout", "") or "").strip()
    stderr = (getattr(result, "stderr", "") or "").strip()
    return {
        "command": " ".join(str(c) for c in command),
        "return_code": getattr(result, "returncode", None),
        "stdout": stdout,
        "stderr": stderr,
        # ok：引擎自报的这一次成败（False=命中失败信号 / True=见成功信号 / None=无可判信号；
        # 超时等异常、rc≠0 都直接记 False）。⚠️ 数据层失败时 rc 恒 0，别拿 return_code 当判据；
        # 但 rc≠0 必属更硬的失败（如 Qt 参数层 rc=127）⇒ 那些场合判 False，避免漏判成 None。
        "ok": (False if error or (result is not None and result.returncode != 0)
               else judge_engine_output(stderr, stdout)),
    } | ({"error": error} if error else {})


# ── 引擎自报信号（2026-09-30 实测口径）──
# 引擎的 rc 恒为 0（曲线不存在/无数据时也 rc=0 并写一张空图），所以"这次调用到底成没成"只能靠
# 它自己的措辞 + 产物来判。下面是实测到过的原句：
_ENGINE_FAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"绘制线段\s*[:：]\s*\(\s*\)"),   # 一条曲线都没画出来
    re.compile(r"linename\s*为空"),
    re.compile(r"格式错误"),
    re.compile(r"CSV导出失败"),
    re.compile(r"无法创建文件"),
    re.compile(r"\[C-ERROR\]"),
)
_ENGINE_OK_PATTERN = re.compile(r"导出成功")


def judge_engine_output(*texts: str) -> bool | None:
    """按引擎自己的措辞判定这一次调用成功与否。

    Returns:
        False = 命中失败信号（如「HB CSV导出失败：x_label_为空」）；
        True  = 只看到成功信号（「… 导出成功」）；
        None  = 没有可判信号（老版本引擎输出恒空时就是这种）。
    """
    joined = "\n".join(t for t in texts if t)
    if not joined.strip():
        return None
    if any(p.search(joined) for p in _ENGINE_FAIL_PATTERNS):
        return False
    return True if _ENGINE_OK_PATTERN.search(joined) else None


def engine_report(runs: Sequence[dict]) -> list[str]:
    """把各次调用的关键行摘出来（去重、保留引擎原措辞），供人/agent 快速判断。"""
    out: list[str] = []
    for run in runs:
        text = "\n".join(str(run.get(k) or "") for k in ("stderr", "stdout"))
        for raw_line in text.splitlines():
            line = raw_line.strip().strip('"').strip()
            if line and _is_engine_key_line(line) and line not in out:
                out.append(line)
    return out


def engine_failures(runs: Sequence[dict]) -> list[str]:
    """只挑失败行（做 message/warning 用）。"""
    return [l for l in engine_report(runs)
            if any(p.search(l) for p in _ENGINE_FAIL_PATTERNS)]


def _is_engine_key_line(line: str) -> bool:
    return (any(p.search(line) for p in _ENGINE_FAIL_PATTERNS)
            or _ENGINE_OK_PATTERN.search(line) is not None)


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
            result.stdout = decode_local_text(result.stdout)
            result.stderr = decode_local_text(result.stderr)
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


# ── 引擎原文落盘（2026-09-30 起）──
# 为什么：MCP 返回的 stderr/command_runs/engine_report 只到"模型"为止，模型可能省略转述
# （用户 2026-09-30 反馈：只回一句 message 就拿不到引擎原文）。落盘后 user/agent 随时可读全文，
# 不依赖模型自觉。日志与 RAW 同目录，追加写，多次调用累积。
_ENGINE_LOG_SUFFIX = ".turbocharts.log"


def engine_log_path(raw_path: str | Path) -> Path:
    """引擎原文日志路径 = 与 RAW 同目录的 ``<raw 文件名>.turbocharts.log``（追加写）。"""
    p = Path(raw_path)
    return p.with_name(p.name + _ENGINE_LOG_SUFFIX)


def append_engine_log(
    raw_path: str | Path,
    runs: Sequence[dict],
    *,
    tool: str = "turbocharts_convert",
    context: dict[str, object] | None = None,
) -> tuple[str, int]:
    """把一次工具调用的引擎原始输出（每趟 command / rc / stdout / stderr）追加落盘。

    写入内容即引擎原措辞（不加工、不截断），供人事后核对；函数**不吞异常**，
    写盘失败由调用方决定怎么提示（工具侧转成 warning，不影响产物与返回）。
    """
    log_path = engine_log_path(raw_path)
    head = f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} {tool}"
    if context:
        head += " | " + " ".join(
            f"{k}={v}" for k, v in context.items() if v not in (None, "")
        )
    lines = [head + " ====="]
    for idx, run in enumerate(runs):
        rc = run.get("return_code")
        ok = run.get("ok")
        err = run.get("error")
        lines.append(f"-- run[{idx}] rc={rc} ok={ok}" + (f" error={err}" if err else ""))
        lines.append(f"   cmd: {run.get('command', '')}")
        for stream in ("stdout", "stderr"):
            text = str(run.get(stream) or "")
            if not text.strip():
                lines.append(f"   {stream}: (空)")
                continue
            lines.append(f"   {stream}:")
            lines.extend(f"     {ln}" for ln in text.splitlines())
    lines.append("")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return str(log_path), len(lines)
