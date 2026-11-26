"""MCP 服务器集合 — 全局 MCP 实例 + 版本号。

本模块是 MCP 服务的「心脏」：创建全局唯一的 mcp 实例，所有工具/资源/提示词
都挂在这个实例上。整个项目通过 `from servers import mcp` 引用它。

子包：
  - servers.eda : EDA gRPC 服务（工程、网表、仿真、模型、信号链）
  - servers.turbocharts : turbocharts_app 图表生成
  - servers.ansys : ANSYS HFSS 工具（COM 附着）
  - servers.cst : CST 电磁仿真（求解 / S 参数 / 远场方向图导出）
  - servers.multimodal_vision : 图片显示 / 视觉分析 / 文档访问
  - servers.resources_prompts : MCP Resources & Prompts（7 Resource + 7 Prompt）
  - 另有 6 个 Resource 随业务包走：turbocharts（RAW 转图说明）+ simulation_tool（5 个 exe 说明文档）
  - servers.chat : 聊天模块（会话管理、LLM 工具闭环）
"""

# FastMCP：MCP 框架的核心类，用于创建 MCP 服务器实例。
# get_settings：项目自己的配置单例函数（集中读取 .env，模块内禁止直接 os.getenv）。
from mcp.server.fastmcp import FastMCP

from servers.settings import get_settings

# 版本号：需与 pyproject.toml 里的 version 保持一致。
# /ready、/health 等端点和 Resource 会返回这个版本号。
__version__ = "0.1.13"

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
        "EDI gRPC MCP —— EDA 设计与仿真工具集（EDI / ANSYS HFSS / CST 三大仿真引擎）。\n"
        "\n"
        "【硬规则：先查工具，别自己实现】\n"
        "1. EDA 任务（读/改工程、跑仿真、出图、算指标、查器件、算线宽/阶数）先用 describe_tool 找工具；能找到就必须调用，不要写脚本重实现算法。\n"
        "2. 禁止读/搜索/反编译本服务源码（不要 grep、不要读 .py 文件）来推断参数或实现，一切以工具返回为准；参数用 describe_tool(\"<工具名>\")；不知道工具名就写能力关键词，如 describe_tool(\"微带线宽\")、describe_tool(\"滤波器阶数\")。\n"
        "3. 不要自己写脚本重实现算法，调用工具时也不要写临时脚本；只有两种情况允许写脚本：① describe_tool(\"\") 列全后确认没有该能力；② 批量编排多个工具（脚本内部仍调工具，不重算算法）。\n"
        "4. 不要凭经验套公式：微带线宽/阻抗/损耗、滤波器阶数与零点、功分器节数与隔离电阻、S 参数解析与绘图，工具里都已实现（含修正项）。\n"
        "\n"
        "【不知道怎么用一个工具 → 先调 describe_tool】\n"
        "- describe_tool()            → 列出全部工具名 + 一句话说明（不知道有哪些工具时用它）\n"
        "- describe_tool(\"工具名\")    → 完整 docstring + 参数表（含数组元素结构提示）+ 可直接照抄的最小示例\n"
        "- describe_tool(\"能力关键词\") → 按「你想做什么」检索（如 describe_tool(\"噪声系数\")），返回候选工具 + 最匹配那个的完整说明\n"
        "- 工具名写错会返回相似名建议，改名重试即可\n"
        "⚠️ MCP 只把 docstring 首行发给模型；复杂参数的嵌套结构已展开写进 inputSchema，先看 schema 再填。schema 里仍是「数组但无元素结构」的参数（如 stopband_specs），用 describe_tool 补全。\n"
        "⚠️ 参数名与单位一律照 describe_tool 的 example 写，别猜。\n"
        "\n"
        "【找工具/查参数】用 describe_tool：留空列全部工具、传工具名看参数示例、传能力关键词检索。\n"
        "\n"
        "【操作规则】产生输出文件（截图/图表/报告）或采用默认值时，先告知用户输出位置/默认值。仿真用异步接口，按 task_id 轮询进度。"
    ),
    stateless_http=(
        _settings.mcp_transport == "streamable-http"
        and _settings.mcp_stateless_http
    ),
)

"""
── 同步工具统一 offload（保持函数 sync，只在 MCP 调用层 offload）────────── FastMCP 的 Tool.run 对同步工具直接同步执行 fn(...)（见 call_fn_with_arg_validation
的 else 分支 `return fn(...)`），慢工具执行期间整个事件循环被冻结（下载 / 探活 /其它会话轮询全堵）。

这里 monkeypatch Tool.run：同步工具整段丢到工作线程（asyncio.run 驱动，因为 run是 async 方法），
事件循环立即回来接单。工具函数本身保持 sync，直接调用仍返回dict（Python API 不变、测试不破）。
anyio.to_thread 会复制 context（Host 推导的contextvar 传得进线程），与产物链接机制不冲突。

"""

import asyncio
import json

import anyio
from mcp import types as _mcp_types
from mcp.server.fastmcp.tools import base as _tb

_orig_run = _tb.Tool.run


def _as_error_result(payload: dict) -> _mcp_types.CallToolResult:
    """把失败的统一信封转成 isError=True 的 CallToolResult。

    为什么要有它（2026-11-17 实测）：工具失败时返回的是普通 dict，SDK 在
    lowlevel/server.py 里一律 `isError=False` 包装（只有抛异常才是 isError=True）。
    于是 MCP 客户端把「文件不存在」「工具名不存在」这类失败当成功结果渲染，
    模型也不会据此自查参数。这里统一在协议层标错，正文 JSON 原样保留
    （content 文本 + structuredContent 双份，客户端读哪个都不丢信息）。
    """
    return _mcp_types.CallToolResult(
        content=[_mcp_types.TextContent(
            type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))],
        structuredContent=payload,
        isError=True,
    )


async def _offloading_run(self, arguments, context=None, convert_result=False):
    """monkeypatch Tool.run：同步工具整段丢 anyio.to_thread，防冻结事件循环。

    同时承担两件事（2026-11-17 新增）：
      1. 失败信封（`success=False`）→ `isError=True`，见 _as_error_result；
      2. convert_result 由本函数在判定之后统一调用，保证成功路径与原 SDK 行为等价。
    """
    if self.is_async:
        result = await _orig_run(self, arguments, context, False)
    else:
        result = await anyio.to_thread.run_sync(
            lambda: asyncio.run(_orig_run(self, arguments, context, False))
        )

    if isinstance(result, dict) and result.get("success") is False:
        return _as_error_result(result)

    if convert_result:
        return self.fn_metadata.convert_result(result)
    return result


_tb.Tool.run = _offloading_run
