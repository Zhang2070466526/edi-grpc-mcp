# TR 仿真集成问题清单

> 记录 `servers/tr_simulation/`（对接 SimulationAgent 的 17 个 `tr_*` 工具 + 1 Resource + 1 Prompt）的已知问题、方案与待决策点。
>
> 状态：整个 `servers/tr_simulation/` 目录尚未提交（开发中）。

## 一、当前结构

```
servers/tr_simulation/
├── client.py         # HTTP 客户端：会话管理、稳定 request_id、调用与轮询
├── tools.py          # 17 个 tr_* 工具（@mcp.tool()）
├── resource.py       # TR 仿真工作流 Resource（edi://integration/workflow）
├── prompts.py        # TR 仿真工作流 Prompt（run_tr_simulation）
├── MCP_WORKFLOW.md   # 工作流规则（来自 SimulationAgent）
└── MCP_HTTP集成说明.md  # HTTP 契约说明
```

对接外部服务 `SimulationAgent.exe`（默认 `http://127.0.0.1:17866`），1:1 镜像其 `/api/v1/integration/tools` 的 17 个工具。

## 二、已修复（本次会话）

| # | 问题 | 修复 |
|---|---|---|
| 1 | docstring「18 工具」与实际 17 个不符 | 改为 17 |
| 2 | `_resolve_session` 在锁内执行阻塞式 HTTP 建会话 | 移到锁外，锁内只读写缓存 |
| 3 | `httpx.HTTPStatusError` 被兜底 `Exception` 误报为 `TR_SERVICE_UNAVAILABLE` | 精确分类 |
| 4 | 会话缓存无失效，SimulationAgent 重启后 404 无法自愈 | 加 `_invalidate_session` + 404 自愈重试一次 |
| 5 | docstring「6 个后台工具由 client 轮询」与实际不符 | 改为「所有 queued/running 统一轮询」 |

## 三、待解决问题（按严重度）

### A. 会话归属可能串台（正确性 🔴）

- **现象**：`client.py` 的会话归属是「有 `epp_path` 的工具按路径建 session；无 `epp_path` 的工具用『当前最近 session』」。
- **无 `epp_path` 的工具（8 个）**：`tr_get_workflow_state` / `tr_set_workflow_plan` / `tr_get_simulation_capabilities` / `tr_run_simulation` / `tr_parse_raw` / `tr_read_guide` / `tr_prepare_report` / `tr_generate_document`
- **`epp_path` 可选的工具（2 个）**：`tr_read_netlist` / `tr_query_components`
- **隐患场景**：工作流文档要求「恢复任务、纠错、重跑或生成报告前，先调 `tr_get_workflow_state`」（无 epp_path）。此时 MCP 层会新建一个**没关联工程的空 session**，和之前那个按 epp_path 建的 session 分裂，拿不到旧状态。多项目并发时「当前最近 session」也可能串台。
- **方案**：
  1. **（推荐）给 project_id 类工具补 `epp_path` 参数**：`tr_get_workflow_state` / `tr_set_workflow_plan` / `tr_prepare_report` / `tr_read_guide` 加 `epp_path: str = ""`（可选，默认空走现有「最近 session」）。资源类工具（`tr_run_simulation` / `tr_parse_raw` / `tr_generate_document`）的入参本身是 session 内资源，保持「最近 session」。
  2. 维护 `project_id → session` 映射（类似 `epp_path → session`）。但 project_id 是 SimulationAgent 返回的，首轮之前拿不到，映射不好初始化。
  3. session 完全显式：`tr_find_paths` 返回 session_id，所有工具都显式传。最干净但改动大（17 个签名 + Agent 要理解 session 概念）。

### B. 确认门空壳（安全性 🟠）

- **现象**：`tr_restore_schematic` / `tr_sync_project_components` 的 `requires_confirmation=True`，但 `client.py` 的 `_confirm_id()` 只返回 `mcp-confirm-{时间戳}`，且 `confirmed_by_user=True` 恒为真。
- **工作流文档要求**（`MCP_WORKFLOW.md:69-71`）：只有用户明确确认后才能调用，且 HTTP 请求必须携带非空 `confirmation_id`。
- **待决策**：SimulationAgent 是否校验 `confirmation` 字段？
  - 若**校验** → MCP 层等于替用户伪造确认，需把这两个工具接入 Chat 层的破坏性工具确认门（`_DESTRUCTIVE_CHAT_TOOLS`，与 `clear_schematic` 同款）。
  - 若**不校验**（只文档约定）→ 现状的「恒传 True」可接受，`_confirm_id` 用时间戳也无所谓。

### C. 17 个工具认知负担重（可用性 🟠）

- **现象**：17 个 `tr_*` 工具 + 89 行工作流规则（工程发现 → 计划 → 仿真 → 报告 → 同步五阶段），LLM 认知负担重。
- **不能靠 MCP 层精简**（17 个是 SimulationAgent 的 1:1 镜像，精简需 SimulationAgent 侧合并）。
- **可做**：给每个工具 docstring 首行写清「属于哪个阶段、何时用」（类似已给器件查询三胞胎做的定位标注）。

## 四、次要问题（🟡）

| # | 位置 | 问题 |
|---|---|---|
| 1 | `client.py` `fetch_workflow()` | `resp.text.replace("tr_open_document", "open_document")` 是脆弱字符串替换，否定语境（如「不要用 tr_open_document」）也会被误改 |
| 2 | `client.py` `_stable_request_id()` | `json.dumps(default=str)` 会把不可序列化参数静默 `str()` 化，极端情况可能哈希碰撞 |
| 3 | `client.py` `_confirm_id()` | 秒级时间戳，同秒两个确认工具会拿到相同 confirmation_id（因 B 是空壳，影响小） |

## 五、关键契约（供后续决策参考）

- **会话规则**：`MCP_WORKFLOW.md` 第 7-15 行（创建/复用 session、稳定 request_id、轮询、先查 `tr_get_workflow_state`）。
- **确认要求**：`MCP_WORKFLOW.md` 第 69-71 行（`tr_sync_project_components` / `tr_restore_schematic` 需用户确认 + 非空 confirmation_id）。
- **大信号默认值**：`MCP_WORKFLOW.md` 第 73-78 行（`freq_rx`/`freq_pout` 默认频段中点，`pwr_rx`/`pwr_pout` 默认 -20 dBm）。
- **结果处理**：`MCP_WORKFLOW.md` 第 80-89 行（HTTP operation 成功 ≠ 业务成功；chart_only 指标数值可空但必须有图片）。
