# EDI gRPC MCP

> **让 AI 用自然语言驱动 EDA 设计与仿真** —— 一句话完成「打开工程 → 配置器件 → 跑仿真 → 出报告」，三大仿真引擎统一封装。

[![PyPI](https://img.shields.io/pypi/v/edi-grpc-mcp?label=PyPI)](https://pypi.org/project/edi-grpc-mcp/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## ✨ 亮点

- 🛠️ **64 个 MCP 工具** — 工程管理 / 仿真 / 器件配置 / 模型选型 / 信号链分析，全链路覆盖
- ⚡ **三大仿真引擎** — EDI gRPC · ANSYS HFSS · CST，一个服务统一封装
- 🧠 **自然语言驱动** — 接入 Claude Code / OpenClaw，告别鼠标点击
- 🔒 **本地安全** — 全本地运行，工程数据不出机器
- 📈 **异步仿真 + 实时日志** — task_id 追踪，支持长时间任务

### 一句话示例

> "帮我看看 C:/Projects 下有哪些 .epp 工程，打开第一个，查看 S 参数仿真器件的配置，设置频率 1-10GHz、步长 0.1GHz，然后跑仿真"

MCP 服务自动完成：扫描工程 → 打开 → 查询器件 → 更新参数 → 启动异步仿真 → 返回 task_id → 查询进度 → 获取结果。

---

## 架构

```
AI 客户端 (Claude Code / OpenClaw)
   │  Streamable HTTP (stateless) 或 stdio
   │  POST /mcp  │  initialize → tools/list → tools/call
   ▼
EDI gRPC MCP 服务 (FastMCP, 64 工具, 6 Resource, 8 Prompt)
   │
   ├── EDA gRPC 工具 (46) ──→ EDI 客户端 (127.0.0.1:50055)
   │     FetchEvent ← PerformAction 异步模型，增量 ads_output
   │
   ├── TurboCharts (3) ──→ turbocharts_app.exe (subprocess)
   │     ADS RAW → 曲线图 + CSV，串行信号量保护
   │
   ├── ANSYS HFSS (6) ──→ ansysedt.exe (COM 附着)
   │     多 ProgID 回退，锁文件管理，异步任务队列
   │
   ├── 视觉分析 ──→ Vision API (可选, OpenAI 兼容)
   │
   ├── 报告渲染 ──→ Report Render Service (可选, POST /api/v1/reports/render)
   │
   └── Chat ──→ LLM API (可选, OpenAI 兼容)
         会话管理，多轮工具闭环，破坏性操作确认门
```

---

## 快速开始

### 安装

```powershell
pip install edi-grpc-mcp
```
或源码：`git clone <repo-url> && cd edi-grpc-mcp && uv sync`

### 配置

创建 `.env`，留空的字段自动检测：

```ini
EDA_GRPC_SERVER=127.0.0.1:50055
EDI_PATH=                    # 留空自动检测
TURBOCHARTS_PATH=            # 留空自动检测
MCP_TRANSPORT=streamable-http
MCP_PORT=50026
MCP_API_KEY=                 # 可选：留空不鉴权；配置后 /mcp /ui /chat 等要求 ?token= 匹配
OPENCLAW_WORKSPACE=          # 留空自动检测，或手动指定
```

自动检测规则：
- `EDI_PATH`：项目同级找 `EDI.exe` → `EDA-PMDS.exe` → `CAIS.exe`
- `TURBOCHARTS_PATH`：项目同级找 `turbocharts_app.exe` → `TurboCharts.exe`
- `OPENCLAW_WORKSPACE`：edi-mcp 同级 `rfclaw/openclaw-service/state/workspace`，回退 `~/.openclaw/workspace`

### 启动

```powershell
edi-grpc-mcp                    # Streamable HTTP，默认 50026
edi-grpc-mcp --transport stdio  # Claude Code stdio 模式
edi-grpc-mcp --port 9000        # 自定义端口
```

### 验证

```powershell
curl http://127.0.0.1:50026/health     # 进程 + gRPC 状态
→ {"status":"ok","mcp_ready":true,"eda_grpc_ready":true}

curl http://127.0.0.1:50026/ready      # 初始化完成 (启动中 503)
→ {"status":"ready","transport":"streamable-http","stateless":true,"tool_count":64}
```

### 客户端接入

```json
// Claude Code (.mcp.json)
{ "mcpServers": { "eda": {
    "command": "edi-grpc-mcp", "args": ["--transport", "stdio"]
} } }

// OpenClaw
{ "mcpServers": { "eda-mcp": {
    "baseUrl": "http://127.0.0.1:50026/mcp"
} } }
```

> 配置了 `MCP_API_KEY` 后，Streamable HTTP 客户端（OpenClaw、Hermes 等）的 URL 需带 `?token=` 才能访问，例如 `http://127.0.0.1:50026/mcp?token=xxx`，不带则返回 401。stdio 模式（Claude Code）不经过 HTTP，不受影响。
>
> 服务启动日志会打印 token 值（`Auth: enabled (?token=xxx required on /mcp)`），可直接复制。内置聊天界面通过 `http://127.0.0.1:50026/ui?token=xxx` 访问，页面内的请求会自动带上该 token。

---

## 使用方式

| 方式 | 说明 |
|---|---|
| **MCP 客户端** | Claude Code / OpenClaw 接入后，自然语言调用全部 64 个工具 |
| **聊天界面** | 浏览器访问 `http://127.0.0.1:50026/ui`，内置 LLM 多轮工具闭环 |
| **Python 调用** | `from servers.eda import list_epp_projects` 直接调用 |

```python
from servers.eda.project_manage import list_epp_projects
from servers.eda.simulation import start_simulation_async

r = list_epp_projects("C:/Users/JGL/EDI-Workspace")
# → {"success": True, "count": 3, "projects": [...]}
r = start_simulation_async("C:/Projects/test/test.epp")
# → {"success": True, "task_id": "abc123...", "status": "QUEUED"}
```

---

## 工具一览（64 个）

> 工具数量由运行时动态统计，此处为当前快照。权威值见 `/ready` 的 `tool_count`（或 `tests/test_tool_registry.py` 的 `required` 列表）。

### 工程管理（9 个）

| 工具 | 说明 |
|---|---|
| `list_epp_projects` | 扫描文件夹中的 .epp 工程 |
| `create_project` | 创建新的 .epp 工程 |
| `open_edi_project` | 打开 .epp 工程 |
| `close_edi_project` | 关闭工程 |
| `list_schematic_components` | 查询原理图全部器件（gRPC，含完整参数） |
| `get_schematic_component_info` | 按实例名查询器件完整信息（gRPC） |
| `get_project_summary` | 工程概览（元数据/原理图/仿真配置） |
| `analyze_variables` | 分析变量定义、引用和 Sweep 配置 |
| `get_components_static_params` | 查询器件固有参数（重量/尺寸/封装/厂商/成本） |

### 仿真器件（10 个）— 工具 API v3 / gRPC 协议 v2

| 工具 | 说明 |
|---|---|
| `get_simulation_component_schema` | 查询 SP/HB/XDB 支持的参数和权限 |
| `list_simulation_components` | 列出全部器件，支持过滤/分页/隐藏参数（本地读） |
| `create_simulation_component` | 新增器件（EDI 默认参数，创建后 update 设参） |
| `update_simulation_component` | 按实例名更新参数（三路类型推断） |
| `delete_simulation_component` | 按实例名删除器件 |
| `replace_port_component` | 替换端口器件类型（TermG↔P_nToneG） |
| `set_component_active_state` | 确定性设置 NORMAL / DISABLED / SHORTED |
| `generate_schematic_from_netlist` | 从网表导入生成原理图 |
| `attach_out_component` | 为器件引脚挂载 Out 器件并自动连线 |
| `replace_schematic_from_file` | 从 .ep 文件整体替换原理图 |

### 仿真（8 个）

| 工具 | 说明 |
|---|---|
| `start_simulation_async` | 启动异步仿真，立即返回 task_id（推荐） |
| `get_simulation_async_status` | 查询实时进度和增量日志 |
| `get_simulation_async_result` | 获取完整结果和 ads_output |
| `list_eda_tasks` | 列出当前仿真任务 |
| `simulate_project` | 执行工程仿真（同步阻塞） |
| `simulate_netlist` | 仿真网表文件 |
| `simulate_netlist_with_ads` | 调用 ADS 仿真控制器 |
| `simulate_anti_burnout` | 抗烧毁仿真与风险评估 |

### 导出与分析

| 工具 | 说明 |
|---|---|
| `export_project_netlist` | 查看/导出工程网表 |
| `capture_schematic` | 截取原理图为图片 |
| `get_signal_chain` | 追踪信号链路（节点接力算法） |

### 模型库（6 个）

| 工具 | 说明 |
|---|---|
| `replace_models_from_csv` | 按 CSV 批量替换模型 |
| `get_model_category_params` | 获取模型分类及参数列表 |
| `search_public_models` | 按子类查询公共模型库 |
| `search_personal_models` | 按子类查询个人模型库 |
| `load_performance_component_from_mms` | 从 MMS 导入性能模型到本地模型库 |
| `add_performance_component` | 放置模型库中的性能器件到原理图 |

### 启动 / 诊断（3 个）

| 工具 | 说明 |
|---|---|
| `launch_edi` | 启动 EDI 客户端并等待 gRPC 就绪 |
| `get_service_status` | 返回 gRPC 通道状态、队列信息 |
| `get_service_logs` | 读取 EDI 服务端日志并分析异常 |

### 工作区（3 个）

| 工具 | 说明 |
|---|---|
| `create_workspace` | 创建工作区（不自动切换） |
| `switch_workspace` | 设置下次启动使用的工作区 |
| `get_current_workspace` | 查询当前实际加载的工作区目录 |

### 原理图扩展（4 个）

| 工具 | 说明 |
|---|---|
| `list_ideal_components` | 列出内置器件类型及说明 |
| `add_ideal_component` | 按指定坐标新增内置器件 |
| `clear_schematic` | 清空原理图（破坏性，需 confirm_clear） |
| `add_wire` | 连接两个器件的指定引脚 |

### ANSYS HFSS（6 个）

| 工具 | 说明 |
|---|---|
| `open_hfss_project` | 启动 AEDT 并打开 .aedt 项目 |
| `close_hfss_project` | 关闭 AEDT 项目 |
| `launch_aedt` | 启动 AEDT |
| `get_hfss_project_info` | 查询项目列表和活动设计 |
| `start_hfss_analysis_async` | 异步启动 HFSS 仿真 |
| `get_hfss_analysis_status` | 查询 HFSS 仿真状态 |

### CST 电磁仿真（5 个）

| 工具 | 说明 |
|---|---|
| `cst_solve_async` | 异步求解 .cst 模型（一次性会话） |
| `cst_solve_query` | 查询求解任务（进度+结果） |
| `cst_export_snp` | 导出 S 参数为 Touchstone .sNp |
| `cst_export_farfield` | 导出远场方向图为 ASCII .txt（自动判断求解） |
| `cst_export_farfield_query` | 查询远场导出任务（进度+结果） |

### 图表与图片

| 工具 | 说明 |
|---|---|
| `list_result_curves` | 解析 RAW 返回可用曲线名 |
| `turbocharts_convert` | ADS RAW → 曲线图 + CSV |
| `compare_simulation_results` | 多 RAW 同曲线对比叠图（Matplotlib） |
| `show_image` | 返回 MCP ImageContent + 本地路径 |
| `analyze_image` | 调用视觉模型分析图片内容 |

### 报告与文档

| 工具 | 说明 |
|---|---|
| `generate_simulation_report` | 仿真数据 → PDF/DOCX，自动返回 HTTP 预览链接 |
| `open_document` | 打开本地文档（link 链接 / local 系统打开） |

> 完整参数说明见 [工具 API](./docs/TOOLS_API.md)。

---

## 接口设计

### 传输方式

| 方式 | 端点 | 适用场景 |
|---|---|---|
| **Streamable HTTP**（默认） | `POST http://127.0.0.1:50026/mcp` | OpenClaw、Web 客户端 |
| **stdio** | 标准输入输出 | Claude Code、本地桌面客户端 |

Streamable HTTP 模式启用 `stateless_http=True`，服务不保留 MCP 会话状态，重启后新请求自动重建连接。

### HTTP 路由

| 路由 | 方法 | 说明 | 响应示例 |
|---|---|---|---|
| `/health` | GET | 进程存活 + gRPC 连接状态 | `{"status":"ok","mcp_ready":true,"eda_grpc_ready":true}` |
| `/ready` | GET | 服务是否完成初始化（启动中返回 503） | `{"status":"ready","transport":"streamable-http","stateless":true,"tool_count":64}` |
| `/mcp` | POST | MCP 协议端点（Streamable HTTP） | MCP JSON-RPC 响应 |
| `/ui` | GET | 内置聊天界面 | HTML 页面 |
| `/chat` | POST | 聊天 API（LLM 多轮工具闭环） | `{"success":true,"reply":"...","activities":[...]}` |
| `/tools/list` | GET | 已注册工具列表 | `[{"name":"list_epp_projects","description":"..."}]` |
| `/images/{token}` | GET | 临时图片访问（10 分钟有效） | 图片文件 |
| `/documents/{token}` | GET | 临时文档访问（10 分钟有效） | PDF/DOCX 文件 |
| `/upload` | POST | 文件上传（multipart/form-data） | `{"success":true,"file_path":"C:/...","file_name":"..."}` |
| `/metrics` | GET | 运行时指标（Prometheus 格式） | 见 HTTP_API.md |

### MCP Resources（6 个，只读上下文）

客户端通过 `resources/list` 和 `resources/read` 访问。

| URI | MIME | 说明 |
|---|---|---|
| `edi://service/overview` | `application/json` | 服务版本、gRPC 协议 v2、工具 API v3、安全规则、工作区状态 |
| `edi://service/status` | `application/json` | 实时运行时状态（gRPC 通道、队列占用、工具指纹） |
| `edi://projects` | `application/json` | 工作区工程目录清单（名称/路径/大小） |
| `edi://reference/simulation-components` | `application/json` | SP/HB/XDB 参数目录，与 `get_simulation_component_schema` 同源 |
| `edi://reference/operation-guide` | `text/markdown` | 操作安全约束：创建/删除/网表导入规则 |
| `edi://reference/error-codes` | `text/markdown` | gRPC 状态码词典及建议动作 |

### MCP Prompts（8 个，可复用工作流）

| Prompt | 参数 | 说明 |
|---|---|---|
| `inspect_edi_project` | `project_path`, `detail_level` | 只读检查：概览 → 变量 → 器件 → 仿真配置 |
| `run_and_review_simulation` | `project_path`, `execution_mode`, `analyze_log` | 异步仿真 + 日志分析，含轮询限制 |
| `configure_simulation_component` | `project_path`, `action`, `component_type`, `instance_name`, `requirements` | Schema → 参数映射 → 确认 → 创建/更新 |
| `create_simulation_report` | `project_path`, `output_path`, `overwrite` | 查询工程 → 确认结果 → 生成曲线 → 渲染 PDF/DOCX |
| `troubleshoot_edi_error` | `status`, `error_code` | 按状态码查错误词典、检查服务状态、给排查建议 |
| `assess_anti_burnout` | `project_path` | 抗烧毁评估并按功率裕量排序 |
| `select_component` | `sub_type_id`, `requirement` | 从公共/个人模型库选型（含替换闭环） |
| `analyze_signal_chain` | `project_path`, `start` | 追踪信号链路并逐级说明 |

---

## 工具返回结构

### gRPC 工具统一返回

```python
{
    "success": bool,        # 本次 MCP 调用是否成功
    "completed": bool,      # MCP 侧任务是否结束
    "outcome_known": bool,  # 是否收到 EDI 最终事件（SUCCEEDED/FAILED）
    "task_success": bool,   # EDI 任务是否成功（仅 outcome_known=True 有意义）
    "status": str,          # SUCCEEDED / FAILED / TIMEOUT / STREAM_DISCONNECTED / ...
    "message": str,         # 描述信息
    "project_path": str,    # 工程路径
    "result_path": str,     # 结果文件路径（如 RAW 文件）
    "ads_output": str,      # 增量拼接的完整仿真器日志
    "log_complete": bool,   # 日志是否完整接收
    "details": dict,        # 原始事件 payload 字段
}
```

### 异步仿真生命周期

```
QUEUED → ACCEPTED → RUNNING → SUCCEEDED / FAILED
                                     ↓
                              2 小时后自动清理
```

超时/断连时：`outcome_known=False, task_success=None`，不冒充 EDI 业务失败。

### 产物格式 (artifacts)

`turbocharts_convert`、`capture_schematic`、`compare_simulation_results`、`generate_simulation_report` 返回统一产物：

```json
{
  "success": true,
  "artifacts": [
    {"type": "image", "path": "C:/.../gain.png", "name": "gain.png", "generated_by": "turbocharts_convert"},
    {"type": "csv", "path": "C:/.../gain.csv", "name": "gain.csv", "generated_by": "turbocharts_convert"}
  ],
  "message": "曲线图已生成。"
}
```

报告额外返回 `preview_url`（10 分钟有效 HTTP 链接）。

---

## Chat 接口

内置 LLM 多轮工具调用闭环，通过 `POST /chat` 和浏览器 `/ui` 访问。

### 请求

```json
POST /chat
{"session_id": "", "message": "帮我扫描 C:/Projects 下的工程"}
```

### 响应

```json
{
  "success": true,
  "session_id": "a1b2c3d4",
  "reply": "找到 3 个工程：demo1.epp, demo2.epp, test.epp",
  "activities": [{"tool": "list_epp_projects", "status": "success", "summary": "找到 3 个工程"}],
  "context": {"current_project_name": null},
  "media": []
}
```

### 特性

- 会话保持（2h TTL，100 上限），自动记忆当前工程和最近仿真 task_id
- 最多 5 轮工具调用，单轮最多 8 个工具
- 参数自动补齐：`project_path` 从会话上下文，`task_id` 从最近仿真
- 破坏性操作确认门：delete/replace/close_save/overwrite 需用户确认，支持肯定词
- 默认值透明：产生输出文件或采用默认值时，先告知用户输出位置/默认值并询问是否调整
- 重复调用保护：同轮同参数指纹去重
- 空 session_id 自动创建，服务重启后旧 session 返回 Session not found

---

## 配置一览

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `EDA_GRPC_SERVER` | `127.0.0.1:50055` | EDI gRPC 服务地址 |
| `MCP_TRANSPORT` | `streamable-http` | 传输方式（streamable-http / stdio） |
| `MCP_STATELESS_HTTP` | `true` | 无状态模式 |
| `MCP_HOST` | `127.0.0.1` | 监听地址（强制本地） |
| `MCP_PORT` | `50026` | HTTP 监听端口 |
| `MCP_API_KEY` | — | 访问令牌（留空不鉴权；配置后 `/mcp` `/ui` `/chat` 等要求 `?token=` 匹配） |
| `EDI_PATH` | 自动检测 | EDI.exe 路径 |
| `TURBOCHARTS_PATH` | 自动检测 | turbocharts_app.exe 路径 |
| `OPENCLAW_WORKSPACE` | 自动检测 | OpenClaw 工作区路径 |
| `LLM_API_KEY` | — | Chat AI 功能 |
| `LLM_BASE_URL` | — | LLM API 地址 |
| `LLM_MODEL` | — | 模型名称 |
| `VISION_API_KEY` | — | 视觉分析（三项全填开启） |
| `VISION_BASE_URL` | — | 视觉模型 API 地址 |
| `VISION_MODEL` | — | 视觉模型名称 |
| `REPORT_RENDER_URL` | `http://127.0.0.1:17867/api/v1/reports/render` | 报告渲染服务 |

---

## 项目结构

```
edi-grpc-mcp/
│
├── proto/                              # protobuf 协议定义及编译产物
│   ├── ecserver.proto                  #   gRPC 服务定义（ExternalCall, 27 种 EventType）
│   ├── ecserver_pb2.py                 #   protobuf 编译消息类
│   ├── ecserver_pb2_grpc.py            #   protobuf 编译 Stub/Servicer
│   └── grpc接口调用.md                 #   gRPC 协议完整文档（含 payload 示例）
│
├── servers/                            # MCP 服务主体
│   ├── __init__.py                     #   FastMCP 实例 + 版本号，支持 stateless_http
│   ├── registry_server.py              #   注册入口：导入所有子包触发 @mcp.tool() 注册
│   │                                   #   同时注册 8 条 HTTP 自定义路由
│   ├── utils.py                        #   公共工具层
│   │                                   #     validate_file() — 文件校验
│   │                                   #     error_response() — 统一错误响应（submitted_response / queue_full_response 同处）
│   │                                   #     build_file_link() — file:// + Markdown 链接
│   │                                   #     get_server_base_url() — 运行时地址
│   │                                   #     set_server_address() — CLI 参数覆盖
│   │                                   #     ServerAddress — 运行时地址 dataclass
│   │                                   #     SERVER_STARTED_AT — 服务启动时间戳
│   ├── settings.py                     #   统一配置：Settings dataclass (frozen, lru_cache)
│   │                                   #   所有环境变量收敛于此，启动时 validate()
│   ├── task_runner.py                  #   通用异步任务队列（EDA/HFSS/CST 复用，单 worker 串行）
│   │
│   ├── resources_prompts/              #   MCP Resource & Prompt（6 Resource + 8 Prompt）
│   │   ├── __init__.py                 #     注册入口（import 下面 6 模块触发注册）
│   │   ├── resources_service.py        #     服务状态类（概览 / 状态 / 工程目录）
│   │   ├── resources_reference.py      #     参考类（参数目录 / 操作规则 / 错误码）
│   │   ├── prompts_project.py          #     工程类（检查 / 信号链）
│   │   ├── prompts_simulation.py       #     仿真类（仿真 / 抗烧毁）
│   │   ├── prompts_component.py        #     器件类（配置 / 选型）
│   │   └── prompts_report.py           #     报告类（报告 / 诊断）
│   │
│   ├── eda/                            #   EDI 工程工具 (46 个)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── config.py                   #     路径检测 / 环境变量加载
│   │   ├── project_reader.py           #     ProjectReader + S-expression 解析器
│   │   ├── grpc_client.py              #     gRPC 通信层：FetchEvent + PerformAction 异步模型
│   │   │                               #     全局 _EDA_LOCK 串行锁，增量 ads_output 收集
│   │   │                               #     _terminal_result() 统一返回结构
│   │   ├── project_manage.py           #     工程管理：扫描/创建/打开/关闭/元件/概述/变量分析/静态参数 (9 工具)
│   │   ├── simulation.py               #     仿真引擎：同步/异步/网表/ADS 控制器/抗烧毁 (8 工具)
│   │   │                               #     ThreadPoolExecutor(1) 串行执行，最多 8 个排队任务
│   │   ├── simulation_components.py    #     仿真器件管理：10 工具（含 Out 挂载）
│   │   │                               #     11 步参数校验管线 + wire↔public 名称映射
│   │   ├── simulation_component_catalog.json  # SP/HB/XDB 参数目录 v2.0
│   │   ├── design_export.py            #     网表查看 + 原理图截图 (2 工具)
│   │   ├── signal_chain.py             #     信号链路追踪（节点接力算法）(1 工具)
│   │   ├── model_replace.py            #     CSV 批量模型替换 (1 工具)
│   │   ├── model_library.py            #     模型库：查询/MMS 导入/性能器件放置 (5 工具)
│   │   ├── edi_launcher.py             #     启动 EDI + 服务诊断/日志读取 (3 工具)
│   │   ├── workspace_ops.py            #     工作区：创建/切换/查询 (3 工具)
│   │   └── schematic_ops.py            #     原理图扩展操作 (4 工具)
│   │
│   ├── turbocharts/                    #   ADS RAW 图表工具 (3 个)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── config.py                   #     run_turbocharts() 串行信号量执行器
│   │   ├── convert_raw.py              #     RAW→曲线图+CSV，VSWR 自动拆分，曲线查询
│   │   └── compare_results.py          #     多 RAW 对比叠图 (Matplotlib), alignment 校验
│   │
│   ├── ansys/                          #   ANSYS HFSS 工具 (6 个, COM 附着)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── config.py                   #     进程检测 / COM 附着 (多 ProgID 回退) / 锁文件管理
│   │   ├── project_manage.py           #     工程打开/关闭 + AEDT 启动 + 信息查询 (4 工具)
│   │   └── run_analysis.py             #     异步仿真（复用 TaskRunner 单 worker 队列）, outcome_known 追踪
│   │
│   ├── cst/                            #   CST 电磁仿真工具 (5 个)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── cst_api.py                  #     安装检测 / API 加载 / 会话管理
│   │   ├── simulate.py                 #     仿真求解（异步，一次性会话）
│   │   └── result_export.py            #     结果导出（S 参数 / 远场方向图）
│   │
│   ├── multimodal_vision/              #   图片 + 视觉 + 文档 (5 个工具)
│   │   ├── __init__.py                 #     copy_image_to_workspace（已隐藏）
│   │   ├── validators.py               #     共享校验：图片路径/扩展名/Pillow 内容验证
│   │   ├── image_display.py            #     show_image + HTTP /images/{token} 路由
│   │   ├── vision_analyzer.py          #     analyze_image (OpenAI Vision API, Semaphore(2))
│   │   ├── workspace_copy.py           #     copy_image_to_workspace（已隐藏，暂不检测工作区）
│   │   └── document.py                 #     open_document（link/local）+ /documents/{token}
│   │
│   ├── report/                         #   仿真报告渲染 (1 个工具)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   └── generator.py               #     16 步校验 → POST 渲染服务 → 返回 preview_url
│   │
│   └── chat/                           #   Chat 聊天模块
│       ├── __init__.py                 #     包标识
│       ├── service.py                  #     ChatService 单例：会话管理、LLM 调用、工具闭环
│       │                               #     _auto_build_chat_tools() 从 MCP 元数据自动生成
│       ├── routes.py                   #     Web 路由：/chat /upload /ui /health /tools/list
│       └── index.html                  #     聊天前端页面
│
├── docs/                               # 项目文档
│   ├── DEPLOY.md                       #   部署指南（打包产物使用、客户端配置）
│   ├── TOOLS_API.md                    #   工具 API（64 个工具完整签名+返回值示例）
│   ├── HTTP_API.md                     #   HTTP 接口（请求体、响应体、成功/失败情况）
│   ├── IMPLEMENTATION.md               #   实现原理（通信类型、校验管线、并发控制、工具动机与依赖）
│   ├── RESOURCES_PROMPTS.md            #   Resource & Prompt 说明（6 Resource + 8 Prompt 的用途与实现）
│   ├── HANDOVER.md                     #   交接文档（架构设计、技术栈、47 条注意事项）
│   └── EDI系统接口与外部调用汇总.md    #   EDI 系统全量对外接口
│
├── tests/                              # 测试套件 (346 项)
│   ├── test_chat_service.py            #   28 项：会话/校验/重复调用/上下文/show_image
│   ├── test_simulation_components.py   #   89 项：参数目录/Schema/校验管线/wire转换
│   ├── test_simulation.py              #   17 项：任务注册表/事件回调/生命周期
│   ├── test_grpc_client.py             #   24 项：终端结果/日志累积/异常处理
│   ├── test_report_generator.py        #   26 项：输出路径/模型名/spec_table/charts/components
│   ├── test_mcp_content.py             #   13 项：Resources/Prompts 直接调用+MCP协议冒烟
│   ├── test_tool_registry.py           #   7 项：完整工具注册+Chat一致性的双重验证
│   ├── test_project_reader.py          #   5 项：S-expression 解析/元件提取
│   ├── test_component_tools.py         #   4 项：list/过滤/分页/参数查询
│   ├── test_compare_results.py         #   2 项：多 RAW 对比对齐/插值参考轴
│   ├── test_task_runner.py             #   9 项：异步队列生命周期/队列满/清理
│   ├── test_cst.py                     #   22 项：共享函数/静态解析/查询返回/导出流程 mock
│   ├── test_turbocharts_runner.py      #   3 项：串行执行器超时范围
│   ├── test_utils.py                   #   12 项：文件校验/错误响应/地址管理/链接生成
│   ├── test_settings.py                #   10 项：环境变量读取/范围限制/启动校验
│   ├── test_ansys.py                   #   9 项：HFSS 队列迁移后逻辑（mock COM/AEDT）
│   └── test_health.py                  #   2 项：TCP 检查
│
├── scripts/                            # 构建与启动脚本
│   ├── build.ps1                       #   PyInstaller 打包脚本（体积检查+过滤敏感配置）
│   ├── edi_mcp_server.spec             #   PyInstaller spec（hiddenimports+excludes）
│   ├── run.bat                         #   快速启动批处理
│   └── Logo.ico                        #   应用图标
│
├── dist/                               # 打包产物（不提交 Git）
│   └── edi-mcp/                        #   edi_mcp_server.exe + _internal/ + .env 模板
│
├── start_servers.py                    # 主入口：配置校验 → 所有模块导入 → MCP 启动
├── pyproject.toml                      # uv 项目配置 + PyPI 元数据
├── .mcp.json                           # Claude Code MCP 配置示例
├── .env                                # 本地配置（不提交 Git）
└── README.md                           # 本文件
```

---

## 测试

| 测试文件 | 覆盖范围 | 项数 |
|---|---|---|
| `test_simulation_components.py` | 参数目录 / Schema 查询 / 11 步校验 / wire 转换 / 权限 / 别名冲突 | 90 |
| `test_chat_service.py` | 会话隔离 / 工具白名单 / 重复保护 / 上下文更新 / 消息裁剪 | 28 |
| `test_grpc_client.py` | 终端结果构建 / 日志累积 / 任务隔离 / 异常处理 / 协议不匹配 | 24 |
| `test_report_generator.py` | 输出路径 / 模型名 / spec_table / charts / components / timeout | 26 |
| `test_simulation.py` | 任务注册表 / 事件回调 / TaskLifecycle / TASK_NOT_FOUND | 17 |
| `test_mcp_content.py` | Resources 结构 / Prompts 参数校验 / MCP 协议 list/read/get | 18 |
| `test_tool_registry.py` | 完整注册验证 / Chat 一致性 / 破坏性工具 / 工具数动态统计 | 7 |
| `test_project_reader.py` | S-expression 解析 / 元件提取 | 5 |
| `test_component_tools.py` | 元件列表 / 类型过滤 / 分页 / 参数查询 | 4 |
| `test_turbocharts_runner.py` | 超时范围校验 | 3 |
| `test_health.py` | TCP 连接检查 | 2 |
| `test_compare_results.py` | 多 RAW 对比对齐 / 插值参考轴校验 | 2 |
| `test_task_runner.py` | 异步队列生命周期 / 结果写回 / 队列满 / 清理 | 9 |
| `test_cst.py` | 共享函数 / 静态解析 / 查询返回结构 / 导出流程 mock | 23 |
| `test_utils.py` | 文件校验 / 错误响应 / 地址管理 / 链接生成 | 12 |
| `test_settings.py` | 环境变量读取 / 范围限制 / 启动校验 | 9 |
| `test_ansys.py` | HFSS 队列迁移后逻辑（mock COM / AEDT） | 9 |
| `test_bugfixes.py` | 历史 bug 修复回归测试 | 33 |
| `test_token_auth.py` | MCP 访问令牌鉴权（token 中间件） | 5 |

```powershell
uv run pytest -q                 # 全量 346 项
uv run pytest tests/ -v          # 详细输出
uv run pytest tests/test_simulation_components.py -v  # 单文件
```

---

## 打包

```powershell
uv build && uv publish           # PyPI
powershell -File scripts/build.ps1  # PyInstaller
# → dist/edi-mcp/（edi_mcp_server.exe + _internal/ + .env）
#   打包完成后自动冒烟测试（启动 exe → 健康检查 → 鉴权 → 工具注册）
```

---

## 文档

| 文档 | 说明 |
|---|---|
| [部署指南](./docs/DEPLOY.md) | 打包产物使用、客户端配置 |
| [工具 API](./docs/TOOLS_API.md) | 全部 64 个工具参数、返回值、示例 |
| [HTTP 接口](./docs/HTTP_API.md) | 全部 HTTP 路由的请求体、响应体、成功/失败情况 |
| [实现原理](./docs/IMPLEMENTATION.md) | 5 种通信类型、校验管线、并发控制、工具动机与依赖 |
| [Resource & Prompt](./docs/RESOURCES_PROMPTS.md) | 6 Resource + 8 Prompt 的用途、功能与实现 |
| [交接文档](./docs/HANDOVER.md) | 架构设计、技术栈、扩展开发、47 条注意事项 |
| [gRPC 协议](./proto/grpc接口调用.md) | ExternalCall 接口调用说明 |
| [EDI 系统接口汇总](./docs/EDI系统接口与外部调用汇总.md) | EDI 全量对外接口 |

---

## 常见问题

### 端口占用

启动时会自动检测端口是否被占用：若被占用，自动结束占用进程（如残留的 `edi_mcp_server.exe`）并继续启动，无需手动处理。

仅当自动清理失败（如权限不足）时才需手动结束：

```powershell
netstat -ano | findstr 50026              # 查找占用端口的 PID
taskkill -f -pid <PID>                     # 强制结束该 PID
taskkill -f -im edi_mcp_server.exe        # 或按进程名结束
```

### gRPC 状态

```powershell
netstat -ano | findstr 50055         # LISTENING = EDI 运行中
curl http://127.0.0.1:50026/health   # 健康检查
curl http://127.0.0.1:50026/ready    # 就绪检查
```

### 图片

- `show_image` 始终可用，未配置工作区时提示用资源管理器打开
- `copy_image_to_workspace` 已暂时隐藏（当前不使用 OpenClaw）
- `analyze_image` 仅用户明确要求时调用，会上传到第三方

### 服务重启

- 重启后旧 MCP session 失效，客户端重新 initialize
- 仿真任务在内存中，重启后查询返回 TASK_NOT_FOUND
- 不自动重放工具调用


