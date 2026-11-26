"""打包入口：MCP 主服务 与 AEDT COM 隔离子进程共用同一个 exe。

为什么单独拆一个入口：
PyInstaller 冻结后 `sys.executable` 指向 exe 自身，包内**没有解释器**，
所以 `sys.executable + com_child.py + task` 这条路必然失败 —— exe 的 argparse
把脚本路径当非法参数，直接 exit=2（2026-11-26 真机：HFSS 里 export_snp / open /
state 全挂，进程内 COM 的求解与状态查询正常）。
冻结态改用同一个 exe 的隐藏参数 `--com-child` 重新进入；这条分支在 import
start_servers 之前就返回，不付「注册几百个工具 + 起 uvicorn」的启动代价。

用法：
    edi_mcp_server.exe                        # 正常启动 MCP 服务
    edi_mcp_server.exe --com-child state      # 内部：单次 AEDT COM 操作
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "--com-child":
        # 还原成 com_child.main() 期望的 argv 形状（argv[1] = 任务名）
        sys.argv = ["com_child", *argv[1:]]
        from servers.ansys.com_child import main as com_child_main
        sys.exit(com_child_main())
    # 主服务入口：与 `python start_servers.py` 完全等价
    import start_servers
    start_servers.main()


if __name__ == "__main__":
    main()
