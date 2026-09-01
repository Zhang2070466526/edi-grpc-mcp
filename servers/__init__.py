"""MCP 服务器集合 — 全局 MCP 实例 + 版本号。

子包：
  - servers.eda : EDA gRPC 服务（工程、网表、仿真）
  - servers.turbocharts : turbocharts_app 图表生成
  - servers.ansys : ANSYS HFSS 工具（COM 附着）
  - servers.cst : CST 电磁仿真（求解 / S 参数 / 远场方向图导出）
  - servers.multimodal_vision : 图片显示 / 工作区复制 / 视觉分析
  - servers.report : 仿真报告生成
"""

from mcp.server.fastmcp import FastMCP
from servers.settings import get_settings

__version__ = "0.1.7"

_settings = get_settings()


"""
参数详解
1. "EDA MCP" 含义：MCP服务器的名称
作用：这是服务的唯一标识符，会在客户端（如DeepSeek Harness）连接时显示，用来区分不同的MCP服务

2. instructions= (...) 含义：服务器的指令/描述信息
作用：
向连接的客户端（如大模型）声明这个MCP服务器能做什么
包含所有可用工具的列表和操作规则
当大模型连接到这个MCP服务时，会读取这些指令来了解如何调用工具
关键规则："产生输出文件或采用默认值时，先告知用户输出位置或默认值，并询问是否需要调整" —— 这是一个重要的交互约束

3. stateless_http= (...) 含义：是否启用无状态HTTP模式
取值：由两个条件共同决定：
    _settings.mcp_transport == "streamable-http"：传输协议必须是HTTP流式传输
    _settings.mcp_stateless_http：配置项要求无状态
作用：
    True：每个HTTP请求独立，不保存会话状态（类似RESTful API），适合高并发、请求间无依赖的场景
    False：保持HTTP长连接，维持会话状态，适合需要多轮交互的场景

"""

mcp = FastMCP(
    "EDA MCP",
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
