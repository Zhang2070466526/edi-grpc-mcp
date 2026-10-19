# Langfuse × edi-grpc-mcp 集成方案

> 定位：**开发期 LLM 可观测性与评测**方案（观测 / 评测 / 提示词版本化）。
> 状态：方案稿，未实施。文中标注「待实测」的条目在落地前必须用真实调用验证。

## 评审意见（2026-10-09）

> **结论**：方案骨架可作实施蓝本，但**先拍板「② 评测是不是近期目标」再定 M0**。核心问题不在「怎么接」，而在「要不要为方案①单独上 Langfuse」。

**做得对的（保持，勿改）**
- Win7 不接入、主依赖 / `requirements-win7.txt` / 打包流程零改动——判断正确。
- 可选依赖 + 运行时双开关 + import 失败 no-op 降级——隔离到位，§3.6-5 可回归。
- 方案③默认不做（权威源不外移）、方案④最低优先级（通道 A 覆盖 90%）——全文最清醒的两条判断。

**关键决策点（§9 M0 之前先定）**
- 方案①（trace）当前 `:661` 已有结构化日志（`request_id/tool/duration_ms/success/args`）+ `/metrics` 计数，单机排障已覆盖约 80%。
- Langfuse 相对普通 OTel/Jaeger 的**不可替代价值在 ② 评测 / ③ 提示词版本化**，而③已推迟、②又依赖①。
- → 拍板：**若近期目标是②，值得上 Langfuse，①是它的前置；若短期只追排障，先用结构化日志，Langfuse 缓上，M0（起 6 容器）暂缓。**

**落地前缺口（进入对应里程碑前补齐）**
1. **SDK `flush()` 遗漏**：Langfuse 异步批量上报，长驻 server 优雅退出前不 `flush()` 会丢最后一批 trace。§9 未列此步，M1 要补。
2. **`usage` 字段是最核心却最不确定**：token/成本是 LLM 观测最想要的，但当前 `:525` 只读 `choices` 不读 `usage`。M1 先实测上游网关是否回标准 `usage`，不回则 ① 的「LLM 维度」成色打折。
3. **span 跨线程**：工具执行走 `asyncio.to_thread`（`:638`），SDK 的 contextvars 传播要实测确认；`duration_ms` 复用 `act.duration_ms`（方案已对，别重复计时）。
4. **§4.2「tests/ 已有 6697 行可复用夹具」需核清**：具体哪些 fixture 可复用、与 `ChatService.chat()` 签名/依赖是否对得上，别到时对不上返工。

### 复核结论（助手实测，2026-10-09）

逐条复核上列 4 项缺口 + 决策点：

| 评审项 | 判定 | 依据（实测） |
|---|---|---|
| 1. `flush()` 遗漏 | **成立** | 落点比 M1 更准：`start_servers.py:146 _install_shutdown_handlers()`（`:272` 安装，`_handle_shutdown` 仅 `sys.exit(0)`）——在此加 `flush()` |
| 2. `usage` 最不确定 | **成立** | 已确认 `:525` 只取 `choices`；关键在**上游网关是否回标准 `usage`**，属必须实测项 |
| 3. span 跨线程 | **不构成阻碍**（部分修正） | 手工 span 在事件循环上下文 open/end（循环体 `:609-673` 在 async 函数内），仅工具函数体跑在 `:638 to_thread`；子节点是显式对象引用、不走 contextvars。**仅改用自动埋点/装饰器才需重测**。`duration_ms` 复用 `act.duration_ms`（与评审一致） |
| 4. tests/ 夹具可复用性 | **原稿口径过松，已改 §4.2** | `tests/` 共 6697 行/30 文件，但 `tests/test_chat_service.py` 仅 251 行、`await`/`asyncio` 出现 **0** 次——从不驱动 `chat()`；需自造 async 驱动器 + LLM stub |

**决策点复核**：「`:661` 结构化日志覆盖约 80%」——**工具层成立**（`:661-663` 已含 `request_id/tool/duration_ms/success/args`），但 **LLM 维度为 0%**（无轮次内容 / token / 成本 / trace 树 / 跨请求聚合）。故**同意评审结论**：近期目标是 ② 就上 Langfuse（① 作前置）；只追单机排障则先用结构化日志，M0 暂缓。

## 0. 范围与前提

| 项 | 结论 |
|---|---|
| **目标平台** | **仅 Win10 开发机**。 |
| **Win7** | **明确不接入**。原因：`requirements-win7.txt` 把 `mcp==1.12.4` / `pydantic==2.10.6` / `pydantic-core==2.27.2` 钉死（后者是 Win7 上 Rust 扩展的硬天花板），注入 Langfuse 有扰动该组合的风险；且现场机离线。 |
| **交付基线** | 不改。`pyproject.toml` 主依赖、`requirements-win7.txt`、冻结 exe 打包流程**均不动**。集成以**可选依赖 + 运行时开关**存在，默认关闭。 |
| **行为影响** | 不改任何现有工具行为与返回；只做旁路观测/评测。 |
| **数据合规** | trace 会携带工具入参、返回内容与工程内数据。开发机自用可接受；若上报 Langfuse Cloud 需评估外发。 |

## 1. 现状盘点（代码事实，已逐条核对）

| 位置 | 事实 |
|---|---|
| `servers/chat/service.py:500-514` | 裸 `httpx.AsyncClient` POST `{llm_base_url}/v1/chat/completions`，请求体含 `model` / `max_tokens` / `messages` / `tools=CHAT_TOOLS_SCHEMA` |
| `servers/chat/service.py:501` | LLM↔工具循环，上限 `_MAX_ROUNDS = 5` |
| `servers/chat/service.py:162-196` | `_auto_build_chat_tools()`：从 MCP 注册表自动生成 OpenAI function-calling schema；`CHAT_TOOL_MAP` = 工具名→可调用函数 |
| `servers/chat/service.py:609-682` | 单轮工具执行段：`_validate` → `await asyncio.to_thread(func, **args)` → 组装 `Activity` |
| `servers/chat/service.py:654-658` | `act.duration_ms` 已测得；随后 `record_tool_call(tool_name, success, duration_ms)` |
| `servers/chat/service.py:293` | `_system_with_context(session)`：动态 system prompt（当前工程 / task_id 等上下文注入点） |
| `servers/chat/service.py:202-265` | 数据类：`Activity{tool,label,status,duration_ms,summary,result,error}`、`ChatSession`、`ChatResponse{success,session_id,request_id,reply,activities,context,media}` |
| `servers/chat/service.py:516-523 / 695-709` | 三条失败路径：LLM 非 200、`httpx.RequestError`、通用异常 |
| `servers/metrics.py` | `record_tool_call()` 只累计 `{count, errors, total_ms}`（无内容、无 trace 树、无 LLM 维度） |
| `servers/registry_server.py:89-97` | 路由：`/`、`/ui`、`/health`、`/chat`(POST)、`/tools/list`、`/metrics` |
| `servers/settings.py:72-80` | 配置项：`llm_api_key` / `llm_base_url` / `llm_model`；`vision_api_key` / `vision_base_url` / `vision_model` / `vision_timeout` / `vision_max_mb` |
| `servers/multimodal_vision/vision_analyzer.py` | 第二处 LLM 调用（视觉，OpenAI 兼容 / DashScope 格式） |
| `pyproject.toml` | 依赖中**无** `openai` / `langfuse` / `opentelemetry`；均为待新增 |
| `servers/registry_server.py`、`start_servers.py` | MCP 传输：streamable-http（默认，本机）/ stdio；远程模式 `--host` |

**一句话结论**：仓库内置了一个完整 agent 循环（`ChatService`），目前只有「一行 `_logger.info`（`:538`）」+「Prometheus 计数（`/metrics`）」两级粗粒度观测，缺 trace 树、缺 LLM 维度（token/成本/延迟分位）、缺可回放的失败样本。Langfuse 正是补这一层。

## 2. 总体思路与数据流

三条能力线，两条接入通道：

```
能力线                        接入通道
──────────────────────────   ─────────────────────────────────────────
① 观测 Trace                 A. 进程内：ChatService（servers/chat/service.py）
② 评测 Regression            B. 跨进程：外部 agent 经 MCP 调本 server
③ 提示词版本化
```

```
                          ┌───────────────────────────┐
  浏览器/客户端  ──POST──▶ │ /chat  ChatService         │
                          │  └ trace(edi-chat, session)│──┐
                          │      ├ generation(LLM 第1轮)│  │
                          │      ├ tool(open_edi_project)│ │
                          │      ├ tool(validate_...)    │ │  Langfuse SDK
                          │      └ generation(LLM 第2轮) │  │  (HTTPS/OTLP)
                          └───────────────────────────┘  ├──▶ ┌───────────┐
                                                          │    │ Langfuse  │
  外部 agent(Hermes…) ──MCP──▶ MCP 工具 handler ──span───┘    │  (Cloud / │
   (自带 LLM trace)   traceparent 透传(待实测)                 │  自托管)   │
                                                              └───────────┘
```

- **通道 A**：在 `ChatService` 内埋点。收益最大、成本最低、当天可见效。
- **通道 B**：给 MCP 工具 handler 包 OTel span，上报 Langfuse 的 OTLP 端点；能否与外部 agent 的 trace 串成一棵树取决于 W3C `traceparent` 透传（**待实测**）。

## 3. 方案① 全链路 Trace（观测）

### 3.1 目标

把 `POST /chat` 的一次请求变成 Langfuse 里一条可点开、可回放的 trace。

### 3.2 trace 模型映射

| 业务对象 | Langfuse 实体 | 关键字段 |
|---|---|---|
| 一次 `POST /chat` | **trace** | `name="edi-chat"`、`session_id=session.session_id`、`metadata.request_id` |
| 一轮 LLM 调用（循环体） | **generation** | `model`、`input=messages`、`output=assistant_text/tool_calls`、`usage`（token）、`latency` |
| 一次工具执行 | **span**（type=tool） | `name=tool_name`、`input=validation_result`、`output=act.result`、`status`、`duration_ms` |
| 多轮会话 | **session** | 由 `session_id` 聚合（`ChatSession` TTL 2h） |

### 3.3 落点（文件:行）

| 落点 | 改动 |
|---|---|
| `servers/chat/service.py:499`（`try:` 之后） | 开 trace；失败时置 `error` level |
| `:502`（`client.post(...)` 前后） | 开 generation；**新增**从 `data["usage"]` 取 token（现在 `:525` 只读 `choices`，未取 usage） |
| `:609-673`（工具循环体） | 每个 tool_call 开一个 span；成功/失败按 `act.status` 置级别；复用 `:654` 的 `duration_ms` 与 `:641/643` 的 `act.result` |
| `:516-523` / `:695-709` | 三条失败路径显式标记 trace error（否则失败请求在 UI 里看不出红） |
| `:293 _system_with_context` | 见方案③（提示词版本化），与 trace 解耦 |

### 3.4 代码骨架（**示意，SDK API 待实测**）

> 我无法在当前环境访问 `langfuse.com`（站点被网络拦），Python SDK 的确切 API 形态（v3 的 `start_span/start_generation` 与旧版 `trace()/span()`）**接入时必须按已锁定的 SDK 版本实测确认**，下述仅为结构示意。

```python
# servers/chat/service.py 顶部（可选导入，缺失时降级为 no-op）
try:
    from servers.observability import get_langfuse   # 本地薄封装
except Exception:
    get_langfuse = lambda: None

# _chat_locked 内：
lf = get_langfuse()
trace = lf.trace(name="edi-chat", session_id=session.session_id,
                 metadata={"request_id": request_id}) if lf else None

# LLM 调用处（:502 附近）
gen = trace.generation(name=f"llm-round-{_round}", model=model,
                       input=session.messages) if trace else None
resp = await client.post(...)
data = resp.json()
if gen:
    gen.end(output=data.get("choices"), usage=data.get("usage"))   # 取 token

# 工具执行处（:609-673 内）
sp = trace.span(name=tool_name, input=validation_result) if trace else None
...  # 现有执行逻辑不动
if sp:
    sp.end(output=act.result, level="ERROR" if act.status == "error" else "DEFAULT",
           metadata={"duration_ms": act.duration_ms, "error": act.error})
```

要点：
- **全部包在 `if trace` 判断里**，Langfuse 不可用 = 零影响，绝不阻断 chat。
- 建议抽一层 `servers/observability.py` 薄封装（`get_langfuse()` / 开关读取 / no-op 降级），业务代码不直接依赖 SDK。
- 脱敏：现有日志已对路径做 basename 处理（`:660-663 _safe_args`），trace 的 `input` 建议复用同一脱敏函数（**待定：是否对工程路径全量上报，由你定**）。
- **关闭 flush（补评审①）**：长驻 server 退出前不 `flush()` 会丢最后一批 trace。落点 `start_servers.py:146 _install_shutdown_handlers()`（`:272` 安装；`_handle_shutdown` 当前只 `sys.exit(0)`）。
- **span 与 `asyncio.to_thread`（回应评审③）**：手工 span 的 open/end 都在事件循环上下文（循环体 `:609-673` 在 async 函数内），仅工具函数体跑在 worker 线程（`:638`）；子节点是显式对象引用、不走 contextvars → **不受跨线程影响**。仅当改用自动埋点/装饰器才需重测。

### 3.5 配置（`.env`）

```
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com      # 或自托管 http://127.0.0.1:3000
```

### 3.6 验收判据（可测量）

1. Langfuse UI 出现一条 trace，`name=edi-chat`，其 `session_id` 与 `/chat` 返回的 `session_id` 一致。
2. 该 trace 下 generation 数 = 实际 LLM 轮数；每个 generation 有非空 `usage`（token）。
3. tool span 数 = `/chat` 返回 `activities` 长度；每个 span 的 `duration_ms` 与 `activities[i].duration_ms` 一致。
4. 构造一次工具失败（如传非法工程路径），该 span 在 UI 显示为 error 级别，且 `error` 文案与 `activities[i].error` 一致。
5. `LANGFUSE_ENABLED=false` 时，`/chat` 行为、返回、`/metrics` 与改造前**逐字节一致**（可用现有 `tests/` 回归）。

## 4. 方案② 评测与回归（Datasets + Evaluations）

### 4.1 目标

把「改了工具描述 / 换了模型」从「凭感觉」变成「可度量」。对最近几笔「描述统一口径」的提交尤其有直接价值。

### 4.2 设计

- **数据集（Dataset）**：以典型 EDI 任务为 item，`input`=用户原话，`expected`=期望工具序列（含关键参数）/期望关键数值。
  样例：`打开工程 → validate → analyze → 导出 s2p`；`画 Wilkinson 功分器`；`读 HB RAW 画图`；`替换模型`；`读原理图器件清单`。
- **驱动**：写 SDK 脚本直接调 `ChatService.chat()`（**不改服务**）。⚠️ **无现成夹具可复用（实测 2026-10-09）**：`tests/` 共 6697 行/30 文件，但 `tests/test_chat_service.py` 仅 251 行、`await`/`asyncio` 出现 **0** 次——它从不驱动 `chat()`，只测内部方法（`_get_or_create`/`_update_context`/`_trim_messages`/`_is_duplicate_tool_call`/参数校验），仓库亦无 async 测试基建（`pyproject.toml` 无 `pytest-asyncio`/`anyio` 配置）。**方案② 需自造「async 驱动器 + LLM stub」**；仅工具层可复用 `conftest.py` 的 `fake_caller` 与 `real_project`（后者路径 `C:\Users\JGL\EDI-Workspace\EDI_TEST\EDI_TEST.epp` 已确认存在）。
- **评估器（Evaluator）**：
  - **code evaluator**：断言工具名集合/顺序是否符合 `expected`；关键数值（如 s2p 频点、功分器隔离度）在容差内。
  - **LLM-as-judge**：判自然语言回复质量、是否遗漏前置确认、破坏性操作是否要求确认。
- **运行时机**：手动触发 or 挂 CI；产出为 Langfuse 里的 dataset run 对比视图。

### 4.3 落点

新增 `scripts/` 下评测脚本 + `tests/` 夹具，不触碰 `servers/`。

### 4.4 验收判据

对一个 ≥5 条的数据集跑两次（改描述前/后），Langfuse 出现两次 run，可对比每条的通过率与工具序列差异。

## 5. 方案③ 提示词版本化（Prompt Management）

### 5.1 目标

把 `servers/chat/service.py:293 _system_with_context()` 的 system prompt 与 `:116 _CHAT_TOOL_DESCRIPTIONS` 的补丁描述搬到 Langfuse 版本化管理，运行时拉取 + 本地兜底 + 缓存，改 prompt 不发版。

### 5.2 ⚠️ 决策点（需你拍板）

按既定口径「**业务流权威源 = 服务端 prompts/文档**」，本方案会把部分权威源外移到 Langfuse。
**因此方案③默认不做**，仅在①/②跑顺、你确认「可接受权威源外移」后再开。

### 5.3 若做

- 拉取失败必须回退到本地常量（离线/断网不阻断）。
- trace 里记录本次使用的 prompt 版本号（便于「这次结果差 → 是不是 prompt 变了」的归因）。

## 6. 方案④ 跨进程 MCP trace（OTel）

### 6.1 目标

让「外部 agent（Hermes / Claude Code）的 LLM trace」与「MCP 内部工具 span」串成一棵树。

### 6.2 做法

- MCP server 侧初始化 OTLP exporter，指向 Langfuse OTLP 端点（自托管 `…/api/public/otel`，路径**待实测**）。
- 每个工具 handler 包一个 span（落点：`servers/registry_server.py` 注册层）。
- 若客户端在 MCP 请求 `_meta` 里透传 W3C `traceparent` / `tracestate` / `baggage`，则 server 内的 span 成为外部 trace 的子节点。

### 6.3 未验证项（落地前必须实测）

- [ ] MCP 客户端（Hermes/Claude Code）是否发送 `traceparent`？
- [ ] 本 server 的 MCP 层是否把 `_meta` 透传到工具 handler？
- [ ] Langfuse OTLP 端点的确切路径与鉴权头（`Authorization` 或 `x-api-key`）？

**优先级最低**——通道 A 已能覆盖 90% 调试需求。

## 7. 部署形态（Win10）

| 选项 | 说明 | 成本 |
|---|---|---|
| **A. Langfuse Cloud** | 官方托管，免运维，免信用卡免费额度 | 低（需外发数据） |
| **B. 本机自托管（docker compose）** | 6 容器：`langfuse-web` + `langfuse-worker` + ClickHouse + Postgres + Redis + MinIO | 中（内存/磁盘、运维） |

推荐：**开发机先 B（数据不出机）**，或 A（最省事）。两处都**只装在 Win10 开发机**，现场 Win7 不涉及。

## 8. 依赖隔离与开关（关键）

- 新依赖**不进主依赖**。落在 `pyproject.toml` 的可选组：

  ```toml
  [project.optional-dependencies]
  observability = ["langfuse>=<锁定版本>"]
  # 安装：uv sync --extra observability   （或 pip install '.[observability]'）
  ```

- `requirements-win7.txt` **零改动**（Win7 不做）。
- 运行时**双重开关**：`LANGFUSE_ENABLED=false` 默认关 + 导入失败自动降级为 no-op。保证「不装 Langfuse 也能正常跑」。

## 9. 实施顺序与里程碑

| 阶段 | 内容 | 依赖 | 产出 |
|---|---|---|---|
| M0 | 起一套 Langfuse（§7），确认可达 | — | 可访问实例 + 凭证 |
| M1 | 方案①（trace）+ `servers/observability.py` 封装 + **关闭钩子 flush**（§3.4） | M0 | trace 树（§3.6 全绿） |
| M2 | 方案②（dataset + evaluator） | M1 | 可回归的评测 run |
| M3 | 方案③（提示词版本化）—— **需你拍板权威源** | M2 | prompt 版本/AB |
| M4 | 方案④（跨进程 OTel）—— 先做 §6.3 三项实测 | M1 | 端到端 trace |

## 10. 风险与未验证项

| 项 | 级别 | 说明 |
|---|---|---|
| Langfuse Python SDK 确切 API | 高 | 站点不可达，未联网核；接入时按锁定版本实测 |
| `usage` 字段 | 中 | 需确认上游 LLM 网关是否返回 OpenAI 标准 `usage`；不返回则 token/成本统计缺失 |
| OTLP 端点路径与鉴权 | 中 | §6.3 待实测 |
| `traceparent` 透传 | 中 | §6.3 待实测 |
| 数据外发合规 | 中 | 走 Cloud 时评估；自托管可规避 |
| 埋点对 chat 延迟 | 低 | SDK 默认异步批量上报；仍建议实测 P95 变化 |

## 11. 环境变量清单

```
# 开关
LANGFUSE_ENABLED=false            # false=完全不启用（默认）

# 连接（LANGFUSE_ENABLED=true 时生效）
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

# 复用既有 LLM 配置（不改）
LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
```
