"""公共工具层 — 文件/参数校验、统一响应构建、运行时地址、产物与文件链接。

全项目复用的基础函数，无外部依赖。
"""

from __future__ import annotations

import functools
import math
import os
import socket
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


def per_tool_mutex(fn):
    """给工具函数加一把独立互斥锁。

    同步工具统一 offload 到工作线程后，同类工具并发会争用共享资源（turbocharts
    子进程、Matplotlib 全局状态、报告渲染服务）。此装饰器给每个被装饰函数一把
    独立锁，同类工具互斥、不同工具互不阻塞。
    """
    lock = threading.Lock()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with lock:
            return fn(*args, **kwargs)

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

def error_response(code: str, message: str, retryable: bool = False,
                   hint: str | None = None, **extra) -> dict[str, Any]:
    """构建工具统一错误响应。hint 为显式形参，避免被 **extra 吞进 details。"""
    result: dict[str, Any] = {
        "success": False, "error_code": code, "message": message,
        "hint": hint if hint is not None else ("retry_safe" if retryable else "do_not_retry"),
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

def build_artifact(type_: str, path: str, generated_by: str) -> dict[str, Any]:
    """构建统一产物条目（用于 artifacts 数组）。"""
    return {"type": type_, "path": path, "name": Path(path).name,
            "generated_by": generated_by}


def build_file_link(path: str, label: str = "打开文件") -> dict:
    """为本地文件生成 file:// URI 和 Markdown 链接。

    只在 MCP 服务与客户端同机时可靠。
    """
    p = Path(path).resolve()
    uri = p.as_uri()
    return {
        "file_uri": uri,
        "markdown_link": f"[{label}]({uri})",
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
