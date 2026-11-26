"""打包产物冒烟测试 —— 启动 exe，验证「能启动 + 健康检查 + 工具注册」。

用法：
    python scripts/smoke_test_exe.py        # 默认验证 dist/edi-mcp/edi_mcp_server.exe

前置：需先打包（powershell -File scripts/build.ps1）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "edi-mcp" / "edi_mcp_server.exe"
PORT = 50026
BASE = f"http://127.0.0.1:{PORT}"


def _wait_ready(timeout: float = 90) -> bool:
    """轮询 /health 直到服务就绪（启动可能需加载大量模块）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _check_com_child(exe: Path) -> bool:
    """验证冻结产物的 `--com-child` 隐藏入口（AEDT 隔离子进程那条路）。

    这是 2026-11-26 真机故障的回归点：包内没有解释器，父进程只能靠
    `exe --com-child <task>` 重新进入。这里用「不带任务」的用法分支验证
    （不碰 AEDT、不起服务），只要求：
      1. exe 能进 com_child 分支（不是主服务、也不是 argparse exit=2）
      2. JSON 经由 EDI_COM_CHILD_OUT 结果文件回来（窗口模式 stdout 不可靠）
    """
    out = Path(tempfile.mkdtemp(prefix="edi_com_child_smoke_")) / "result.json"
    env = {**os.environ, "EDI_COM_CHILD_OUT": str(out)}
    try:
        cp = subprocess.run([str(exe), "--com-child"], cwd=str(exe.parent),
                            capture_output=True, text=True, timeout=60, env=env)
    except Exception as exc:                        # noqa: BLE001 —— 冒烟失败要打印原因
        print(f"FAIL: --com-child 启动失败：{exc}")
        return False
    if not out.is_file():
        print(f"FAIL: --com-child 没写结果文件（exit={cp.returncode}）"
              f" stderr={(cp.stderr or '')[-200:]}")
        return False
    payload = json.loads(out.read_text(encoding="utf-8"))
    if payload.get("ok") is not False or "usage" not in str(payload.get("error", "")):
        print(f"FAIL: --com-child 返回异常：{payload}")
        return False
    print(f"  --com-child    -> 入口可用（exit={cp.returncode}，结果走文件）")
    return True


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: exe 不存在 {EXE}")
        print("请先运行: powershell -File scripts/build.ps1")
        return 1

    print(f"检查 {EXE.name} --com-child 入口 ...")
    if not _check_com_child(EXE):
        return 1

    print(f"启动 {EXE.name} ...")
    proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))

    try:
        if not _wait_ready():
            print("FAIL: 服务未在 90s 内就绪")
            return 1

        # 1. 健康检查
        health = httpx.get(f"{BASE}/health", timeout=5).json()
        print(f"  /health        -> status={health.get('status')}, "
              f"mcp_ready={health.get('mcp_ready')}")

        # 2. 工具注册
        ready = httpx.get(f"{BASE}/ready", timeout=5).json()
        tool_count = ready.get("tool_count", 0)
        print(f"  /ready         -> {tool_count} 个工具")
        if tool_count == 0:
            print("FAIL: 工具数异常")
            return 1

        print("PASS: exe 可用（启动 / 健康检查 / 工具注册 均正常）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
