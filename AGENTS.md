# AGENTS.md — 给 AI 编码助手的硬规则

> 本文件优先级**高于**你的默认策略（探索代码 → 自己写脚本）。
> 在本仓库工作前，先读完这 60 行，能省掉你 90% 的弯路。

## 这个仓库是什么

EDI MCP 服务：把 EDI / ANSYS HFSS / CST 三个仿真引擎包装成 **84 个 MCP 工具**。
仓库里的 Python 代码是**服务实现**，不是给你用的库 —— **你的任务几乎从不发生在这一层**。

## 硬规则

1. **先查工具，再动手**：任何 EDA 任务（读/改工程、跑仿真、出图、算指标、查器件、算线宽/阶数）
   都先在这 84 个工具里找。能调用的**必须调用**，不要写脚本重实现算法。
2. **禁止读/反编译 `servers/` 源码来推断参数**。参数用 `describe_tool("<工具名>")`；
   不知道工具名就用**能力关键词**问它，例如 `describe_tool("微带线宽")`、`describe_tool("滤波器阶数")`、
   `describe_tool("噪声系数")` —— 它会返回候选工具 + 完整参数 + 可照抄的示例。
3. **只有两种情况允许写脚本**：
   - ① 用 `describe_tool("")` 列全 84 个工具后，确认**确实没有**该能力；
   - ② 需要批量编排多个工具（脚本内部**仍必须调工具**，不得重算算法）。
4. **不要凭经验套公式**。下面这些"看起来该手算"的事，工具里都已经做了（含修正项）：
   微带线宽/阻抗/损耗、滤波器阶数与零点、威尔金森节数与隔离电阻、S 参数解析与绘图。
5. **错误码不是让你去读实现的**。`INVALID_PARAMETERS` / `MISSING_REQUIRED_ARGUMENT` 的返回里
   已经给了 `missing` / `next_actions`，照着改参数重试即可。

## 任务 → 工具 对照表（不要自己写）

| 你要做的事 | 用这个（别重实现） |
|---|---|
| 算微带线 / 带状线 / 共面波导 / 同轴的线宽、阻抗、损耗 | `calculate_transline` |
| 低通 / 高通 / 带阻滤波器要几阶、零点放哪 | `filter_order_lowpass` / `filter_order_highpass` / `filter_order_bandstop` |
| 威尔金森功分器 / Marchand 巴伦的节数 | `wilkinson_marchand_order` |
| 威尔金森功分器的臂阻抗、线宽线长、隔离电阻 | `design_wilkinson` |
| 读 `.raw` 结果、查有哪些曲线、画图、导 CSV | `list_result_curves` → `turbocharts_convert` |
| 多个仿真结果叠加对比 | `compare_simulation_results` |
| 查器件型号 / 参数 / 封装 / 成本 | `search_public_models` / `search_personal_models` / `get_components_static_params` |
| 批量查多个型号 | `batch_query_component` |
| 看工程里有什么器件 | `list_schematic_components`（gRPC 实时）/ `list_simulation_components`（含参数映射） |
| 改器件参数 / 加器件 / 连线 | `update_simulation_component` / `add_ideal_component` / `add_wire` |
| 跑混频链路并出 PDF/DOCX 报告 | `run_mixer_simulation`（自带报告） |
| 跑 TR 纯链路 | `run_tr_simulation` → `export_simulation_report` |
| 跑一般仿真（异步 + 轮询） | `start_simulation_async` → `get_simulation_async_status` → `get_simulation_async_result` |
| 打开/关闭 EDI 工程 | `open_edi_project` / `close_edi_project` |
| 生成 PDF/DOCX 报告 | `export_simulation_report` |
| 扫工作区有哪些工程 | `list_epp_projects` |
| 不知道用哪个工具 | `describe_tool("<你的诉求>")` |

## 调用前必读（三个最容易踩的点）

1. **参数不确定就先 describe**：MCP 只把 docstring **首行**发给模型，`links`、`stopband_specs`
   这类数组参数的元素结构在 schema 里是空的 —— 只有 `describe_tool` 能给完整字段表和示例。
2. **异步工具的套路**：`*_async` 返回 `task_id`，用 `*_query` / `*_status` 轮询，别重复提交。
3. **破坏性工具**（首行带 ⚠️ 的：`clear_schematic`、`delete_simulation_component`、
   `replace_schematic_from_file`、`replace_models_from_csv`、`close_edi_project`…）
   调用前先 `get_project_summary` 核对，避免误删/误覆盖。

## 不要做的事（会被 review 打回）

- ❌ 自己用 `numpy`/`scipy` 实现滤波器阶数综合或微带线综合
- ❌ 自己解析 `.raw` / `.s2p` / Touchstone 文件
- ❌ 用 `grep` / 读 `.py` 源码来猜工具参数
- ❌ 绕过 MCP 直接调用 `*.exe` 或 raw gRPC
- ❌ 为了"简单"重新实现一个已有工具的子集

## 如果你在做"改这个服务本身"的活

那才需要读 `servers/` 源码。此时请先看：

- `docs/审查报告.md` —— 已知未修问题清单（别把已修的当新发现）
- `docs/TOOLS_API.md` —— 工具契约
- `docs/IMPLEMENTATION.md` —— 机制权威说明

改完代码必须跑：`uv run pytest -q`（当前基线 **562 passed / 2 skipped**）。
