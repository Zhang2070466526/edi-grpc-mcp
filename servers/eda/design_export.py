r"""EDA 分析工具 — 网表导出 + 原理图截图（gRPC 调用）。

capture_schematic  截取工程原理图并保存为图片 返回 artifacts（含 build_file_link），图片生成后自动添加 file:// 链接。
export_project_netlist  查看/导出 .epp 工程的网表文件

自然语言使用示例：
  帮我查看 EDA 工程 C:\...\EDI_TEST.epp 的网表
  帮我截取这个工程的原理图，保存到 C:\screenshots\circuit.png
  帮我导出这个工程的网表，超时设为 120 秒

参数说明：
  project_path     EDA 服务所在机器上的 .epp 工程文件绝对路径
  img_path         截图输出路径，支持 PNG/JPG 等（capture_schematic）
  timeout_seconds  最长等待秒数，无上限，默认 60 秒
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from proto import ecserver_pb2
from servers.eda.grpc_client import call_grpc
from servers.eda.config import validate_project_path
from servers.utils import build_file_link
from servers import mcp


@mcp.tool()
def export_project_netlist(
    project_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """查看 EDA .epp 工程的网表，返回网表文件路径。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    resolved_path = validate_project_path(project_path)
    return call_grpc(
        ecserver_pb2.VIEW_PROJECT_NETLIST,
        {"project_path": resolved_path},
        timeout_seconds,
        max_timeout_seconds=300,
    )


@mcp.tool()
def capture_schematic(
    project_path: str,
    img_path: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """截取 EDA 工程原理图为图片。

    Args:
        project_path: EDA 服务所在机器上的 .epp 工程文件绝对路径。
        img_path: 输出图片路径，支持 PNG/JPG 等。
        timeout_seconds: 最长等待时间，默认 60 秒。
    """
    resolved_path = validate_project_path(project_path)
    # Basic path validation: resolve and check output extension
    img_resolved = str(Path(img_path).expanduser().resolve())
    img_ext = Path(img_resolved).suffix.lower()
    if img_ext not in (".png", ".jpg", ".jpeg", ".bmp", ".svg"):
        return {"success": False,
                "error_code": "INVALID_PATH",
                "message": f"img_path 扩展名不支持: {img_ext}，请使用 PNG/JPG/BMP/SVG"}

    result = call_grpc(
        ecserver_pb2.CAPTURE_SCHEMATIC,
        {"project_path": resolved_path, "img_path": img_resolved},
        timeout_seconds,
        max_timeout_seconds=300,
    )
    img_ok = Path(img_resolved).is_file()
    if result.get("success") and img_ok:
        result["img_generated"] = True
        result["artifacts"] = [{"type": "image", "path": img_resolved,
                                "name": Path(img_resolved).name,
                                "generated_by": "capture_schematic"}]
        result["message"] = "原理图已截图。"
        result.update(build_file_link(img_resolved, "打开原理图"))
    return result


# ═══════════════════════════════════════════════════════════
# 信号链路追踪（节点接力算法）
# ═══════════════════════════════════════════════════════════

import re  # noqa: E402

# 仿真控制块行类型（跳过）；其余带冒号的行都视为器件/端口行（不穷举器件类型，
# 以兼容 AmplifierDevice/LinearDevice/Attenuator/Port 及未来的无源器件等）
_CONTROL_BLOCK_TYPES = ("S_Param", "SweepPlan", "OutputPlan", "HB", "Tran", "Component")
# 信号节点命名：N__数字、Out数字（命名网络）、PowerPin_数字（抗烧毁污染）
_SIGNAL_NODE_RE = re.compile(r"^(N__\d+|Out\d*|PowerPin_\d+)$")


def _acquire_netlist(project_path: str, timeout_seconds: int) -> tuple[str | None, str]:
    """获取网表内容：优先本地读 netlist.log，失败降级 gRPC 现场导出。返回 (text, warning)。"""
    local = Path(project_path).parent / "netlist.log"
    if local.is_file():
        try:
            return local.read_text(encoding="utf-8", errors="replace"), ""
        except OSError as exc:
            return None, f"读取本地网表失败: {exc}"

    # 降级：gRPC 现场导出网表
    try:
        result = call_grpc(
            ecserver_pb2.VIEW_PROJECT_NETLIST,
            {"project_path": project_path},
            timeout_seconds,
            max_timeout_seconds=300,
        )
        details = result.get("details", {})
        netlist_path = details.get("netlist_path", "") or result.get("result_path", "")
        if netlist_path and Path(netlist_path).is_file():
            text = Path(netlist_path).read_text(encoding="utf-8", errors="replace")
            return text, "已通过 gRPC 现场导出网表"
    except Exception as exc:
        return None, f"网表获取失败: {exc}"

    return None, "网表不存在，请先保存/导出网表"


def _parse_netlist(text: str) -> tuple[dict, dict, dict]:
    """解析网表 → (comps, node_map, meta)。

    comps: {instance: {"type", "model", "pins", "role"}}
    node_map: {node: [instance 列表]}  节点 → 连在该节点上的器件
    meta: {"skipped_lines", "warning"}
    """
    comps: dict[str, dict] = {}
    node_map: dict[str, list] = {}
    skipped_lines = 0
    powerpin_count = 0

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            skipped_lines += 1
            continue

        # 行类型在冒号前
        if ":" not in line:
            skipped_lines += 1
            continue
        line_type = line.split(":", 1)[0]
        if line_type in _CONTROL_BLOCK_TYPES:
            skipped_lines += 1
            continue

        rest = line.split(":", 1)[1].strip()
        tokens = rest.split()
        if not tokens:
            skipped_lines += 1
            continue

        instance = tokens[0]
        # 引脚：实例名之后、第一个含 "=" 的 token 之前；Model 在参数区（含 "=" 的 token）
        pins: list[str] = []
        model = ""
        pin_done = False
        for tok in tokens[1:]:
            if not pin_done and "=" not in tok:
                pins.append(tok)
            else:
                pin_done = True
                if tok.startswith("Model="):
                    model = tok.split("=", 1)[1].strip('"')

        # 信号节点：N__数字 / Out数字 / PowerPin_数字；0 = 地
        signal_pins = [p for p in pins if _SIGNAL_NODE_RE.match(p)]
        if any(p.startswith("PowerPin_") for p in pins):
            powerpin_count += 1

        role = "device"
        if line_type == "Port":
            role = "source" if "P[1]=" in line else "load"

        comps[instance] = {
            "type": line_type,
            "model": model,
            "pins": signal_pins,
            "role": role,
        }
        for node in signal_pins:
            node_map.setdefault(node, []).append(instance)

    meta = {
        "skipped_lines": skipped_lines,
        "warning": "",
    }
    if powerpin_count > 0:
        meta["warning"] = f"网表含 PowerPin 污染（{powerpin_count} 处），已容错当作普通节点处理"

    return comps, node_map, meta


def _trace_chain(
    comps: dict,
    node_map: dict,
    start_instance: str,
    max_depth: int,
) -> tuple[list, int, bool]:
    """节点接力追踪信号链路。返回 (chain, branch_count, truncated)。

    从起点沿信号节点接力，方向由起点角色隐式决定（源→下游、负载→上游）。
    chain: 按信号流方向排列的实例名列表
    """
    if start_instance not in comps:
        return [], 0, False

    chain = [start_instance]
    visited_inst = {start_instance}
    entry_node: str | None = None
    branch_count = 0
    truncated = False

    current = start_instance
    for _ in range(max_depth):
        comp = comps[current]
        # 当前器件的其它信号节点（排除进入节点）
        signal_nodes = [p for p in comp["pins"] if p != entry_node]

        next_inst: str | None = None
        next_entry: str | None = None
        branches_here = 0

        for node in signal_nodes:
            neighbors = [c for c in node_map.get(node, []) if c not in visited_inst]
            if not neighbors:
                continue
            if len(neighbors) > 1:
                branches_here += len(neighbors) - 1
            if next_inst is None:
                next_inst = neighbors[0]
                next_entry = node

        branch_count += branches_here

        if next_inst is None:
            break  # 悬空 / 到负载

        chain.append(next_inst)
        visited_inst.add(next_inst)
        current = next_inst
        entry_node = next_entry
    else:
        truncated = True

    return chain, branch_count, truncated


@mcp.tool()
def get_signal_chain(
    project_path: str,
    start_component: str = "",
    direction: str = "forward",
    max_depth: int = 40,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """追踪工程原理图的信号链路（节点接力算法，从源到负载）。

    用法："这个工程的信号是怎么走的"、"从 PORT1 开始追踪信号链路"

    读取网表，按「节点↔器件交替接力」追踪信号流方向，返回单条链路
    （功分器等多输出场景记录分支数，主链取第一路）。v1 只返回链路结构，
    不含增益/插损等规格。

    Args:
        project_path: .epp 工程文件绝对路径。
        start_component: 起始器件实例名，留空自动找激励源（无激励源则取第一个端口）。
        direction: forward（默认，向下游）/ backward（向上游）。
        max_depth: 最大追踪深度，默认 40（防环路死循环）。
        timeout_seconds: gRPC 网表导出超时，默认 60。

    Returns:
        {"success": True, "start": "PORT1", "chain": [
            {"instance": "PORT1", "type": "Port", "role": "source", "model": ""},
            {"instance": "...", "type": "AmplifierDevice", "role": "device", "model": "..."},
            {"instance": "TermG2", "type": "Port", "role": "load", "model": ""}],
         "branch_count": 0, "truncated": false, "warning": "...", "skipped_lines": 8}
    """
    resolved = validate_project_path(project_path)
    max_depth = max(1, min(int(max_depth), 100))

    text, warn = _acquire_netlist(resolved, timeout_seconds)
    if text is None:
        return {"success": False, "error_code": "NETLIST_NOT_FOUND", "message": warn}

    comps, node_map, meta = _parse_netlist(text)

    if not comps:
        return {"success": False, "error_code": "NETLIST_EMPTY",
                "message": "网表中没有器件/端口（可能是空工程或网表未生成）"}

    # 起点处理：未指定时自动找激励源，无激励源则取第一个端口
    if not start_component:
        sources = [i for i, c in comps.items() if c["role"] == "source"]
        if sources:
            start_component = sources[0]
        else:
            ports = [i for i, c in comps.items() if c["type"] == "Port"]
            if ports:
                start_component = ports[0]

    if start_component not in comps:
        return {"success": False, "error_code": "COMPONENT_NOT_FOUND",
                "message": f"网表中未找到起始器件: {start_component}"}

    chain, branch_count, truncated = _trace_chain(
        comps, node_map, start_component, max_depth,
    )

    chain_detail = []
    for inst in chain:
        c = comps[inst]
        chain_detail.append({
            "instance": inst,
            "type": c["type"],
            "role": c["role"],
            "model": c.get("model", ""),
        })

    warning = "；".join(x for x in (warn, meta.get("warning", "")) if x)

    return {
        "success": True,
        "start": start_component,
        "direction": direction,
        "chain": chain_detail,
        "branch_count": branch_count,
        "truncated": truncated,
        "warning": warning,
        "skipped_lines": meta.get("skipped_lines", 0),
    }
