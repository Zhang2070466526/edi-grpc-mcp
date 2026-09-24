"""CST 作业调度层 —— 定位 CST 自带解释器，并以独立子进程执行 CST 作业。

背景：CST 官方 cst 库自带版本闸门（`cst/__init__.py` 只接受 Python 3.6~3.9），
而本服务跑在 3.12 上 —— 同进程 `sys.path.insert` 后 import 必失败：
    The cst package supports only Python 3.6/3.7/3.8/3.9
因此所有真正触碰 CST 的调用（开会话 / 求解 / 读结果 / VBA 导出）都交给
`servers/cst/cst_worker.py`，由 CST 自带的 `AMD64\\python\\python.exe` 执行。

本模块只负责三件事：
  1. 定位那个解释器（环境变量 `CST_PYTHON_PATH` → 注册表）
  2. 写作业 JSON → 起子进程 → 读结果 JSON（超时兜底）
  3. 向工具层暴露统一的串行队列 `cst_runner`（CST 一次只能跑一个会话）
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import winreg

from servers.task_runner import TaskRunner
from servers.utils import decode_local_text, error_response

_log = logging.getLogger("cst_api")

# CST 全局串行队列：求解（simulate）和结果导出（result_export）共用。
# CST 一次只能跑一个会话，run_solver 与 execute_vba 不能并发。
cst_runner = TaskRunner(name_prefix="cst", max_run_seconds=7200)

# 单个作业的整体超时（秒）：求解可能很久，给足余量（与 cst_runner 的兜底一致）
JOB_TIMEOUT_SECONDS = 7200

# 同目录下的独立执行器（以脚本方式启动，不 import 包，避免 3.6 侧解析不了本包依赖）
_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cst_worker.py")

_CST_APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\CST DESIGN ENVIRONMENT_AMD64.exe"


def _cst_install_dir() -> str:
    """从注册表定位 CST 安装目录（CST DESIGN ENVIRONMENT_AMD64.exe 所在目录）。"""
    exe_path = ""
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CST_APP_PATHS_KEY, 0, winreg.KEY_READ | view) as key:
                exe_path, _ = winreg.QueryValueEx(key, "")
                if exe_path:
                    break
        except OSError:
            continue
    if not exe_path:
        raise RuntimeError("CST not found in registry (%s)" % _CST_APP_PATHS_KEY)
    return os.path.dirname(str(exe_path).strip().strip('"'))


def cst_python_path() -> str:
    """定位 CST 自带的 Python 解释器（cst 库官方支持的 3.6~3.9 版本）。

    优先级：环境变量 `CST_PYTHON_PATH`（便于现场换版本/排查）→ 注册表推断
    `<CST 安装目录>\\python\\python.exe`。
    """
    override = os.environ.get("CST_PYTHON_PATH", "").strip()
    if override:
        if not os.path.isfile(override):
            raise RuntimeError("CST_PYTHON_PATH 指向的文件不存在: %s" % override)
        return override
    candidate = os.path.join(_cst_install_dir(), "python", "python.exe")
    if not os.path.isfile(candidate):
        raise RuntimeError(
            "CST 自带解释器不存在: %s（可用环境变量 CST_PYTHON_PATH 指定）" % candidate
        )
    return candidate


def run_cst_job(op: str, *, timeout: float = JOB_TIMEOUT_SECONDS, **payload):
    """在 CST 自带解释器的子进程里执行一个 CST 作业，返回其结果字段。

    作业与结果都走临时目录下的 JSON 文件（不靠 stdout 传数据，避免 CST 原生输出污染）。
    失败时抛 RuntimeError，并把子进程给的真实原因（含 traceback 尾部）带出来 ——
    这样工具层能把它落进 `CST_*_FAILED` 的 message，而不是只留一句「失败」。

    Args:
        op: "solve" / "export_snp" / "export_farfield"（见 cst_worker._OPS）。
        timeout: 子进程整体超时（秒）。
        **payload: 作业参数（model_path / output_dir / port_count 等）。
    """
    python_path = cst_python_path()
    payload["op"] = op
    tmp_dir = tempfile.mkdtemp(prefix="cst_job_")
    job_path = os.path.join(tmp_dir, "job.json")
    result_path = os.path.join(tmp_dir, "result.json")
    payload["result_path"] = result_path
    try:
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        _log.info("cst job op=%s python=%s timeout=%ss", op, python_path, timeout)
        try:
            proc = subprocess.run(
                [python_path, _WORKER_SCRIPT, "--job", job_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("CST 作业执行超时（>%ss，op=%s）" % (_fmt_seconds(timeout), op))

        out = _read_result(result_path)
        if out is None:
            # 结果文件缺失（子进程崩在写盘之前）→ 用 stdout/stderr 兜底诊断
            detail = _tail(decode_local_text(proc.stdout)) or _tail(decode_local_text(proc.stderr)) or "(无输出)"
            raise RuntimeError(
                "CST 作业进程未产出结果（exit=%s, op=%s）: %s" % (proc.returncode, op, detail)
            )
        if not out.get("success"):
            reason = out.get("error") or "unknown error"
            tb = _tail(str(out.get("traceback") or ""))
            raise RuntimeError("%s%s" % (reason, "\n" + tb if tb else ""))
        return out.get("result")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _read_result(result_path: str):
    """读取结果 JSON；文件不存在或不是合法 JSON 时返回 None。"""
    if not os.path.isfile(result_path):
        return None
    try:
        with open(result_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _log.warning("cst job result unreadable: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _fmt_seconds(value: float) -> str:
    """把秒数格式化成整数/小数都好看的字符串。"""
    if float(value).is_integer():
        return str(int(value))
    return "%.1f" % value


def _tail(text: str, limit: int = 600) -> str:
    """取文本尾部若干字符（错误诊断用），保留最后几行 traceback。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def query_task(
    task_id: str,
    snap: dict | None,
    *,
    not_found_msg: str,
    running_msg: str,
    success_key: str,
    success_msg: str,
    fail_code: str,
    fail_msg: str,
) -> dict:
    """统一的异步任务查询返回体：进度 + 完成时附结果字段。

    snap 为 None 返回 TASK_NOT_FOUND；运行中返回进度快照；成功时在快照上附
    success_key 字段；失败返回 error_response。供各 *_query 工具复用，消除同构模板。
    """
    if snap is None:
        return error_response("TASK_NOT_FOUND", not_found_msg)
    payload = {
        "success": True,
        "task_id": task_id,
        "status": snap["status"],
        "completed": snap.get("finished_at") is not None,
        "error": snap["error"],
        "started_at": snap["started_at"],
        "finished_at": snap["finished_at"],
    }
    if snap.get("finished_at") is None:
        payload["message"] = running_msg
        return payload
    if snap["status"] == "SUCCEEDED":
        payload[success_key] = snap["result"]
        payload["message"] = success_msg
        return payload
    return error_response(fail_code, snap.get("error") or fail_msg)
