"""MCP Prompts（ANSYS ↔ LASPCEM 联动）— 同模型两软件求解 + 结果对比。"""

from __future__ import annotations

from typing import Any

from servers import mcp


@mcp.prompt(
    name="run_aedt_laspcem_compare",
    title="AEDT/LASPCEM 同模型求解对比",
    description="把同一个 .aedt 模型在 HFSS 与 LASPCEM 各跑一遍，导出 sNp 并出对比曲线。",
)
def prompt_run_aedt_laspcem_compare(
        aedt_path: str,
        design_name: str = "HFSSDesign1",
        setup_name: str = "Setup1",
        sweep_name: str = "Sweep",
        renormalize_to: float = 50.0,
) -> list[dict[str, Any]]:
    """AEDT ↔ LASPCEM 联动工作流模板。

    Args:
        aedt_path: 源 .aedt 绝对路径（LASPCEM 侧由它导入生成 .sem）。
        design_name: HFSS 设计名。
        setup_name: HFSS 的 Analysis Setup 名。
        sweep_name: HFSS 扫频名（必填，AEDT 2020 的 LastAdaptive 导出不稳）。
        renormalize_to: 对比前的参考阻抗（Ω），默认 50。
    """
    steps: list[str] = [
        "1. 调用 `import_aedt_as_sem(aedt_path=...)` 生成 .sem，记下返回的 `sem_path`、"
        "`setup_name`（形如 `Setup1(FEM)`）与 `setup_candidates`。`exit_code=1` 配 `success=true` 是常态，"
        "判成败看 `sem_path` 存在且 `sem_size>0`。",
        "2. 调用 `run_laspcem_solve_async(sem_path, then_export=true)` 拿 task_id —— "
        "**必须在打开 LASPCEM GUI 之前**（LASPCEM 单工程独占、无进程间投递通道，GUI 占着 .sem 时求解必 PROJECT_IN_USE）。",
        "3. 用 `laspcem_solve_query(task_id, wait_seconds=120)` 有界等待并轮询；"
        "判成败只看 `steps`（`laspcem_task_list` 顶层 status 会误报 SUCCEEDED）。导出产物路径取 `export_result`。",
        "4. 调用 `open_hfss_project(aedt_path)`；返回 `OPEN_TIMEOUT` 时先 `get_hfss_project_info` 核对，"
        "已打开就继续，真没开才重试（重试会多起实例，后续工具会被拦）。",
        "5. 调用 `start_hfss_analysis_async(project_path, design_name, setup_name)`，再用 "
        "`get_hfss_analysis_status` 轮询。状态报 UNKNOWN / `result_verified=false` 时**不要重跑**："
        "看 `<工程>.aedtresults/<设计>.results/*_SU.txt` 里的 `Success 1` 判真成功。",
        "6. 调用 `export_hfss_snp(project_path, design_name, setup_name, sweep_name)` 导出 sNp。",
        "7. 调用 `compare_snp(paths=[hfss_snp, laspcem_snp], renormalize_to=renormalize_to, "
        "z0_csv=<sem目录>/1_Result/Design_POST/S_result.csv)` 出对比图 + CSV —— z0_csv 让 LASPCEM 侧"
        "按端口阻抗正确重归一化（真实路径含 `1_Result` 这一级；且只有文件名含 laspcem 才会命中，"
        "否则会明确告警）。",
        "8. 汇报格式：先给曲线图，再给关键频点数值表，最后给差值统计；只讲能拿出证据的结论。",
        "",
        "重要约束：",
        "- LASPCEM 工程里 `PortRenormalization=DoNotRenormalize`，sNp 按端口自身阻抗归一（实测 52.66/45.05 Ω），"
        "与 HFSS 的 50 Ω 不可直接比 —— 必须重归一化后再比较，且同时保留原始值。",
        "- 请求 `.sNp` 会按端口数落地成 `.s2p`；两个名字不同属正常。",
        "- S11 的残余差异可能是端口/过渡段建模差异，不是参考口径问题；不要用「口径不同」掩盖模型差异。",
        "- 不要为了看结果提前打开 LASPCEM GUI；要看图形请等求解与导出都结束后再开。",
    ]

    return [
        {
            "role": "user",
            "content": (
                f"请把 {aedt_path} 分别在 HFSS 与 LASPCEM 里求解并导出 sNp，然后给出对比曲线。\n"
                f"HFSS：design={design_name}, setup={setup_name}, sweep={sweep_name}\n"
                f"对比参考阻抗：{renormalize_to} Ω\n\n"
                + "\n".join(steps)
            ),
        },
    ]
