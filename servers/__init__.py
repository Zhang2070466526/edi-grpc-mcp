"""MCP 服务器集合 — 全局 MCP 实例 + 版本号。

本模块是 MCP 服务的「心脏」：创建全局唯一的 mcp 实例，所有工具/资源/提示词
都挂在这个实例上。整个项目通过 `from servers import mcp` 引用它。

子包：
  - servers.eda : EDA gRPC 服务（工程、网表、仿真）
  - servers.turbocharts : turbocharts_app 图表生成
  - servers.ansys : ANSYS HFSS 工具（COM 附着）
  - servers.cst : CST 电磁仿真（求解 / S 参数 / 远场方向图导出）
  - servers.multimodal_vision : 图片显示 / 工作区复制 / 视觉分析
  - servers.report : 仿真报告生成
"""

# FastMCP：MCP 框架的核心类，用于创建 MCP 服务器实例。
# NotificationOptions：底层协议类，用于声明「工具列表可变」（listChanged）能力。
# get_settings：项目自己的配置单例函数（集中读取 .env，模块内禁止直接 os.getenv）。
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import NotificationOptions

from servers.settings import get_settings

# 版本号：需与 pyproject.toml 里的 version 保持一致。
# /ready、/health 等端点和 Resource 会返回这个版本号。
__version__ = "0.1.7"

# 全局配置单例：所有环境变量（EDA_GRPC_SERVER、LLM_*、VISION_* 等）都从这里读。
# lru_cache 保证整个进程只加载一次、所有模块共享同一个 Settings 对象。
_settings = get_settings()


# ── 全局 MCP 实例 ──────────────────────────────────────────────
# FastMCP 三个参数的含义：
#   1. 服务名 "EDI gRPC MCP" —— 客户端连接时显示的唯一标识符，用于区分不同 MCP 服务。
#   2. instructions —— 向连接的 AI 客户端声明本服务能做什么（工具范围 + 操作规则），
#      大模型连接后会读取这段说明来了解如何调用工具。
#   3. stateless_http —— 是否启用无状态 HTTP 模式：
#      True  = 每个 HTTP 请求独立、不保留会话状态（类似 RESTful，适合高并发）；
#      False = 保持长连接、维持会话状态（适合需要多轮交互的场景）。
#      由「传输方式是 streamable-http」且「配置开启了无状态」两个条件共同决定。
mcp = FastMCP(
    "EDI gRPC MCP",
    instructions=(
        "EDA 工程操作工具集："
        "扫描工程、打开工程、网表查看、仿真执行、截图原理图、"
        "模型替换、关闭工程、ADS 仿真控制、启动 EDI、RAW 图表生成、"
        "ANSYS HFSS 工具。"
        "操作规则：产生输出文件（截图/图表/报告）或采用默认值时，要告知用户输出位置或默认值，暂时不用询问是否需要调整。"
    ),
    stateless_http=(
        _settings.mcp_transport == "streamable-http"
        and _settings.mcp_stateless_http
    ),
)


# ── 声明「工具列表可变」能力（initialize 返回 capabilities.tools.listChanged=true）──
# 背景：MCP 客户端（如 Hermes）在 initialize 握手时调用一次 tools/list 后，会缓存工具
# 列表、不再主动刷新，导致服务端增删工具后客户端要很久才能发现。
# 这里包装 FastMCP 底层生成「初始化响应」的方法，让响应带上 listChanged=true，
# 告诉客户端「工具列表可能变化」，客户端可据此（配合 /ready 的 tools_hash）判断是否需要重新拉取。
#
# 逐行说明：
#   getattr(..., "create_initialization_options", None)
#       —— 拿到 FastMCP 底层「生成初始化响应」的方法；属性不存在时返回 None（防御，
#          防止 SDK 升级后私有属性改名/消失导致服务 import 崩溃）。
#   if _orig_create_init is not None:
#       —— 只有方法存在时才做包装，否则静默跳过（不声明 listChanged，但不影响服务）。
#   def _create_init_with_tools_changed(...):
#       —— 定义一个「包装函数」，在调用原方法前塞入 NotificationOptions(tools_changed=True)。
#   kwargs.setdefault("notification_options", NotificationOptions(tools_changed=True))
#       —— 若调用方（FastMCP）没传 notification_options 参数，就补上「工具可变=True」。
#   return _orig_create_init(*args, **kwargs)
#       —— 用原方法生成最终的初始化响应（此时 capabilities.tools.listChanged 已被设为 True）。
#   mcp._mcp_server.create_initialization_options = _create_init_with_tools_changed
#       —— 用包装函数替换原方法，此后所有 initialize 调用都会走包装逻辑。
#
# 注意：这里依赖了私有属性 _mcp_server（下划线开头），属 SDK 内部实现，升级有风险；
# 故用 getattr 防御。真正让客户端「自动刷新」还需服务端主动发 notifications/tools/list_changed
# 通知（当前工具是 import 时静态注册、运行期不变，故未发送通知）。
_orig_create_init = getattr(mcp._mcp_server, "create_initialization_options", None)

if _orig_create_init is not None:
    def _create_init_with_tools_changed(*args, **kwargs):
        kwargs.setdefault(
            "notification_options", NotificationOptions(tools_changed=True)
        )
        return _orig_create_init(*args, **kwargs)

    mcp._mcp_server.create_initialization_options = _create_init_with_tools_changed
