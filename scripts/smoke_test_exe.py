"""打包产物冒烟测试 —— 启动 exe，验证「能启动 + 健康检查 + 鉴权 + 工具注册」。

用法：
    python scripts/smoke_test_exe.py        # 默认验证 dist/edi-mcp/edi_mcp_server.exe

前置：需先打包（powershell -File scripts/build.ps1）。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "edi-mcp" / "edi_mcp_server.exe"
PORT = 50026
BASE = f"http://127.0.0.1:{PORT}"


def _read_api_key() -> str:
    """从打包产物 .env 读取 MCP_API_KEY（与 build.ps1 生成的值一致）。"""
    env = ROOT / "dist" / "edi-mcp" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("MCP_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


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


def main() -> int:
    if not EXE.exists():
        print(f"FAIL: exe 不存在 {EXE}")
        print("请先运行: powershell -File scripts/build.ps1")
        return 1

    api_key = _read_api_key()
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

        # 2. 鉴权（配置了 token 才验证）
        if api_key:
            no_token = httpx.get(f"{BASE}/mcp", timeout=5)
            with_token = httpx.get(f"{BASE}/mcp", params={"token": api_key}, timeout=5)
            print(f"  鉴权           -> 不带 token={no_token.status_code}, "
                  f"带 token={with_token.status_code}")
            if no_token.status_code != 401 or with_token.status_code == 401:
                print("FAIL: 鉴权校验不通过（期望 不带=401 且 带=非401）")
                return 1
        else:
            print("  鉴权           -> 未配置 MCP_API_KEY，跳过")

        # 3. 工具注册
        ready = httpx.get(f"{BASE}/ready", timeout=5).json()
        tool_count = ready.get("tool_count", 0)
        print(f"  /ready         -> {tool_count} 个工具")
        if tool_count == 0:
            print("FAIL: 工具数异常")
            return 1

        print("PASS: exe 可用（启动 / 健康检查 / 鉴权 / 工具注册 均正常）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
