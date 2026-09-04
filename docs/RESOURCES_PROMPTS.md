# MCP Resources & Prompts 说明

本文档详细说明 EDI gRPC MCP 服务的 **6 个 Resource** 和 **8 个 Prompt**：每个都有什么用、干了什么、怎么实现的。

## 一、概述

### Resource 与 Prompt 的区别

| | Resource | Prompt |
|---|---|---|
| **定位** | 只读上下文/参考资料 | 可复用工作流模板 |
| **访问方式** | `resources/list` → `resources/read` | `prompts/list` → `prompts/get` |
| **内容** | 服务状态、参数目录、错误码词典、工程清单 | 多步骤操作指令（引导 LLM 调用工具） |
| **带参数？** | 无（固定 URI） | 有（如 `project_path`、`sub_type_id`） |
| **输出** | JSON 或 Markdown 数据 | 一组 `{role, content}` 消息 |

### 注册机制

两者都在 `servers/resources_prompts/` 目录，按语义拆为 6 个文件：

- `resources_service.py` / `resources_reference.py` — Resource，用 `@mcp.resource(uri, name, title, description, mime_type)` 装饰器定义
- `prompts_project.py` / `prompts_simulation.py` / `prompts_component.py` / `prompts_report.py` — Prompt，用 `@mcp.prompt(name, title, description)` 装饰器定义
- `__init__.py` — import 这 6 个模块，触发装饰器注册（与工具的 `@mcp.tool()` 同理）

`servers/registry_server.py` 里的 `import servers.resources_prompts` 是最终触发点。装饰器在 import 时执行，把 Resource/Prompt 注册到 FastMCP 实例，无需手动维护清单。

---

## 二、Resource 详解（6 个）

### 1. `edi://service/overview` — 服务概览

- **用途**：让 LLM 一次性了解服务能力、版本、安全规则，作为会话开场的上下文。
- **干什么**：返回服务的元信息（版本号、协议版本、gRPC 目标、工作区状态、安全规则标志）。
- **怎么实现**：纯静态组装，读模块常量 `SERVER_VERSION`（`servers/__init__.py`）、`EDA_GRPC_SERVER`（`config.py`）、`OPENCLAW_WORKSPACE_PATH`（`multimodal_vision`），拼成 dict。**不含任何密钥/路径敏感信息**（有测试断言 `sk-`、`API_KEY` 不出现）。

### 2. `edi://service/status` — 实时运行时状态

- **用途**：诊断 gRPC 通道健康度、队列占用，以及工具集指纹。
- **干什么**：返回 `grpc_target` / `channel_state`（ready/unhealthy）/ `channel_cached` / `queue_locked` / `tool_count` / `tools_hash`。
- **怎么实现**：与 `get_service_status` 工具共享数据源——调 `grpc_client.get_cached_channel()` 读缓存 channel 并 `channel_ready_future` 探测状态，`is_queue_busy()` 读执行槽占用；`tool_count`/`tools_hash` 由 `_current_tools_names()`（读 `mcp._tool_manager` 排序工具名）和 `_current_tools_hash()`（`md5(sorted 名)[:8]`）计算。`tools_hash` 是**版本指纹**（工具启动时静态注册、运行期不变，重启后变化时 hash 随之变化），算法与 `/ready` 端点保持一致。

### 3. `edi://projects` — 工作区工程目录

- **用途**：让 LLM 无需翻文件系统就能知道「工作区有哪些工程」。
- **干什么**：返回 `workspace` 目录 + `count` + 精简的 `projects` 列表（每个工程只有 name/path/size，不读原理图内容）。
- **怎么实现**：`_projects_dir()` 确定目录——优先读 settings 的 `projects_dir`（env `PROJECTS_DIR`），留空自动检测 `%USERPROFILE%/EDI-Workspace/projects`；然后复用 `list_epp_projects(folder_path)` 扫描 `.epp` 文件，裁剪成精简清单返回。

### 4. `edi://reference/simulation-components` — 仿真器件参数参考

- **用途**：SP/HB/XDB 器件的参数 Schema 参考（公开参数名、gRPC 名、值类型、单位、创建/更新权限）。
- **干什么**：返回参数目录 JSON（`schema_version`、`protocol_version`、`components`、`parameter_patterns` 等）。
- **怎么实现**：直接 `return _load_catalog()`，复用 `simulation_components.py` 的同一份参数目录（`simulation_component_catalog.json`），**不维护两套定义**。与 `get_simulation_component_schema` 工具同源。

### 5. `edi://reference/operation-guide` — 操作安全约束

- **用途**：给 LLM 的操作「红线」，防止破坏性/危险操作。
- **干什么**：返回 Markdown 格式的安全规则清单（如「TIMEOUT 后禁止自动重试创建/导入」「clear_before_import 需双重确认」「不猜测工程路径」「产生输出文件先告知用户」等）。
- **怎么实现**：硬编码的 Markdown 字符串返回（`mime_type="text/markdown"`），内容与工具 docstring、错误码词典的口径一致。

### 6. `edi://reference/error-codes` — 错误码词典

- **用途**：让 LLM 根据 gRPC 返回的 status 选择正确的重试/排查策略。
- **干什么**：返回状态码 → 含义 → 建议动作的对照表（SUCCEEDED/FAILED/REJECTED/QUEUE_TIMEOUT/TIMEOUT/STREAM_DISCONNECTED/GRPC_UNAVAILABLE/PAYLOAD_TOO_LARGE/PROTOCOL_MISMATCH/TASK_NOT_FOUND），以及重试原则。
- **怎么实现**：硬编码的 Markdown 表格返回。核心原则：TIMEOUT/STREAM_DISCONNECTED 时 `outcome_known=false` 不要假设失败；非幂等操作禁止自动重试；查询类可安全重试一次。

---

## 三、Prompt 详解（8 个）

所有 Prompt 都返回 `[{"role": "user", "content": "..."}]`——一段给 LLM 的步骤指令。参数（如 `project_path`）在 `prompts/get` 时传入，由函数插值进模板。

### 1. `inspect_edi_project` — 工程只读检查

- **用途**：只读查看工程全貌，不改工程、不启动仿真。
- **干什么**：按 `detail_level`（summary/standard/full）分档，引导 LLM 依次调 `get_project_summary` → `analyze_variables` → `list_simulation_components`，汇总输出原理图数量、器件统计、变量/Sweep、仿真配置。
- **怎么实现**：`detail_level` 参数归一化后，拼出带深度的步骤模板。

### 2. `run_and_review_simulation` — 仿真执行 + 日志分析

- **用途**：执行仿真并分析结果。
- **干什么**：默认异步流程（`start_simulation_async` → 最多查一次状态 → 完成后 `get_simulation_async_result`），含「不要紧密轮询、单次对话最多查 3 次」的限流约束；可选同步流程。`analyze_log=True` 时引导分析 `ads_output`。
- **怎么实现**：`execution_mode`（async/sync）+ `analyze_log` 参数决定步骤分支，末尾拼接「重要约束」（TIMEOUT 不自动重试等）。

### 3. `configure_simulation_component` — 配置仿真器件

- **用途**：按需求创建/更新 SP/HB/XDB 器件。
- **干什么**：create/update 两条分支——先查 Schema → 把需求映射为合法参数 → 展示给用户确认 → 再调 `create_simulation_component` / `update_simulation_component`。
- **怎么实现**：`action`/`component_type`/`instance_name`/`requirements` 参数校验后拼步骤，强调「参数名必须与 Schema 一致、不编造」「无单位不传 unit」。

### 4. `create_simulation_report` — 生成仿真报告

- **用途**：协调多工具生成 PDF/DOCX 仿真报告。
- **干什么**：确认输出路径 → 查工程信息 → 查已有结果（不自动重跑）→ 列曲线 → 转图 → 截原理图 → 整理 spec_table/components → 调 `generate_simulation_report`。
- **怎么实现**：固定 10 步模板，重点约束「不自动启动新仿真」「不编造厂家/规格」「log_complete=false 不断言日志完整」。

### 5. `troubleshoot_edi_error` — 诊断调用错误

- **用途**：根据 status/error_code 给出排查建议。
- **干什么**：读 `edi://reference/error-codes` → 查 `get_service_status` → 按 status 分支给建议（TIMEOUT/STREAM_DISCONNECTED 不重试、GRPC_UNAVAILABLE 启动 EDI、QUEUE_TIMEOUT 查长任务、REJECTED 修参数等）。
- **怎么实现**：`status` 参数驱动分支逻辑，`error_code` 作为补充信息。

### 6. `assess_anti_burnout` — 抗烧毁评估

- **用途**：对工程执行抗烧毁仿真并按功率裕量排序。
- **干什么**：调 `simulate_anti_burnout` → 处理「抗烧毁仿真网表处理失败」的已知原因（PORT1 功率 >40dBm、多通道合路）→ 解析 results 按裕量升序 → 汇总表格。
- **怎么实现**：单参数 `project_path`，重点约束「max_input_power 单位可能 dBm/W，比较前统一换算 dBm」。

### 7. `select_component` — 器件选型（含替换闭环）

- **用途**：从公共/个人模型库选型，选完可生成替换 CSV 落到工程。
- **干什么**：8 步闭环——`get_model_category_params(categories_only=true)` 确认子类 → **依次**（gRPC 串行，不并行）搜公共/个人库 → 提取过滤条件 → `get_components_static_params` 查厂商/尺寸 → 对比表 → 推荐 → 用户确认后 `list_simulation_components(summary_only=true)` 取 original 三列 → 生成 CSV 调 `replace_models_from_csv`。
- **怎么实现**：`sub_type_id` + `requirement` 参数；重点标注了字段映射（搜索结果的 `model_uuid` → `get_components_static_params` 的 `original_uuid`；`component_type/instance_name/model_id` → CSV 的 `original_model_type/name/id`），防止 LLM 编造字段。

### 8. `analyze_signal_chain` — 信号链分析

- **用途**：追踪工程信号链路并解释各级器件作用。
- **干什么**：步骤 0 若跑过抗烧毁先 `export_project_netlist` 刷新网表（防 PowerPin 污染）→ `get_signal_chain` 追踪 → 逐级说明器件作用 → 处理 warning → 结合 result.raw 说明功率。
- **怎么实现**：`project_path` + `start`（默认 PORT1）参数；标注了 `_acquire_netlist` 现状（本地优先、失败才 gRPC，PowerPin 只 warning 不自动降级）。

---

## 四、使用方式

### 客户端访问

MCP 客户端通过协议原语访问：

```
resources/list                    → 列出 6 个 Resource
resources/read {uri}              → 读取指定 Resource 内容
prompts/list                      → 列出 8 个 Prompt
prompts/get {name, arguments}     → 传入参数，得到工作流指令
```

### 验证

```bash
# 单元级（不起服务）
uv run python -c "
import servers, servers.registry_server
from servers import mcp
print('resources:', sorted(r.uri for r in mcp._resource_manager.list_resources()))
print('prompts:', sorted(p.name for p in mcp._prompt_manager.list_prompts()))
"

# 全量测试
uv run pytest -q
```

### 相关文档

- 工具 API：`docs/TOOLS_API.md`
- HTTP 路由：`docs/HTTP_API.md`