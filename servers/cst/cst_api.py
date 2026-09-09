"""CST 基础层 —— 安装检测 + API 加载 + 会话管理。

CstApi 类封装 CST 官方 Python 接口的定位、加载与会话打开/关闭，
供仿真器（CstSimulator）和结果导出器（CstResultExporter）复用，避免重复。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
import winreg

from servers.task_runner import TaskRunner
from servers.utils import error_response

logger = logging.getLogger(__name__)

# CST 全局串行队列：求解（simulate）和结果导出（result_export）共用。
# CST 一次只能跑一个会话，run_solver 与 execute_vba 不能并发。
cst_runner = TaskRunner(name_prefix="cst", max_run_seconds=7200)


_CST_TMP_TTL_SECONDS = 24 * 3600


def _cleanup_stale_tmp_copies(tmp_dir: str, ttl_seconds: int) -> None:
    """清理超过 TTL 的临时副本（只读模型副本按需创建，避免无限堆积）。"""
    now = time.time()
    try:
        names = os.listdir(tmp_dir)
    except OSError:
        return
    for name in names:
        p = os.path.join(tmp_dir, name)
        try:
            if now - os.path.getmtime(p) > ttl_seconds:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
        except OSError:
            pass


def to_writable_copy(model_path: str) -> str:
    """模型只读时复制到临时可写副本，返回可写模型路径（连同结果目录一起复制）。"""
    if os.access(model_path, os.W_OK):
        return model_path
    tmp_dir = os.path.join(tempfile.gettempdir(), "cst_solve")
    os.makedirs(tmp_dir, exist_ok=True)
    _cleanup_stale_tmp_copies(tmp_dir, _CST_TMP_TTL_SECONDS)
    tmp_model = os.path.join(tmp_dir, os.path.basename(model_path))
    shutil.copyfile(model_path, tmp_model)
    src_dir = os.path.splitext(model_path)[0]
    dst_dir = os.path.splitext(tmp_model)[0]
    if os.path.isdir(src_dir):
        if os.path.isdir(dst_dir):
            shutil.rmtree(dst_dir, ignore_errors=True)
        shutil.copytree(src_dir, dst_dir, ignore=shutil.ignore_patterns('*.lok'))
    return tmp_model


def status_payload(task_id: str, snap: dict) -> dict:
    """把任务快照转成统一的查询返回体（进度部分），供各异步查询工具 *_query 复用。

    查询工具在此进度快照上按状态补充结果字段（model_path / farfield_txts）。
    """
    return {
        "success": True,
        "task_id": task_id,
        "status": snap["status"],
        "completed": snap.get("finished_at") is not None,
        "message": snap["message"],
        "error": snap["error"],
        "started_at": snap["started_at"],
        "finished_at": snap["finished_at"],
    }


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
    payload = status_payload(task_id, snap)
    if snap.get("finished_at") is None:
        payload["message"] = running_msg
        return payload
    if snap["status"] == "SUCCEEDED":
        payload[success_key] = snap["result"]
        payload["message"] = success_msg
        return payload
    return error_response(fail_code, snap.get("error") or fail_msg)


class CstApi:
    """CST 官方 Python 接口封装（安装检测 + 会话管理）。"""

    def __init__(self) -> None:
        self._api_path: str | None = None
        self._cst_interface = None
        self._cst_results = None

    # ── 安装检测 ──

    def _find_api_path(self) -> str:
        """从注册表定位 CST 的 python_cst_libraries 目录。"""
        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\CST DESIGN ENVIRONMENT_AMD64.exe"
        exe_path = ""
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, winreg.KEY_READ | view) as key:
                    exe_path, _ = winreg.QueryValueEx(key, "")
                    if exe_path:
                        break
            except OSError:
                continue
        if not exe_path:
            raise RuntimeError("CST not found in registry (%s)" % key_path)
        api_path = os.path.join(os.path.dirname(str(exe_path).strip().strip('"')), "python_cst_libraries")
        if not os.path.isdir(api_path):
            raise RuntimeError("CST python_cst_libraries not found: %s" % api_path)
        return api_path

    # ── API 加载（缓存） ──

    def load(self):
        """加载 cst.interface / cst.results，返回 (interface_module, results_module)。"""
        if self._cst_interface is None:
            api_path = self._find_api_path()
            if api_path not in sys.path:
                sys.path.insert(0, api_path)
            import cst.interface
            import cst.results
            self._cst_interface = cst.interface
            self._cst_results = cst.results
        return self._cst_interface, self._cst_results

    # ── 会话管理（一次性） ──

    def open_session(self, model_path: str):
        """连接/启动 CST 并打开模型，返回 (de, prj)。"""
        model_path = os.path.abspath(str(model_path))
        if not os.path.isfile(model_path):
            raise RuntimeError("Model file not found: %s" % model_path)
        cst_interface, _ = self.load()
        # 用官方 API 启动/连接：只有它启动的 CST 才会在 run_solver 后自动加载
        # 场可视化 hex 网格，后续导出远场才不报 "No plot data"。
        de = cst_interface.DesignEnvironment.connect_to_any_or_new()
        try:
            prj = de.open_project(model_path)
        except Exception:
            # open_project 抛异常时也要关闭环境，避免 CST/COM 会话泄漏
            de.close()
            raise
        if prj is None:
            de.close()
            raise RuntimeError("open_project returned None: %s" % model_path)
        return de, prj

    def close_session(self, de, prj) -> None:
        """关闭项目与环境（best-effort）。"""
        for obj in (prj, de):
            try:
                obj.close()
            except Exception:
                pass
