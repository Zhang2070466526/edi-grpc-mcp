"""TR 仿真集成工具 —— 封装 SimulationAgent 的 17 个 tr_* 工具为 MCP 工具。

每个函数 1:1 映射到 `/api/v1/integration/tools` 返回的工具。参数默认值逐字对齐上游：
- 上游 `default=None`（无默认）→ MCP 参数必填（无 Python 默认值）；
- 上游 `default=<值>` → MCP 参数可选，用同款默认值。

调用返回 queued/running 状态时，统一由 client 的 call_tool 内部轮询到终态后同步返回
（不区分具体是哪些工具，轮询逻辑在 client 统一处理，而非仅限某几个后台工具）。
"""

from __future__ import annotations

from typing import Any

from servers import mcp
from servers.tr_simulation.client import call_tool
from servers.utils import error_response


@mcp.tool()
def tr_get_workflow_state(project_id: str = "", input_port: int = 0, output_port: int = 0,
                          indicator: str = "", include_attempts: bool = False,
                          band_name: str = "") -> dict[str, Any]:
    """查询当前会话持久化的计划、工程、链路、指标、尝试和报告状态。

    恢复旧会话、继续未完成仿真、纠错、重跑指标或重新生成报告前必须先调用，不要依赖
    较早对话中出现过的文件路径。默认返回最新状态、成功数值和产物路径；检查全部修订
    和失败原因时设置 include_attempts=true。按端口查询时两个端口必须同时提供。

    注意：返回结构随工作流阶段变化——tr_set_workflow_plan 之后与 tr_execute_simulation_plan
    之后，indicators 字段结构不同（前者以 planned_parameters 为主，后者以 direct_result
    和 attempts 为主），解析时需兼容两种形态。

    Args:
        project_id: 工程 ID（默认空，查询全部）。
        input_port: 输入端口号（默认 0，不按端口筛选）。
        output_port: 输出端口号（默认 0，不按端口筛选）。
        indicator: 指标名（默认空，查询全部）。
        include_attempts: 是否返回每项修订和失败原因（默认 False）。
        band_name: 频段名（默认空，返回该端口对所有频段链路）。
    """
    return call_tool("tr_get_workflow_state", {
        "project_id": project_id, "input_port": input_port, "output_port": output_port,
        "indicator": indicator, "include_attempts": include_attempts, "band_name": band_name,
    })


@mcp.tool()
def tr_set_workflow_plan(project_id: str, links_json: str) -> dict[str, Any]:
    """持久化用户已经确认的完整链路仿真计划。

    调用 tr_find_paths 并取得用户最终确认后、第一次修改网表前必须调用。本工具保存
    所有链路、端口、指标、共同参数和指标目标，使尚未开始的指标不只会停留在对话上下文。

    Args:
        project_id: 工程 ID（来自 tr_get_workflow_state）。
        links_json: 链路计划 JSON 字符串，每项含 name/input_port/output_port/indicators/
            parameters/requirements。
    """
    return call_tool("tr_set_workflow_plan", {"project_id": project_id, "links_json": links_json})


@mcp.tool()
def tr_get_simulation_capabilities(indicator: str = "") -> dict[str, Any]:
    """查询 TR 仿真实际支持的指标、单位、结果语义和必需参数。

    参数确认前如不确定支持范围或条件参数，应先调用本工具。indicator 为空时返回全部
    可选指标；传标准中文指标名时只返回该指标。

    Args:
        indicator: 指标名（默认空字符串，返回全部可选指标）。
    """
    return call_tool("tr_get_simulation_capabilities", {"indicator": indicator})


@mcp.tool()
def tr_read_netlist(epp_path: str = "", netlist_path: str = "", start_line: int = 1,
                    max_lines: int = 400) -> dict[str, Any]:
    """读取 EDI 当前网表，或者分页读取 Agent 已创建的网表修订版。

    读取 EDI 时传 epp_path；检查修改结果或错误恢复时传 netlist_path（须属于当前会话）。
    两个路径至少提供一个。返回 netlist_path、总行数、当前行范围、has_more 和 content。

    Args:
        epp_path: EPP 工程绝对路径（读 EDI 网表时，默认空）。
        netlist_path: 网表文件路径（读会话内修订版时，默认空）。
        start_line: 起始行（从 1 开始，默认 1）。
        max_lines: 最多返回行数（默认 400，范围 1~1000）。
    """
    if not epp_path.strip() and not netlist_path.strip():
        return error_response("MISSING_REQUIRED_ARGUMENT", "epp_path 与 netlist_path 至少提供一个")
    return call_tool("tr_read_netlist", {
        "epp_path": epp_path, "netlist_path": netlist_path,
        "start_line": start_line, "max_lines": max_lines,
    })


@mcp.tool()
def tr_find_paths(epp_path: str) -> dict[str, Any]:
    """查找 EPP 的端口和全部有效有向端口组合，每个端口对只取第一条路径。

    返回 ports、paths 和只读快照 netlist_path。返回的 netlist_path 可直接作为首次
    tr_modify_netlist 的 source_netlist_path。用户没有唯一指定端口时必须先调用本工具。

    Args:
        epp_path: EPP 工程绝对路径。
    """
    return call_tool("tr_find_paths", {"epp_path": epp_path})


@mcp.tool()
def tr_restore_schematic(epp_path: str) -> dict[str, Any]:
    """把 EDI 工程原理图整体回退到本会话初始化时备份的最初状态。

    读取工程首次初始化时备份的原始 schematic.ep，整体替换 EDI 当前原理图并保存。
    不影响会话内网表快照、修订和仿真记录，仅用于对原理图同步做彻底回退。

    Args:
        epp_path: EPP 工程绝对路径。
    """
    return call_tool("tr_restore_schematic", {"epp_path": epp_path}, requires_confirmation=True)


@mcp.tool()
def tr_modify_netlist(epp_path: str, source_netlist_path: str, input_port: int = 1,
                      output_port: int = 2, indicator: str = "", band_name: str = "",
                      min_freq: float = 0.0, max_freq: float = 0.0,
                      phase_step: float = 5.625, atten_step: float = 0.5,
                      phase_devices_json: str = "[]", atten_devices_json: str = "[]",
                      freq_rx: float = -1.0, pwr_rx: float = -20.0,
                      freq_pout: float = -1.0, pwr_pout: float = -20.0,
                      replacements_json: str = "[]") -> dict[str, Any]:
    """创建可追溯的新网表修订版，设置第一条有效链路并注入指标控制器。

    本工具绝不覆盖 EDI 原网表。首次修改应使用 tr_read_netlist 或 tr_find_paths 返回的
    快照。返回 netlist_path、indicator、端口、频率、switch_settings、ac 和
    manual_replacements，后续仿真使用返回的 netlist_path。

    Args:
        epp_path: EPP 工程绝对路径。
        source_netlist_path: 源网表路径（未修改快照或上次修订版）。
        input_port: 输入端口号（默认 1）。
        output_port: 输出端口号（默认 2）。
        indicator: 指标名（默认空）。
        band_name: 频段名（默认空，非分段仿真留空）。
        min_freq: 最小频率（默认 0.0）。
        max_freq: 最大频率（默认 0.0）。
        phase_step: 移相步进（默认 5.625）。
        atten_step: 衰减步进（默认 0.5）。
        phase_devices_json: 需要扫参的移相器**实例名** JSON 数组字符串，如 '["PhaseShifterSML2"]'（默认 "[]"）。
        atten_devices_json: 需要扫参的衰减器**实例名** JSON 数组字符串（默认 "[]"）。
        freq_rx: 接收频率（默认 -1.0，<=0 取频段中点）。
        pwr_rx: 接收功率（默认 -20 dBm）。
        freq_pout: 输出频率（默认 -1.0）。
        pwr_pout: 输出功率（默认 -20 dBm）。
        replacements_json: 精确替换操作组成的 JSON 数组字符串，每项 {"old_text","new_text","expected_count"}，通常传 "[]"。
    """
    return call_tool("tr_modify_netlist", {
        "epp_path": epp_path, "source_netlist_path": source_netlist_path,
        "input_port": input_port, "output_port": output_port, "indicator": indicator,
        "band_name": band_name, "min_freq": min_freq, "max_freq": max_freq,
        "phase_step": phase_step, "atten_step": atten_step,
        "phase_devices_json": phase_devices_json, "atten_devices_json": atten_devices_json,
        "freq_rx": freq_rx, "pwr_rx": pwr_rx, "freq_pout": freq_pout,
        "pwr_pout": pwr_pout, "replacements_json": replacements_json,
    })


@mcp.tool()
def tr_execute_simulation_plan(epp_path: str, source_netlist_path: str,
                               plan: list) -> dict[str, Any]:
    """按已确认的计划批量执行多个指标：每项自动完成网表修订→ADS 仿真→RAW 解析→登记。

    适用于参数经用户确认后的首轮多指标仿真，一次调用替代多次「修改-仿真-解析」往返。
    工具内部逐项顺序执行，单项失败不中断其余指标。

    Args:
        epp_path: EPP 工程绝对路径。
        source_netlist_path: 干净网表快照路径（每项都从该快照开始）。
        plan: 指标计划数组。每项是「平铺」字段（与 tr_modify_netlist 一致）：indicator（单数）、
            input_port、output_port、band_name、min_freq、max_freq、phase_step、atten_step、
            phase_devices_json、atten_devices_json、freq_rx、pwr_rx、freq_pout、pwr_pout。
            不是 tr_set_workflow_plan 的嵌套 {name, indicators, parameters, requirements}。
            最小示例（1 条）：
            [{"indicator": "增益", "input_port": 1, "output_port": 2, "band_name": "",
              "min_freq": 8.0, "max_freq": 10.0, "phase_step": 5.625, "atten_step": 0.5,
              "phase_devices_json": "[]", "atten_devices_json": "[]", "freq_rx": -1.0,
              "pwr_rx": -20.0, "freq_pout": -1.0, "pwr_pout": -20.0}]
    """
    return call_tool("tr_execute_simulation_plan", {
        "epp_path": epp_path, "source_netlist_path": source_netlist_path, "plan": plan,
    })


@mcp.tool()
def tr_run_simulation(netlist_path: str) -> dict[str, Any]:
    """执行一个网表修订版，通过 EDI/ADS 获取 result.raw。

    成功时返回 success=true、raw_path 和 controller_result；失败时返回 success=false、
    problem 和 controller_result，raw_path 为空。

    Args:
        netlist_path: 网表修订版路径。
    """
    return call_tool("tr_run_simulation", {"netlist_path": netlist_path})


@mcp.tool()
def tr_parse_raw(raw_path: str, indicator: str, input_port: int = 1, output_port: int = 2,
                 freq_rx: float = 0.0, freq_pout: float = 0.0, ac: str = "") -> dict[str, Any]:
    """解析 ADS result.raw，生成 CSV、曲线图片和标准化指标结果。

    参数必须与生成该 RAW 的网表修订版一致。返回 result 中的 indicator/minimum/typical/
    maximum/unit，以及 image_path、csv_path、curve_name、simulation_type。

    Args:
        raw_path: ADS result.raw 路径（必须属于当前会话目录，否则会被拒绝）。
        indicator: 指标名。
        input_port: 输入端口号（默认 1）。
        output_port: 输出端口号（默认 2）。
        freq_rx: 接收频率（默认 0.0）。
        freq_pout: 输出频率（默认 0.0）。
        ac: 精度配置（默认空；移相/衰减 RMS 时原样传 tr_modify_netlist 返回的 ac）。
    """
    return call_tool("tr_parse_raw", {
        "raw_path": raw_path, "indicator": indicator, "input_port": input_port,
        "output_port": output_port, "freq_rx": freq_rx, "freq_pout": freq_pout, "ac": ac,
    })


@mcp.tool()
def tr_read_guide(guide_name: str, offset: int = 0, limit: int = 12000) -> dict[str, Any]:
    """读取 guides 目录中的可编辑 Word 指南。

    guide_name 可选值：\"网表同步\"（第一次网表同步前必须完整阅读）。

    Args:
        guide_name: 指南名称。
        offset: 分页起始位置（默认 0）。
        limit: 每次读取的最大字符数（默认 12000，范围 100~30000）。
    """
    return call_tool("tr_read_guide", {"guide_name": guide_name, "offset": offset, "limit": limit})


@mcp.tool()
def tr_get_project_netlist(epp_path: str) -> dict[str, Any]:
    """获取工程当前真实网表并保存到会话目录。

    每次网表同步前必须调用，获取工程原理图当前真实状态。返回的 netlist_path 指向
    current_netlist.log，可用 tr_read_netlist 分页读取对比。

    Args:
        epp_path: EPP 工程绝对路径。
    """
    return call_tool("tr_get_project_netlist", {"epp_path": epp_path})


@mcp.tool()
def tr_query_schematic_components(epp_path: str, instance_name: str = "") -> dict[str, Any]:
    """查询工程原理图中的器件信息。

    不传 instance_name（空串）时列出全部器件；传 instance_name 时精确查询单个器件完整
    参数。主要用于获取网表中无法直接看到的器件实例名（如 Var 器件）及当前参数值。

    定位：TR 集成，查器件实例名及当前参数值。需要 gRPC 实时查全部器件用
    list_schematic_components；需要本地分页/过滤用 list_simulation_components。

    Args:
        epp_path: EPP 工程绝对路径。
        instance_name: 器件实例名（默认空，列出全部器件）。
    """
    return call_tool("tr_query_schematic_components", {
        "epp_path": epp_path, "instance_name": instance_name,
    })


@mcp.tool()
def tr_sync_project_components(epp_path: str, operations: list) -> dict[str, Any]:
    """将网表变更同步到工程原理图（尽力而为，不阻断仿真）。

    在报告输出后、用户确认需要同步并选定目标网表修订版时调用。operations 每项的 action
    可选 create/update/delete/set_state/replace_port/attach_out。

    Args:
        epp_path: EPP 工程绝对路径。
        operations: 同步操作数组（按顺序执行）。
    """
    return call_tool("tr_sync_project_components", {
        "epp_path": epp_path, "operations": operations,
    }, requires_confirmation=True)


@mcp.tool()
def tr_prepare_report(project_id: str = "", model_name: str = "") -> dict[str, Any]:
    """从当前工作流生成完整报告草稿和主 Agent 所需的判定证据。

    所有指标处理结束后、生成文档前必须调用。返回 report 草稿及 netlist_excerpt。
    主 Agent 需填写 report.description/report.conclusion 及有 requirement 的 result，
    完成后把整个 report 序列化传给 tr_generate_document。

    Args:
        project_id: 工程 ID（默认空）。
        model_name: 产品型号名称（默认空）。
    """
    if not project_id.strip():
        return error_response("MISSING_REQUIRED_ARGUMENT", "project_id 必填")
    return call_tool("tr_prepare_report", {"project_id": project_id, "model_name": model_name})


@mcp.tool()
def tr_generate_document(report_json: str) -> dict[str, Any]:
    """校验主 Agent 完成的报告草稿并生成 PDF、DOCX 和输入快照。

    report_json 必须来自本轮 tr_prepare_report 返回的 report。返回 report_dir、pdf_path、
    docx_path、report_data_path、schematic_path 和 chart_count。

    Args:
        report_json: 完整 report 对象的 JSON 字符串。
    """
    return call_tool("tr_generate_document", {"report_json": report_json})


@mcp.tool()
def tr_query_components(originalid_list: list, epp_path: str = "") -> dict[str, Any]:
    """取得工程器件的类别、厂家和关键规格。

    originalid_list 可传器件 UUID 或网表 model_name。返回匹配器件的中文类别、标准厂家
    名称和关键规格；未返回的型号保持未知，不得补造。

    返回的 data 按 model_name 对齐到输入顺序：未命中的型号补 null 占位，并在 missing
    中列出未命中的型号。

    Args:
        originalid_list: 器件 UUID 或 model_name 数组。
        epp_path: EPP 工程绝对路径（默认空，回退本地 component.json）。
    """
    r = call_tool("tr_query_components", {
        "originalid_list": originalid_list, "epp_path": epp_path,
    })
    # 兜底：data 不保序且未命中被静默丢弃，按 model 字段对齐输入顺序、补 null、加 missing。
    # 仅当输入能命中 model 字段（model_name 场景）时对齐；UUID 场景 data 项无 model 匹配，保持原样。
    if r.get("success") and isinstance(r.get("result"), dict):
        res = r["result"]
        data = res.get("data")
        if isinstance(data, list):
            by_model = {item.get("model"): item for item in data if isinstance(item, dict)}
            if not data or any(name in by_model for name in originalid_list):
                res["data"] = [by_model.get(name) for name in originalid_list]
                res["missing"] = [name for name in originalid_list if name not in by_model]
    return r
