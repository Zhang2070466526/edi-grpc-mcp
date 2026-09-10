# TR 仿真 HTTP 工具工作流

本说明供调用 SimulationAgent HTTP 工具的 MCP 服务和上层通用 Agent 使用。HTTP
服务只执行工具，不代替上层 Agent 作业务判断。不得编造 EPP 路径、端口、器件、频率、
仿真结果或工具返回值。

## 会话规则

1. 开始一个工程任务前调用 `POST /api/v1/integration/sessions` 创建 `session_id`。
2. 同一仿真任务的全部工具调用必须复用该 `session_id`。
3. 每次调用使用稳定且唯一的 `request_id`。HTTP 超时后使用相同 `request_id` 查询或重试，
   不得换一个 ID 重复执行仿真。
4. `status=queued/running` 表示后台执行中，应通过 operation 地址轮询；只有
   `status=completed` 才能读取最终 `result`。
5. 恢复任务、纠错、重跑或生成报告前，先调用 `tr_get_workflow_state`，以持久化状态为准。

## 标准流程

### 1. 工程发现与参数确认

1. 用户必须提供存在的 EPP 绝对路径，不得搜索或猜测路径。
2. 调用 `tr_get_simulation_capabilities` 获取可选指标、单位和所需参数。
3. 调用 `tr_find_paths` 获取端口、有向路径、第一条有效路径、器件和网表快照。
4. 链路选定后，提取第一条路径中的主要器件型号，调用 `tr_query_components`；同时分页调用
   `tr_read_netlist` 读取未修改快照，检查原有 S 参数和 HB 仿真器频率设置。
5. 分别向用户展示主要器件公共频段和原网表仿真器设置。两者冲突时由用户决定。
6. 向用户统一确认每条链路的端口、频段、指标、扫参器件、步进、大信号条件和目标要求。
   未取得明确确认前，不得调用修改网表或仿真工具。

### 2. 保存计划

1. 调用 `tr_get_workflow_state` 取得 `project_id`。
2. 调用 `tr_set_workflow_plan` 保存完整计划。
3. `indicators` 不得包含“工作频率”；工具会自动把它加入每条链路首位。
4. 同一端口对分频段仿真时，每条计划使用唯一且非空的 `band_name`。

### 3. 仿真

1. DIRECT 指标（工作频率、移相步进、衰减步进）由计划直接形成结果，不调用 ADS。
2. 多个 ADS 指标首轮优先调用一次 `tr_execute_simulation_plan`。每个指标都从共同的干净
   网表快照开始，不能把前一个指标的修订版作为下一个不同指标的基础。
3. 单指标，或失败后的单项纠错，依次调用：
   `tr_modify_netlist → tr_run_simulation → tr_parse_raw`。
4. `tr_parse_raw` 必须沿用生成该 RAW 时的指标、端口、`freq_rx`/`freq_pout` 和 `ac`。
5. 批量结果中的失败项按 `stage=modify/simulate/parse` 分析。不得为了一个失败项重新执行
   整批计划。
6. 同一链路同一指标最多自动尝试三次；参数和网表均无变化时不得重复运行。
7. 解析结果明显异常时，先调用 `tr_get_workflow_state(include_attempts=true)` 定位修订版，
   再调用 `tr_read_netlist` 检查端口、开关状态、控制器、变量和输出表达式。改变用户确认的
   业务参数前必须重新询问用户。

### 4. 报告

1. 所有指标完成或明确失败后，先调用 `tr_prepare_report`。
2. 保留其返回的数值、单位、要求、状态、图片路径、成功项和失败项，不得改写这些事实。
3. 上层 Agent 根据 `evidence` 填写：
   - `report.description`；必须以 `evidence.description_suffix` 结尾；
   - 有 requirement 的电参数 `result`，只能为“通过”“失败”或“未知”；
   - `report.conclusion`；
   - 有可靠来源时补充器件类型、厂家和规格。
4. 将完整 report 序列化后调用 `tr_generate_document`。
5. 把返回的 PDF、DOCX、报告数据和曲线路径提供给用户。

### 5. 原理图同步与回退

1. 报告输出后才能询问是否同步原理图。
2. 首次同步前完整调用 `tr_read_guide(guide_name="网表同步")`。
3. 同步前调用 `tr_get_project_netlist` 获取工程当前真实网表，并与用户选定修订版比较。
4. 只有用户明确确认后才能调用 `tr_sync_project_components`。HTTP 请求还必须携带：
   `confirmation.confirmed_by_user=true` 和非空 `confirmation.confirmation_id`。
5. `tr_restore_schematic` 同样必须先取得用户明确确认并携带 confirmation。

## 大信号默认值

- HB/XDB 的 `freq_rx` 默认取已确认频段中点，`pwr_rx` 默认 `-20 dBm`；不单独询问，
  只在统一确认中展示。
- 输出功率使用独立的 `freq_pout` 和 `pwr_pout`，默认同样为频段中点和 `-20 dBm`，
  需要在统一确认中展示。

## 结果处理

- 每次调用同时检查 HTTP operation 的 `status` 和工具结果中的 `success`。HTTP operation
  成功只表示工具正常返回；如果工具结果含 `success=false`，业务仍然失败。
- `chart_only` 指标数值为空是正常情况，但必须有图片。
- 非 chart_only 指标没有有效数值时不能作为成功结果写入报告。
- 失败指标不得伪造数值或图片。
- 用户明确要求打开报告时，可以调用 `open_document`；PDF 和 DOCX 同时存在且用户未
  指定格式时优先打开 PDF。路径应使用报告工具返回值，不要猜测。
