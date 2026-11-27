"""公共工具层 — 文件/参数校验、统一响应构建、运行时地址、产物与文件链接。

全项目复用的基础函数，无外部依赖。
"""

from __future__ import annotations

import functools
import inspect
import math
import os
import socket
import tempfile
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# ── 服务启动时间戳 ──
SERVER_STARTED_AT: float = time.time()


def server_uptime_seconds() -> float:
    """返回服务启动以来的运行秒数。"""
    return time.time() - SERVER_STARTED_AT


def _signature_for_schema(fn):
    """取「在原始函数自己的 globals 里求值过」的签名，供工具装饰器挂到 wrapper 上。

    `from __future__ import annotations` 让注解变成字符串；FastMCP 用 wrapper 的 __globals__
    去求值，解析不到业务模块的类型（如 `MixerProject`）⇒ 参数 schema 退化成
    `additionalProperties: true`（2026-11-26 实测：agent 只能靠报错反推结构）。
    沿 `__wrapped__` 找到原始函数再 eval，Schema 才能生成真实字段。
    """
    try:
        return inspect.signature(inspect.unwrap(fn), eval_str=True)
    except Exception:                               # noqa: BLE001 —— 求值失败就保持原样
        return None


def per_tool_mutex(fn):
    """给工具函数加一把独立互斥锁。

    同步工具统一 offload 到工作线程后，同类工具并发会争用共享资源（turbocharts
    子进程、Matplotlib 全局状态、报告渲染服务）。此装饰器给每个被装饰函数一把
    独立锁，同类工具互斥、不同工具互不阻塞。
    """
    lock = threading.Lock()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        """持锁串行执行同一工具，防止并发争用共享资源。"""
        with lock:
            return fn(*args, **kwargs)

    # ⚠️ 见 _signature_for_schema：不挂签名的话，FastMCP 会把 pydantic 参数退化成自由 object
    if (sig := _signature_for_schema(fn)) is not None:
        wrapper.__signature__ = sig
    return wrapper


# ── 文件 / 路径校验 ──

def validate_file(path: str, extensions: tuple[str, ...] = ()) -> str:
    """校验文件存在，可选限制扩展名，返回规范化绝对路径。"""
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError(f"路径必须是字符串: {path!r}")
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {p}")
    if extensions and p.suffix.lower() not in extensions:
        raise ValueError(f"文件扩展名必须是 {extensions}: {p}")
    return str(p.resolve())


def require_file(path: str, extensions: tuple[str, ...] = ()) -> tuple[str, dict[str, Any] | None]:
    """校验文件存在，返回 (resolved, error)；失败返回统一错误字典而非抛异常。"""
    try:
        return validate_file(path, extensions), None
    except FileNotFoundError as e:
        return "", error_response("FILE_NOT_FOUND", str(e))
    except ValueError as e:
        return "", error_response("INVALID_PATH", str(e))


def is_network_path(path) -> bool:
    """判断路径是否为网络路径（UNC \\\\ 或 URL //），此类路径无法本地校验。"""
    s = str(path)
    return s.startswith(r"\\") or s.startswith("//")


def tcp_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """探测 TCP 端口是否可连（用于「服务是否在跑」类检查，连接成功即关闭）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── 参数校验 ──

def require_nonempty(value: str, *, error_code: str = "INVALID_PARAMETERS",
                     label: str = "") -> tuple[str, dict[str, Any] | None]:
    """校验字符串非空，返回 (stripped_value, error)。为空时返回统一错误字典。"""
    if not isinstance(value, str):
        return "", error_response(error_code, f"{label or '参数'} 必须是字符串")
    s = value.strip()
    if not s:
        return "", error_response(error_code, f"{label or '参数'} 不能为空")
    return s, None


def require_position(position: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """校验并规范化场景坐标对象，返回 (position, 错误)。

    position 必须是 {"x": .., "y": ..} 形式的对象，x/y 为有限数值（拒绝 bool、
    字符串、NaN/Inf）。返回只含 x/y 的字典；非法时返回统一错误字典。
    """
    if not isinstance(position, dict):
        return None, error_response("INVALID_PARAMETERS", "position 必须是对象 {x, y}")
    x, y = position.get("x"), position.get("y")
    if (not isinstance(x, (int, float)) or isinstance(x, bool)
            or not isinstance(y, (int, float)) or isinstance(y, bool)):
        return None, error_response("INVALID_PARAMETERS", "position 的 x、y 必须是有限数值")
    if not (math.isfinite(x) and math.isfinite(y)):
        return None, error_response("INVALID_PARAMETERS", "position 的 x、y 必须是有限数值")
    return {"x": x, "y": y}, None


def require_uuid(value: str, *, label: str = "uuid") -> tuple[str, dict[str, Any] | None]:
    """校验字符串为合法 UUID（如 12345678-1234-4234-8234-123456789abc），返回 (stripped_value, error)。"""
    if not isinstance(value, str):
        return "", error_response("INVALID_PARAMETERS", f"{label} 必须是字符串")
    s = value.strip()
    if not s:
        return "", error_response("INVALID_PARAMETERS", f"{label} 不能为空")
    try:
        uuid.UUID(s)
    except (ValueError, AttributeError):
        return "", error_response("INVALID_PARAMETERS", f"{label} 不是合法的 UUID: {s}")
    return s, None


def decode_local_text(raw: bytes | None) -> str:
    """解码**本机 Windows 生成的文本**（EDI 的 .ep/netlist.log、Qt 工具控制台输出）。

    顺序必须 cp936 优先：GBK 汉字约 9.2% 的字节序列同时是合法 UTF-8，若先试
    utf-8 会静默解成西里尔/拉丁字母（如 '专业'→'רҵ'）。BOM 是唯一的无歧义
    UTF-8 信号，优先识别。**本函数不是通用解码器**——不要用它读配置/上游 UTF-8 文本。
    """
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM
        return raw.decode("utf-8-sig", errors="replace")
    for enc in ("cp936", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp936", errors="replace")


# ── 统一响应构建 ──

#: 按 error_code 的默认 hint —— 284 处 error_response 调用里只有 6% 显式写了 hint，
#: 其余只回 do_not_retry（对 agent 等于「不许重试」但不说下一步），是「走弯路」的直接原因。
#: 这张表让默认 hint 变成可执行动作；显式传 hint= 时仍以显式值为准。
_DEFAULT_HINTS: dict[str, str] = {
    "MISSING_REQUIRED_ARGUMENT": "按 missing 字段补齐参数；字段含义与可抄示例见 describe_tool(<工具名>)",
    "INVALID_PARAMETERS": "按 message 逐项修正参数；不确定字段含义时先 describe_tool(<工具名>)",
    "PATH_NOT_FOUND": "传绝对路径；工程/模型路径可先用工程列表、模型检索等确认",
    "FILE_NOT_FOUND": "确认文件存在且用绝对路径（Windows 推荐正斜杠，如 C:/data/x.raw）",
    "PROJECT_NOT_FOUND": "先用 list_epp_projects 列出可用工程，再取其中的 .epp 路径",
    "PROJECT_NOT_OPEN": "先打开目标工程（同目录需有 schematics/main/schematic.ep）",
    "NO_CURRENT_PROJECT": "先打开一个工程；只是想看状态可用工程状态查询",
    "EDA_GRPC_OFFLINE": "先启动 EDI 客户端，确认服务就绪后重试",
    "COMPONENT_NOT_FOUND": "先查器件实例名，注意大小写与工程一致",
    "NETLIST_NOT_FOUND": "先生成网表，再把返回的路径传进来",
    "CURVE_NOT_FOUND": "先用曲线查询能力确认可用曲线名（形如 DB_S[2,1]）再重试",
    "NO_SOLUTION": "指标不可达：放宽抑制/回波损耗，或调大 max_notches / 增加阶数后重试",
    "TOOL_NOT_FOUND": '工具名拼写检查：describe_tool("") 可列出全部可用工具名',
    "DEPENDENCY_MISSING": "该功能需要可选依赖（如 scipy）；请确认部署包已收全该依赖，或换不依赖它的工具",
    "SIMULATION_EXECUTION_FAILED": "看 details 里的原始错误/日志；确认 EDI 已打开目标工程、ADS 许可可用",
    "TOOL_TIMEOUT": "调大 timeout_seconds（上限见工具说明）后重试；或减少链路/指标数量",
    "UNSUPPORTED_COMPONENT_TYPE": "先查可用器件类型与参数，再重试",
    "OUTPUT_ALREADY_EXISTS": "换一个 output_path，或先删除同名产物",
    "PERMISSION_DENIED": "换可写目录（如 %TEMP% 或工作区内），避开 Program Files 等受保护路径",
    "REPORT_NOT_VERIFIED": "脚本跑了但工程没被改写 ⇒ 报告未必建上：看 script_output / script_log 与 GUI Message 面板，"
                           "核对解名/setup，必要时到 GUI 手工建一次确认",
    "AEDT_NOT_RUNNING": "先 launch_aedt 启动 AEDT（或 open_hfss_project 会自动拉起）",
    "AEDT_NOT_FOUND": "用 aedt_path 显式指定 AEDT 安装目录",
    "ORDERS_MISSING_LO": "多级混频的 orders 要逐个本振给阶数（如 {\"FREQ\":5,\"LO1\":5,\"LO2\":5}）；"
                         "本振名取自网表的 LO 源，单级混频写 {\"FREQ\":5,\"LO\":5} 即可",
    # 2026-11-26：COM 失败按原因分档（com_child 的 reason → 错误码），默认 hint 覆盖"没显式传 hint"的调用点
    "AEDT_VERSION_MISMATCH": "双版本 AEDT：给 open_hfss_project 传 aedt_path 指向与 GUI 同版本的 "
                             "ansysedt.exe，或关掉另一版本的 AEDT 后重试",
    "AEDT_SHELL_ONLY": "只剩 -Embedding 空壳（服务已自动清理）：重新 launch_aedt / open_hfss_project",
    "AEDT_PERMISSION_MISMATCH": "服务与 AEDT 的完整性级别不一致（UAC 隔离会让 COM 只连到空壳）："
                                "让两者以相同权限运行（关掉 AEDT，用与服务相同的权限重新启动）；"
                                "若刚启动过 AEDT，也可能是 GUI 还没注册完，重开该实例再试",
    # 2026-11-26：ANSYS 链路常见的另外四档（此前这些都退化成 do_not_retry，agent 只知道"别重试"）
    "INVALID_PATH": "路径不对或文件不存在：改传绝对路径（正斜杠更省事），并确认 .aedt/.sem/.epp 与目录都在",
    "OPEN_TIMEOUT": "限时内没确认打开：先 get_hfss_project_info 看是否其实已经打开（假失败），"
                    "再决定重试；冷启动 AEDT 可调大 timeout_seconds",
    "SOLVE_REQUIRED": "先跑完 setup 拿到求解证据（start_hfss_analysis_async / run_laspcem_solve_async），"
                      "再导出；details 里会给出已扫描的证据目录",
    "EXPORT_FAILED": "看 details 与 AEDT 日志的原始错误：确认 setup/sweep 名对、该 sweep 已求解、输出目录可写",
    # ── 任务/进程（此前退化成 do_not_retry，补 actionable 指引）──
    "TASK_NOT_FOUND": "任务不存在、已过期或服务已重启（任务状态在内存里，重启即丢）；重新提交，别当失败重试",
    "TASK_ALREADY_FINISHED": "任务已结束（成功/失败/取消），不能重复操作；用对应的 query/status 查最终结果",
    "TASK_CANCELLED": "任务已被取消，不是失败；如需结果重新提交",
    "LAUNCH_FAILED": "看 details 里的原始报错；确认目标软件已安装且路径正确（可用对应 launch_* 工具自动探测）",
    "EDI_NOT_FOUND": "显式传 edi_path，或先用 launch_edi 自动探测 EDI 安装目录",
    "LASPCEM_NOT_FOUND": "显式传 laspcem_path，或确认 LASPCEM 已安装（注册表 .sem 关联可探测）",
    "PROCESS_EXITED": "子进程已退出，看 details 里的 exit code 与 stderr 定位原因",
    # ── 器件/参数 ──
    "EMPTY_INSTANCE_NAME": "instance_name 不能为空；先用 list_schematic_components 拿准确的实例名",
    "AMBIGUOUS_INSTANCE_NAME": "实例名有歧义/重复；用 list_schematic_components 找到唯一实例名再传",
    "INVALID_ACTIVE_STATE": "state 只接受 NORMAL / DISABLED / SHORTED（大小写不敏感）",
    "CLEAR_CONFIRMATION_REQUIRED": "破坏性操作需显式 confirm_clear=True 二次确认才执行",
    "INVALID_PROJECT_PATH": "检查工程路径：必须是存在的 .epp 绝对路径，同目录需有 schematics/main/schematic.ep",
    "UNSUPPORTED_PARAMETER": "该器件类型不支持这个参数；先用 get_simulation_component_schema 查可写参数",
    "COMPONENT_TYPE_MISMATCH": "传的 component_type 与器件实际类型不符；用 list_schematic_components 核对",
    "COMPONENT_TYPE_REQUIRED": "该操作需要 component_type；用 list_schematic_components / get_schematic_component_info 查",
    "COMPONENT_SCHEMA_UNAVAILABLE": "该器件类型的参数目录不可用；确认 simulation_component_catalog.json 已随包部署，或换用其它器件类型",
    # ── 链路/仿真 ──
    "LINK_JSON_INVALID": "links 结构不合法，看 details 的 location/message 定位具体字段；字段名与单位照 describe_tool 的 schema",
    "NETLIST_EMPTY": "网表为空或没解析出器件；先 export_project_netlist 导出再核对",
    "INVALID_STATUS": "status 过滤值不合法；用 list_eda_tasks 不带参看全部，合法值见工具说明",
    # ── CST ──
    "CST_OPEN_FAILED": "归档 .cst 解包失败；确认文件存在且 CST 能打开（必要时先在 GUI 里打开一次）",
    "CST_INFO_FAILED": "读 CST 模型信息失败；确认模型已解包（cst_open_project）且 CST 已安装",
    "CST_PLOT_FAILED": "远场 txt 解析/绘图失败；确认 txt 是 cst_export_farfield 产物、cut 格式为 phi=0 或 theta=0",
    "CST_EXPORT_FAILED": "CST 导出失败；先 cst_get_model_info 确认已求解、有对应结果",
    # ── HFSS/AEDT ──
    "SETUP_NOT_FOUND": "setup 名不存在；用 get_hfss_project_info 查设计，传正确的 setup 名",
    "DESIGN_NOT_FOUND": "设计名不存在；用 get_hfss_project_info 查当前项目的设计列表",
    "ANALYSIS_BUSY": "已有 HFSS 仿真正在跑（AEDT 单实例串行）；等它完成或用 hfss_task_list 看进度",
    "TASK_LIMIT_REACHED": "HFSS 任务数达上限；等旧任务结束或 hfss_task_cancel 取消",
    "COM_ERROR": "AEDT COM 调用失败，看 details 原始错误；确认 AEDT 未卡死、版本匹配（aedt_path 与 GUI 同版本）",
    "INVALID_AEDT_PATH": "aedt_path 指向的 ansysedt.exe 不存在；用 launch_aedt 自动探测",
    "COM_OPEN_FAILED": "AEDT COM 附着失败；确认 AEDT 已启动（launch_aedt / open_hfss_project）",
    "EXISTING_INSTANCE_COM_UNAVAILABLE": "已有 AEDT 实例但 COM 附着不上；确认两者同权限（UAC 隔离会只连到空壳），或重开 AEDT",
    "NO_PROJECT_TO_CLOSE": "没有打开的项目可关；用 get_hfss_project_info 看当前状态",
    # ── LASPCEM ──
    "SOLVE_FAILED": "求解失败，看 details 原始日志；判成败看 steps 而非 laspcem_task_list 顶层 status（会误报）",
    "SOLVE_TIMEOUT": "求解超时；看 logs 判断是真慢还是卡死，必要时 laspcem_task_cancel",
    "SOLVE_CANCELLED": "求解已被取消，不是失败",
    "REPORT_FAILED": "建报告失败；确认已求解（没求解会回 SOLVE_REQUIRED）",
    "REPORT_TIMEOUT": "建报告超时；重试或查 logs",
    "IMPORT_FAILED": "导入 .aedt 失败；确认源文件存在、ansys_version 匹配",
    "READ_FAILED": "读 .sem 工程失败；确认文件存在且未被 LASPCEM 独占",
    # ── 报告/绘图/产物 ──
    "INVALID_REPORT_JSON": "报告 JSON 结构不合法；字段见 export_simulation_report 的 report 参数说明",
    "REPORT_EXPORT_FAILED": "报告导出失败，看 details 原始错误；确认图片路径是绝对路径",
    "DRAW_FAILED": "绘图失败，看 details 原始错误；确认 series/points 结构与 draw_chart schema 一致",
    "OUTPUT_DIRECTORY_NOT_FOUND": "输出目录不存在；先建目录或换一个已存在目录",
    "INVALID_RAW_DATA": "RAW 数据不合法/解析失败；先用 list_result_curves 确认曲线名与类型",
    "NO_OVERLAP": "多个 sNp 无重叠频点；换 alignment=interpolation 或核对频段",
    "SYNTHESIS_FAILED": "线宽综合未收敛；检查基板参数（h/er）与目标阻抗是否合理，或换传输线类型",
    "TOOL_EXECUTION_FAILED": "工具执行失败，看 details 原始错误",
    "INVALID_LINENAME": "曲线名格式不对；先 list_result_curves 拿准确「单位_线段名」",
    "WRITE_FAILED": "写文件失败；换可写目录（%TEMP% 或工作区）",
    "PATHFINDER_FAILED": "网表寻路失败，看 details；确认 netlist.log 与 schematic.ep 配对正确",
    # ── 视觉/文档 ──
    "VISION_NOT_CONFIGURED": "视觉模型未配置；在 .env 配 VISION_API_KEY / VISION_BASE_URL / VISION_MODEL",
    "VISION_TIMEOUT": "视觉模型超时；调大 vision_timeout 或重试",
    "VISION_PROVIDER_ERROR": "视觉模型返回错误，看 details；确认 model 名与 base_url 正确",
    "VISION_AUTH_FAILED": "视觉模型鉴权失败；核对 VISION_API_KEY",
    "VISION_RATE_LIMITED": "视觉模型限流；稍后重试",
    "VISION_BUSY": "视觉模型忙；稍后重试",
    "INVALID_VISION_RESPONSE": "视觉模型返回结构异常；换 prompt 或重试",
    "INVALID_IMAGE": "图片路径/内容非法；确认是本地 PNG/JPEG/WebP 绝对路径",
    "IMAGE_TOO_LARGE": "图片超过大小上限；压缩后重试",
    "UNSUPPORTED_IMAGE_FORMAT": "不支持的图片格式；用 PNG/JPEG/WebP",
    "UNSUPPORTED_FORMAT": "不支持的文档格式；open_document 支持 10 种办公格式（pdf/docx/xlsx/ppt/txt/csv 等）",
    "DEFAULT_APPLICATION_UNAVAILABLE": "系统没有打开该文件的默认程序；改用 mode=link 生成下载链接",
    # ── 日志 ──
    "LOG_NOT_FOUND": "日志文件不存在；确认服务已运行并产生日志",
    "LOG_READ_ERROR": "读日志失败；确认路径与权限",
}


def error_response(code: str, message: str, retryable: bool = False,
                   hint: str | None = None, **extra) -> dict[str, Any]:
    """构建工具统一错误响应。hint 为显式形参，避免被 **extra 吞进 details。"""
    if hint is None:
        hint = _DEFAULT_HINTS.get(code) or ("retry_safe" if retryable else "do_not_retry")
    result: dict[str, Any] = {
        "success": False, "error_code": code, "message": message,
        "hint": hint,
    }
    if retryable:
        result["retryable"] = True
    if extra:
        result.setdefault("details", {}).update(extra)
    return result


def submitted_response(task_id: str, *, status: str = "QUEUED",
                       message: str = "任务已提交", **extra: Any) -> dict[str, Any]:
    """构建异步任务提交成功响应（统一 task_id/status/message 结构）。"""
    return {"success": True, "task_id": task_id, "status": status,
            "message": message, "hint": "poll", **extra}


def queue_full_response(code: str = "QUEUE_FULL", retryable: bool = True,
                       message: str = "当前已有任务在进行，请稍后重试") -> dict[str, Any]:
    """构建异步任务队列满响应（统一走 error_response），message 可覆盖默认文案。"""
    return error_response(code, message, retryable=retryable)


# ── 运行时服务器地址 ──

@dataclass
class ServerAddress:
    """运行时服务器地址（host + port），由 start_servers.py 通过 set_server_address() 设置。"""
    host: str = "127.0.0.1"
    port: int = 50026


_address = ServerAddress()
_lock = threading.Lock()


def set_server_address(host: str, port: int) -> None:
    """在 start_servers.py 确定最终 host/port 后调用。"""
    with _lock:
        _address.host = host
        _address.port = port


# ── 请求上下文 —— 产物链接按请求 Host 推导 ──
# start_servers.py 的 RequestBaseURLMiddleware 在每个 HTTP 请求上写入。
# 同步工具与中间件跑在同一任务上下文里，所以工具内调用 get_server_base_url()
# 能拿到「客户端实际访问用的地址」；异步任务线程池不继承上下文，读到空串后回退。
_request_base_url: ContextVar[str] = ContextVar("mcp_request_base_url", default="")


def set_request_base_url(base_url: str) -> None:
    """记录当前请求的 base URL（形如 http://192.168.0.58:50026）。"""
    _request_base_url.set(base_url)


def current_request_base_url() -> str:
    """返回当前请求的 base URL；不在 HTTP 请求上下文中时返回空字符串。"""
    return _request_base_url.get()


def get_server_base_url() -> str:
    """返回当前 HTTP 服务的 base URL，供图片/文档 Token 链接使用。

    优先用当前请求的 Host（远程客户端据此拿到自己访问得通的地址）；
    无请求上下文时（异步任务、服务自身调用）回退到启动时确定的 host:port。
    **0.0.0.0/:: 仍映射回 127.0.0.1**：本机用法行为不变；远程场景下异步任务
    没有请求上下文，需要 MCP_PUBLIC_BASE_URL 兜底（本期未做）。
    """
    base = _request_base_url.get()
    if base:
        return base
    with _lock:
        host = _address.host
        port = _address.port
    public_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{public_host}:{port}"


# ── 监听地址与 DNS-rebinding 允许列表（远程访问）──

# 环回写法：SDK 内部默认允许列表用的就是这三种（+ 端口通配）
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def is_loopback_host(host: str) -> bool:
    """判断监听地址是否「仅本机」（默认配置）——决定是否需要重建允许列表。"""
    return (host or "").strip().lower() in _LOOPBACK_HOSTS


def host_without_port(host_header: str) -> str:
    """从 Host 头取主机部分：[::1]:50026 → [::1]；192.168.0.58:50026 → 192.168.0.58。

    无端口时原样返回；裸 IPv6（多个冒号、无方括号）也原样返回。
    """
    h = (host_header or "").strip()
    if h.startswith("["):
        return h[: h.index("]") + 1] if "]" in h else h
    if h.count(":") == 1:
        return h.rsplit(":", 1)[0]
    return h


def local_host_names() -> list[str]:
    """枚举本机可被客户端用作 Host 头的名字：环回 + 全部网卡 IP + 主机名/FQDN。

    不能用 socket.getaddrinfo(socket.gethostname())：实测它只回主网卡 IPv4，
    漏掉 VPN/虚拟网卡地址；psutil.net_if_addrs() 才是完整来源（含 VPN 网卡，
    适配器在用时才出现，down 时自然不在列表里 —— 这就是动态枚举的本意）。
    IPv6 非环回地址按 Host 头规范加方括号（[fe80::x]:50026），并去掉 Windows
    的 %scope 后缀；主机名给原样/小写/大写三种写法（Windows 客户端可能发大写，
    实测大写会被 421 拒）。
    """
    hosts: list[str] = list(_LOOPBACK_HOSTS)
    try:
        import psutil
        for _name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if not a.address:
                    continue
                if a.family == socket.AF_INET:
                    hosts.append(a.address)
                elif a.family == socket.AF_INET6:
                    addr = a.address.split("%", 1)[0]  # 去 Windows 的 %scope 后缀
                    if addr != "::1":                  # 环回已在 _LOOPBACK_HOSTS
                        hosts.append(f"[{addr}]")
    except Exception:
        pass  # psutil 缺失/权限不足 → 降级为环回 + 主机名
    try:
        hn = socket.gethostname()
        if hn:
            hosts += [hn, hn.lower(), hn.upper()]
        fqdn = socket.getfqdn()
        if fqdn and fqdn not in hosts:
            hosts += [fqdn, fqdn.lower()]
    except OSError:
        pass
    return list(dict.fromkeys(h for h in hosts if h))


def build_transport_security(extra_hosts: Iterable[str] = ()):
    """构建 DNS-rebinding 允许列表：本机全部可达地址 + 额外配置的 host。

    每个 host 同时生成「裸名」与「裸名:*」两种模式：SDK 用 host.startswith(base + ":")
    匹配带端口的 Host，裸名模式覆盖不带端口的边界情况。

    **不关闭** enable_dns_rebinding_protection —— 关掉后任意伪造 Host（实测
    Host: evil.example.com）都会通过，会污染产物链接、把 token 递给别人。
    allowed_origins 保持为空（不额外放行浏览器跨源）；本服务客户端不发送 Origin。
    """
    from mcp.server.transport_security import TransportSecuritySettings

    names = local_host_names()
    for h in extra_hosts:
        h = str(h).strip()
        if h and h not in names:
            names.append(h)
    patterns: list[str] = []
    for h in names:
        patterns.append(h)
        patterns.append(f"{h}:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=patterns,
    )


# ── 产物与文件链接 ──

def _artifact_stamp() -> str:
    """默认产物名用的时间戳，精确到毫秒。

    只用 `%Y%m%d_%H%M%S` 时同秒内连续调用会撞同一路径（实测：循环出图前几张就撞），
    产物互相覆盖且难排查。加毫秒后常见调用间隔（>1ms）都不会撞。
    """
    now = time.time()
    return time.strftime("%Y%m%d_%H%M%S", time.localtime(now)) + f"_{int(now * 1000) % 1000:03d}"


def result_dir() -> str:
    """返回默认产物目录（`settings.mcp_result_dir` 或系统临时目录下的 `mcp_result`）。

    默认是 `%TEMP%/mcp_result`（`tempfile.gettempdir()`，本机即
    `C:\\Users\\<用户>\\AppData\\Local\\Temp\\mcp_result`）—— 产物属中间文件，
    放系统临时目录，不污染用户主目录。

    绝对化后再建：`MCP_RESULT_DIR` 若配成相对路径，不能让产物跟着服务进程的 cwd 跑
    （从不同目录启动服务就会落到不同地方）。
    """
    from servers.settings import get_settings
    base = ((get_settings().mcp_result_dir or "").strip()
            or str(Path(tempfile.gettempdir()) / "mcp_result"))
    resolved = Path(base).expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return str(resolved)


def default_result_path(tool_name: str, extension: str = "") -> str:
    """生成默认产物文件路径：`<result_dir>/<工具名>_<时间戳>[_序号]<扩展名>`。

    时间戳到毫秒；同毫秒重复调用再追加 `_1`/`_2`… 保证不覆盖已有文件。
    `extension` 形如 `.png`，少写点号会自动补（避免出现 `x_20261116_120000png`）。
    """
    ext = (extension or "").strip()
    if ext and not ext.startswith("."):
        ext = "." + ext
    base = Path(result_dir())
    stamp = _artifact_stamp()
    for i in range(100):
        suffix = "" if i == 0 else f"_{i}"
        candidate = base / f"{tool_name}_{stamp}{suffix}{ext}"
        if not candidate.exists():
            return str(candidate)
    return str(base / f"{tool_name}_{stamp}_overflow{ext}")     # 极端情况兜底，不抛异常


def result_subdir(tool_name: str) -> str:
    """生成默认产物子目录（原子创建）：`<result_dir>/<工具名>_<时间戳>[_序号]/`。

    不带 `exist_ok`：撞名时 `mkdir` 抛 `FileExistsError` 再加序号重试 —— 这是原子的，
    并发调用也不会两个线程拿到同一个目录（多文件/目录类产物混在一起很难排查）。

    建好的目录会登记到当前线程的「本次调用」栈上，供 `prune_empty_default_dirs`
    在工具失败时回收（见下）。
    """
    base = Path(result_dir())
    stamp = _artifact_stamp()
    for i in range(100):
        suffix = "" if i == 0 else f"_{i}"
        candidate = base / f"{tool_name}_{stamp}{suffix}"
        try:
            candidate.mkdir(parents=True)
        except FileExistsError:
            continue
        _remember_created_dir(str(candidate))
        return str(candidate)
    raise RuntimeError(f"无法为 {tool_name} 创建唯一产物目录（{stamp} 后已试 100 次）")


# ── 默认产物目录的空目录回收 ──
_result_dirs_tls = threading.local()


def _created_result_dirs() -> list[str]:
    """当前线程里「本次调用期间」由 `result_subdir` 建过的目录（栈）。"""
    dirs = getattr(_result_dirs_tls, "created", None)
    if dirs is None:
        dirs = _result_dirs_tls.created = []
    return dirs


def _remember_created_dir(path: str) -> None:
    _created_result_dirs().append(path)


def discard_empty_dir(path: str) -> None:
    """删掉「建了但一个文件都没写进去」的默认产物目录。

    只删空目录：非空（已有产物）、不存在、被占用一律静默跳过 —— 清理是尽力而为，
    绝不能因为清理失败把工具的真实错误盖掉。
    """
    try:
        p = Path(path)
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    except OSError:
        pass


def prune_empty_default_dirs(fn):
    """工具装饰器：本次调用失败时，回收它刚建的空默认产物目录。

    背景（2026-11-16 实测）：`result_subdir()` 是「先建目录再执行」，工具在校验失败 /
    上游未就绪时提前返回，`%TEMP%/mcp_result` 下就攒一串空的 `<工具名>_<时间戳>/`
    （实测残留 `cst_export_snp_*` 2 个、`export_simulation_report_*` 4 个）。

    **只按返回值判**：`success` 为真的调用一律不动 —— 异步工具（如 `cst_export_farfield`
    的 `submitted_response`）提交成功时目录还是空的，后台 worker 马上会往里写，不能删。
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        buf = _created_result_dirs()
        depth = len(buf)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            for d in buf[depth:]:
                discard_empty_dir(d)
            del buf[depth:]
            raise
        made = buf[depth:]
        del buf[depth:]
        if isinstance(result, dict) and not result.get("success"):
            for d in made:
                discard_empty_dir(d)
        return result

    # ⚠️ 见 _signature_for_schema：不加这个，pydantic 类型参数在 tools/list 里退化成自由 object
    if (sig := _signature_for_schema(fn)) is not None:
        wrapper.__signature__ = sig
    return wrapper


def build_artifact(type_: str, path: str, generated_by: str) -> dict[str, Any]:
    """构建统一产物条目（用于 artifacts 数组）。"""
    return {"type": type_, "path": path, "name": Path(path).name,
            "generated_by": generated_by}


#: 仿真工具每次运行都会产出的报告类文件后缀（必须优先暴露，不能被明细文件挤掉）
SIM_REPORT_SUFFIXES = (".pdf", ".docx")

#: 截断时的扩展名价值权重（越小越先保留）：报告 > 图表/CSV > RAW > 日志。
#: 没有这层权重时，层级浅的 netlist.log 会先把名额占满，把 result.raw 这类成果挤掉。
_SIM_EXT_WEIGHT = {".pdf": 0, ".docx": 1, ".png": 2, ".jpg": 2, ".csv": 3, ".raw": 4, ".log": 5}


def collect_simulation_artifacts(sim_path: str, tool_name: str,
                                 key_ext: dict[str, str],
                                 max_artifacts: int = 40,
                                 ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """把仿真结果目录整理成 artifacts 列表。

    返回 ``(artifacts, report_artifacts, truncated)``。

    背景（2026-10-21 实测，用户反馈）：Mixer_Simulator 每次跑完都会在结果目录**顶层**
    生成 ``<工程名>_仿真报告.pdf`` / ``.docx``（含指标判定表、曲线、拓扑图）；
    TR_Simulator 本身不生成报告，但目录里若已有 PDF/DOCX（独立报告工具产物）也应
    同样优先暴露。明细文件在 ``<链路名>/conditions/<工况>/…`` 子目录里。原先 artifacts
    按路径字符串排序后截断到 40 条 —— 子目录里的 raw/log/csv/png 会先把名额吃满，
    报告被**静默截断**，调用方（模型与人都一样）根本不知道报告已经生成，只能自己去
    翻磁盘目录。

    现在的排序优先级：
        1. 报告类文件（.pdf/.docx）永远在前；
        2. 层级浅的在前（顶层判定表/结果主表/schematic.png 先于 conditions 明细）；
        3. 同层再按路径字典序，保证输出稳定可复现。

    同时把报告单独返回一份 ``report_artifacts``，供工具在响应字段与 message 里
    显式告知调用方「这次跑出了报告」，而不是指望对方去数 artifacts。
    """
    artifacts: list[dict[str, Any]] = []
    if not sim_path or not Path(sim_path).is_dir():
        return artifacts, [], False

    root = Path(sim_path)
    # 目录本身拿不到 token 下载链接 ⇒ 关键文件也要单列，远程客户端才能下载产物。
    artifacts.append(build_artifact("directory", sim_path, tool_name))

    key_files = sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in key_ext),
        key=lambda p: (
            0 if p.suffix.lower() in SIM_REPORT_SUFFIXES else 1,
            # 报告内部再按 SIM_REPORT_SUFFIXES 的顺序排（PDF 在 DOCX 之前，交付时优先给 PDF）
            SIM_REPORT_SUFFIXES.index(p.suffix.lower())
            if p.suffix.lower() in SIM_REPORT_SUFFIXES else 0,
            # 截断前先按「产物价值」分层：图表/CSV > RAW > 日志，避免浅层 log 挤掉成果
            _SIM_EXT_WEIGHT.get(p.suffix.lower(), 9),
            len(p.relative_to(root).parts),
            str(p),
        ),
    )
    report_files = [p for p in key_files if p.suffix.lower() in SIM_REPORT_SUFFIXES]
    report_artifacts = [
        build_artifact(key_ext[p.suffix.lower()], str(p), tool_name) for p in report_files
    ]
    for f in key_files[:max_artifacts]:
        artifacts.append(build_artifact(key_ext[f.suffix.lower()], str(f), tool_name))
    return artifacts, report_artifacts, len(key_files) > max_artifacts


MAX_QUERY_WAIT_SECONDS = 120


def _default_task_done(snap: dict | None) -> bool:
    """默认终态判定：快照缺失 / 有 finished_at / 已取消。"""
    return (snap is None or bool(snap.get("finished_at"))
            or snap.get("status") == "CANCELLED")


def bounded_wait(task_id: str, wait_seconds: int, snapshot, is_done=None,
                 sleep=None, clock=None, max_wait: int = MAX_QUERY_WAIT_SECONDS,
                 interval: float = 2.0) -> None:
    """阻塞轮询到任务终态或超时，让客户端一次调用就拿到结果（省掉 sleep 轮询）。

    为什么必须有上限：任务执行层是单槽 FIFO，一次调用阻塞太久会堵住其它工具调用；
    MCP 客户端自身也有超时，几十分钟的等待照样会断。所以「短任务一次拿结果、
    长任务继续轮询」。

    Args:
        task_id: 任务 ID。
        wait_seconds: 期望等待秒数，clamp 到 [0, max_wait]；0/负数 = 立即返回。
        snapshot: callable(task_id) -> dict|None，返回任务快照。
        is_done: callable(snapshot) -> bool；缺省判定「None / finished_at 非空 /
            status == CANCELLED」。
        sleep / clock: 可注入，便于单测（默认 time.sleep / time.monotonic）。
    """
    sleep = time.sleep if sleep is None else sleep
    clock = time.monotonic if clock is None else clock
    wait = max(0, min(int(wait_seconds or 0), int(max_wait)))
    if wait <= 0:
        return
    is_done = _default_task_done if is_done is None else is_done
    deadline = clock() + wait
    while clock() < deadline:
        if is_done(snapshot(task_id)):
            return
        sleep(interval)


def build_file_link(path: str, label: str = "打开文件", *,
                    ttl: int = 3600, disposition: str = "inline") -> dict:
    """为本地文件生成 file:// URI、可下载 token 链接和 Markdown 链接。

    file_uri 是服务端本机路径（仅同机可靠）；download_url / markdown_link 指向
    token 链接（远程也能下载）。注册失败时 markdown_link 回退到 file:// 并标注
    「仅引擎机有效」。

    ttl / disposition 透传给 token 注册（默认 3600 秒、inline）；需要按格式区分
    预览/下载时由调用方显式传，例如报告：PDF→inline、DOCX→attachment。

    media_tag 是可直接粘进聊天正文内联显示的 ``MEDIA:"<路径>"`` 串。路径一律加
    双引号：客户端按 ``\\S+`` 解析裸路径，含空格会被空格截断（2026-11-25 实测
    ``D:\\EDI\\...\\Dual Conversion Link HB\\document.pdf`` 被切成 ``...\\Dual``）。
    """
    p = Path(path).resolve()
    uri = p.as_uri()
    download_url = ""
    if p.is_file():
        try:
            # 延迟 import 避免循环依赖（document.py 模块级 import 本模块）
            from servers.multimodal_vision.document import _register_any_file_url
            download_url = _register_any_file_url(str(p), ttl=ttl, disposition=disposition)
        except Exception:
            download_url = ""
    markdown_link = (
        f"[{label}]({download_url})"
        if download_url
        else f"[{label}]({uri})（仅引擎机有效）"
    )
    return {
        "file_uri": uri,
        "download_url": download_url,
        "markdown_link": markdown_link,
        "media_tag": f'MEDIA:"{p}"',
    }


# ── MCP 工具注册表内省 ──

def registered_tools(mcp) -> list:
    """返回 FastMCP 已注册工具对象列表。

    隔离对 FastMCP 私有属性 ``mcp._tool_manager._tools`` 的依赖：SDK 升级若改动
    内部结构，只需在此处适配，而不是改散落在多个文件的各处访问。
    """
    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", {}) if tool_manager is not None else {}
    return list(tools.values())
