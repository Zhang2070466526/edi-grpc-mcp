# EDI MCP 服务评审报告（待办问题清单）

- **评审日期**：2026-09-08（持续更新至 09-09）
- **评审对象**：EDI MCP 服务（`http://127.0.0.1:50026/mcp`，`D:\GitLabCode\edi-grpc-mcp\start_servers.py`），87 个业务工具
- **评审方法**：① 运行形态侦察（netstat/进程命令行/`/ready`）；② 全量 `tools/list` 定义审查（87 个工具 name/description/inputSchema 静态分析）；③ 行为抽样探针（直接调工具 + 白名单探针裸调 `/mcp`）
- **说明**：本清单只保留**未解决**项（已修复项已移除）。

---

## 一、运行形态快照（评审时点）

| 项 | 值 |
|---|---|
| 50026 进程 | uv python 3.14 直跑 `start_servers.py`（开发源码形态，进程白名单鉴权） |
| `/ready` | `tool_count=87`、`grpc=online`、`tools_hash=70ffb0bd`、`version=0.1.8`、`stateless=true` |
| 真实 `tools/list` | 87 条；前缀分布：`tr_`×17、`get_`×13、`list_`×6、`cst_`×5、`simulate_`×4、`search_`×4 … |
| 协议能力 | `prompts/list` 9 个、`resources/list` 7 个（实测可用，见 P2-7） |
| EDI.exe (50055) | 运行中，LISTENING |

---

## 二、P0（高优先级）

### P0-1 🔐 SimulationAgent 密钥裸奔在进程命令行
- **现象**：`SimulationAgent.exe` 以 `--model-api-key sk-...` 与 `--edi-mms-api-token <JWT>` 作为 argv 明文启动。
- **影响**：本机任何进程/用户可 `ps`/WMI 读到明文密钥。
- **建议**：改从环境变量/配置文件读取；启动后清零 argv。

---

## 三、P1（契约 / 命名不一致）

| # | 问题 | 证据 / 说明 |
|---|---|---|
| P1-1 | 「型号/ID 列表」参数名不统一 | `batch_query_component.model_name_list`（仅型号串，已改名✅）／`tr_query_components.originalid_list`（UUID 或 model_name，未改）／`get_components_static_params.original_uuids`（真 UUID）——三处仍不统一 |
| P1-2 | `originalid` 命名残留 | `tr_query_components` 仍用 `originalid_list`（无下划线），建议一并改名对齐 batch_query |
| P1-3 | 工程路径多名字 | 内部 `project_path` vs `create_project` 的 `path` vs `list_epp_projects` 的 `folder_path`；`epp_path`(tr_*, 镜像 SimulationAgent 契约) 保留 |
| P1-4 | `get_components_static_params` 双参数 | `original_uuid`(标量) + `original_uuids`(anyOf[array,null]) 并存，`type=None` 属实 |

---

## 四、P2（文档 / 质量）

- **P2-1　docstring 格式不统一**：多数「用法:」中文体，少数「Args:」英文体（`close_edi_project`、`capture_schematic`、`simulate_netlist_with_ads` 等）。
- **P2-2　部分工具 docstring 过简**：`get_hfss_project_info` / `launch_aedt` / `start_hfss_analysis_async` 仅一句话，无用法/Args。
- **P2-3　`message` 语言混杂**：`get_model_category_params` 返回英文 `"model category params retrieved"`；`list_schematic_components` → `"schematic components listed"`、`list_ideal_components` → `"ideal components listed"`、`export_project_netlist` → `"netlist generated"`（均英文）；而 `batch_query_component`/`get_components_static_params` 用中文（`"器件批量查询成功"`/`"器件固有参数获取成功"`）。同工具族内部中英混杂。
- **P2-4　`get_service_status` 的 `channel_state="unknown"`**：`get_cached_channel` 返回 None 时直接报 unknown、不探测。建议首次调用 `_get_channel()` 探测一次，报 READY/IDLE/CONNECTING 而非恒 unknown。
- **P2-5　返回字段键语言不一致**：`get_components_static_params` 中文键（封装/尺寸/成本/所属厂商/模型id/重量），`batch_query_component` 英文键（model/manufacturer/type）+ 中文 `specs` 串。
- **P2-6　功能冗余 + 返回信封不一致**：`list_schematic_components` / `list_simulation_components` / `tr_query_schematic_components` 高度重叠，且前两者**返回信封不同**——`list_schematic_components` 是 gRPC 任务信封（task_id/status/details.component_count）、**无分页**；`list_simulation_components` 是扁平结构（total/count/offset/limit/component_type_counts）、**带 offset/limit 分页**。同一批器件两种信封、一个能翻页一个不能，调用方二选一很纠结。docstring 应点明各自定位与取舍。
- **P2-7　协议方法暴露为「伪工具」（备忘，非 bug）**：`get_prompt`/`list_prompts`/`list_resources`/`read_resource` 是 MCP 协议方法（非工具），服务端实际可用（实测 `prompts/list` 9 个、`resources/list` 7 个、`prompts/get` 正常）。Hermes 会话快照把它们暴露成 4 个「伪工具」，经 `tool_call` 调会失败——纯客户端展示问题，记录备忘。
- **P2-8　CSV 导出 EXCLUDED_TYPES 漏了 `Out`**：工程 23 共 9 器件，`export_schematic_components_to_csv` 正确排除了 7 个仿真控制器/端口（TermG / P_nToneG / SParameter / Sweep / Var / HarmonicBalance / XDB），但**输出端口标记 `Out` 漏进了导出**（CSV 出了 Out1 一行）。按 docstring「端口不导出」口径，`Out` 也应入 EXCLUDED_TYPES。

---

## 五、优化建议（服务端视角，按落地优先级）

1. **统一 `originalid_list` 语义**（P1-1）：要么都认 UUID，要么把「只认型号串」写进 docstring 并加校验拦截 UUID 静默丢弃。
2. **`get_service_status` 首探**（P2-4）：报 READY/IDLE/CONNECTING 而非恒 unknown。
3. **密钥治理**（P0-1）：密钥走环境变量/配置，不落 argv，启动后清零。
4. **命名收敛**（P1-1~P1-3）：`tr_query_components.originalid_list` 一并改名、内部路径名统一；`epp_path`（tr_*）保留并 docstring 说明镜像关系。
5. **稳定长连接**：服务端减少会话中途重启（客户端侧断线不自动重连、不自动重拉 tools/list）。

---

## 六、全局运行时冒烟（2026-09-08 二检）

对 87 个工具做全量运行时冒烟（空参 `{}` + 按必需参数类型注入 dummy 实参；写类工具仅空参、不注入实参以避免副作用），另对 12 个只读工具用工程 23 真实参数跑通：

- **结论：0 崩溃**——无 NameError/TypeError/AttributeError/KeyError/traceback/5xx，参数校验层干净。
- **真实参数跑通 12 工具**：get_project_summary / list_schematic_components / list_simulation_components / get_signal_chain / analyze_variables / export_project_netlist / tr_find_paths / tr_query_schematic_components / tr_get_project_netlist / get_simulation_component_schema / list_ideal_components / get_schematic_component_info，均无逻辑崩溃。
- **更正**：工程 23 实际 **9 个器件**（TermG / SParameter / Sweep / Var / HarmonicBalance / Out / NC10355C_2931 / XDB / P_nToneG），早前只看 CSV 误判为 2 个（CSV 排除了仿真控制器）。

---

## 七、冗余与精简建议（工具合并 / 优化路线）

### 明显冗余（建议合并）

| 现有 | 合并后 | 权衡 |
|---|---|---|
| `search_public_models` + `search_personal_models` | `search_models(sub_type, library=public/personal)` | 后端不同（公共模型服务 vs 个人库），可保留分开但 docstring 互指 |
| `search_schematic_from_public_library` + `search_schematic_from_personal_library` | `search_schematic_from_library(search_name, library=...)` | 同上 |
| `use_schematic_from_library_create_project` + `use_schematic_from_library_import` | `use_schematic_from_library(file_uuid, mode=create/import, project_path=?)` | 参数高度对称，合并价值最高 |

### 可精简（合并 / 三选一）

- **列器件四工具**（=P2-6）：`list_schematic_components`（gRPC 信封、无分页）/ `list_simulation_components`（扁平、offset/limit 分页）/ `tr_query_schematic_components`（TR 信封）/ `tr_query_components`（按型号查类别规格）。建议基础层保留一个统一返回 + 分页；TR 版镜像 SimulationAgent 契约可保留，但 docstring 点明「非 TR 流程用 list_simulation_components」。
- **`simulate_netlist` + `simulate_netlist_with_ads`**：一个走 EDI（默认 ADS 路径）、一个直连 ADS（显式 ads_path）。可合并为 `simulate_netlist(netlist_path, ads_path=None)`。

### 需优化（保留但改进）

- **异步查询命名三套不一致**：CST `*_query` / HFSS `get_*_status` / EDA `get_*_async_status`；且 `cst_export_snp` 无 query、`cst_export_farfield` 有 query（不对称）。建议统一命名约定 + 补齐 snP 查询。
- **搜索/模型库无分页**：`search_public_models`/`search_personal_models` 固定每页 10 条、无翻页参数，全量 283KB 撑爆上下文。应加 `offset`/`limit`（参照 `list_simulation_components`）。
- **`get_service_status` channel_state 恒 unknown**（=P2-4）。

### TR 与基础工具重叠（不合并，需澄清定位）

`tr_query_schematic_components` / `tr_get_project_netlist` / `tr_run_simulation` 与 `list_schematic_components` / `export_project_netlist` / `simulate_netlist` 语义重叠，但 TR 是版本化可追溯工作流（网表修订不覆盖原网表），重叠是故意的。建议两组 docstring 各加一句「TR 版用于可追溯仿真流程，普通场景用基础版」。

### 其他小观察

- `get_model_category_params` 全量 158KB，`categories_only` 默认值建议反过来（默认精简、显式要全量）。

### 落地优先级

1. `use_schematic_from_library_create/import` 合并（收益最高、成本最低）
2. `simulate_netlist` / `simulate_netlist_with_ads` 合并
3. 搜索工具统一 `offset/limit` 分页
4. 异步查询命名三套归一
5. 公共/个人库成对工具（权衡后端差异再决定）
6. 列器件四工具（配合 P2-6，文档层先点明定位）

---

## 附：实测原始返回片段（证据留档）

`batch_query_component`（未命中补 null + missing，2026-09-09 修复后实测）：

```json
{"model_name_list": ["NC10355C_2931", "NO_SUCH_MODEL_XYZ"],
 "data": [{...命中项...}, null],
 "missing": ["NO_SUCH_MODEL_XYZ"]}
```

`get_service_status`（channel_state 恒 unknown）：

```json
{"grpc_target":"127.0.0.1:50055","channel_state":"unknown","channel_cached":false,"queue_locked":false,"max_receive_mb":256}
```

协议方法实测（P2-7 备忘）：

```text
prompts/list   OK  9 个: run_tr_simulation / inspect_edi_project / analyze_signal_chain /
                        run_and_review_simulation / assess_anti_burnout / configure_simulation_component /
                        select_component / create_simulation_report / troubleshoot_edi_error
resources/list OK  7 个: edi://integration/workflow / edi://service/overview / edi://service/status /
                        edi://projects / edi://reference/simulation-components /
                        edi://reference/operation-guide / edi://reference/error-codes
prompts/get run_tr_simulation  OK  (description + messages)
```
