# EDI gRPC MCP

> **让 AI 用自然语言驱动 EDA 设计与仿真** —— 一句话完成「打开工程 → 配置器件 → 跑仿真 → 出报告」，三大仿真引擎统一封装。

[![PyPI](https://img.shields.io/pypi/v/edi-grpc-mcp?label=PyPI)](https://pypi.org/project/edi-grpc-mcp/)
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## ✨ 亮点

- 🛠️ **91 个 MCP 工具，全链路闭环** — 工程管理 → 器件配置 → 模型选型 → 信号链分析 → 仿真 → 出图 → 出报告，一个服务走到底
- ⚡ **三大仿真引擎统一封装** — EDI gRPC（ADS）· ANSYS HFSS · CST，同一套工具、同一种返回结构
- 🌐 **本机 / 远程一键切换** — 双击 `start_local.bat`（本机）或 `start_remote.bat`（远程）；`--host 0.0.0.0` 即远程，**地址零配置、机器不固定、主机端零安装**（详见 [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md)「三、特殊机制」）
- 🧠 **自然语言驱动** — 接入 Claude Code / Hermes / OpenClaw，一句「帮我看看工程、跑个仿真」即可
- 📈 **异步仿真 + 实时日志** — `task_id` 追踪、`ads_output` 增量推送，长任务不阻塞、进度可查
- 🔒 **统一返回契约** — 每个工具返回 `success` / `error_code` / `hint`（retry_safe / do_not_retry）；异步任务带 `outcome_known`（重启后「结果未知」不会误判成失败）
- 🖼️ **产物回传** — 截图（≤10MB）内嵌返回；报告 / 文档 / 任意产物（`fetch_artifact`）生成可下载链接，地址按请求 `Host` 推导，远程也能点开

### 一句话示例

**本机**：

> "帮我看看 C:/Projects 下有哪些 .epp 工程，打开第一个，查看 S 参数仿真器件的配置，设置频率 1-10GHz、步长 0.1GHz，然后跑仿真"

MCP 服务自动完成：扫描工程 → 打开 → 查询器件 → 更新参数 → 启动异步仿真 → 返回 task_id → 查询进度 → 获取结果。

**远程**（引擎机 (远程机器) 装对应的工具软件，主机 (本机) 安装对应客户端(Hermes)，只发指令）：

> "连 249 那台机器，看看 D:\EDI-Workspace 有哪些工程，跑一次 HFSS 求解，把 S 参数结果拿回来"

主机只发 MCP 指令；软件启动、仿真执行、产物生成都在远程机器上，产物经可下载链接回传到主机。

---

## 架构

```
AI 客户端 (Claude Code / OpenClaw)
   │  Streamable HTTP (stateless) 或 stdio
   │  POST /mcp  │  initialize → tools/list → tools/call
   ▼
EDI gRPC MCP 服务 (FastMCP, 91 工具, 8 Resource, 9 Prompt)
   │
   ├── EDA gRPC 工具 (55) ──→ EDI 客户端 (127.0.0.1:50055)
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
MCP_ALLOWED_PROCESSES=       # 可选：留空不鉴权；配置后只放行「精确匹配」的进程访问 /mcp（整词 token / exe basename / 完整路径）
```

自动检测规则：
- `EDI_PATH`：项目同级找 `EDI.exe` → `EDA-PMDS.exe` → `CAIS.exe`
- `TURBOCHARTS_PATH`：项目同级找 `turbocharts_app.exe` → `TurboCharts.exe`

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
→ {"status":"ready","transport":"streamable-http","stateless":true,"tool_count":90}
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

### 安全与远程

- **本机 / 远程一键切换**：双击 `start_local.bat`（本机）或 `start_remote.bat`（远程）；`--host 0.0.0.0` 即远程。
- **接入控制**：进程白名单（本机模式生效，远程自动忽略）；本期不加鉴权（内网直接访问）。

> 远程 / 进程白名单等**特殊机制** → [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md)；**HTTP 路由与访问控制** → [`docs/HTTP_API.md`](docs/HTTP_API.md)；**Win7 现场部署** → [`docs/WIN7部署与启动手册.md`](docs/WIN7部署与启动手册.md)；**待修问题** → [`docs/审查报告.md`](docs/审查报告.md)。

---

## 使用方式

| 方式 | 说明 |
|---|---|
| **MCP 客户端** | Claude Code / OpenClaw 接入后，自然语言调用全部 91 个工具 |
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

## 工具一览（91 个）

91 个工具按领域分组：工程管理 / 仿真器件 / 仿真 / 导出与分析 / 模型库·原理图库 / 软 IP / 启动·诊断 / 工作区 / 原理图扩展 / ANSYS HFSS / CST / TR 仿真集成 / 图表与图片 / 报告与文档。

> 每个工具的**详细参数、返回、示例** → **[`docs/TOOLS_API.md`](docs/TOOLS_API.md)**；工具清单由 `scripts/gen_tool_index.py` 自动生成 → [`docs/TOOL_INDEX.md`](docs/TOOL_INDEX.md)。

---
## 接口设计

- **传输**：Streamable HTTP（stateless）或 stdio。
- **HTTP 路由**（每个路由的请求/响应体）→ [`docs/HTTP_API.md`](docs/HTTP_API.md)。
- **实现原理与机制**（底层通信 / 公有设置方法 / 特殊机制 / 工具 / Resources / Prompts）→ [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md)。

---
## 工具返回结构

统一返回契约：`success` / `error_code` / `message` / `hint`；gRPC 工具业务数据在 `details`；异步任务走 `task_id` + `status` + `outcome_known`。完整结构 → [`docs/TOOLS_API.md`](docs/TOOLS_API.md)。

---
## Chat 接口

内置 LLM 多轮工具闭环（`/chat`，需配 `LLM_*`）。请求/响应格式 → [`docs/HTTP_API.md`](docs/HTTP_API.md) §5。

---
## 配置

`.env` 配置项**完整清单** → [`docs/HANDOVER.md`](docs/HANDOVER.md)「配置说明」；Win7 现场必改项 → [`docs/WIN7部署与启动手册.md`](docs/WIN7部署与启动手册.md)。

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
│   ├── eda/                            #   EDI 工程工具 (51 个)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── config.py                   #     路径检测 / 环境变量加载
│   │   ├── project_reader.py           #     ProjectReader + S-expression 解析器
│   │   ├── grpc_client.py              #     gRPC 通信层：FetchEvent + PerformAction 异步模型
│   │   │                               #     全局 _EDA_LOCK 串行锁，增量 ads_output 收集
│   │   │                               #     _terminal_result() 统一返回结构
│   │   ├── project_manage.py           #     工程管理：扫描/创建/打开/关闭/元件/概述/变量分析/静态参数 (9 工具)
│   │   ├── simulation.py               #     仿真引擎：异步/网表/ADS 控制器/抗烧毁 (7 工具)
│   │   │                               #     ThreadPoolExecutor(1) 串行执行，最多 8 个排队任务
│   │   ├── simulation_components.py    #     仿真器件管理：10 工具（含 Out 挂载）
│   │   │                               #     11 步参数校验管线 + wire↔public 名称映射
│   │   ├── simulation_component_catalog.json  # SP/HB/XDB 参数目录 v2.0
│   │   ├── design_export.py            #     网表/截图/CSV 导出 (3 工具)
│   │   ├── signal_chain.py             #     信号链路追踪（节点接力算法）(1 工具)
│   │   ├── model_replace.py            #     CSV 批量模型替换 (1 工具)
│   │   ├── model_library.py            #     模型库 + 原理图库：查询/导入/放置/搜索 (9 工具)
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
│   ├── tr_simulation/                  #   SimulationAgent 集成 (17 个 tr_* 工具 + 1 Resource + 1 Prompt)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── client.py                   #     HTTP 客户端：会话管理 / 稳定 request_id / 调用与轮询
│   │   ├── tools.py                    #     17 个 tr_* 工具（@mcp.tool()）
│   │   ├── resource.py                 #     TR 工作流 Resource（edi://integration/workflow）
│   │   └── prompts.py                  #     TR 工作流 Prompt（run_tr_simulation）
│   │
│   ├── multimodal_vision/              #   图片 + 视觉 + 文档 (3 个工具)
│   │   ├── __init__.py                 #     公共 API re-export
│   │   ├── validators.py               #     共享校验：图片路径/扩展名/Pillow 内容验证
│   │   ├── image_display.py            #     show_image + HTTP /images/{token} 路由
│   │   ├── vision_analyzer.py          #     analyze_image (OpenAI Vision API, Semaphore(2))
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
│   ├── WIN7部署与启动手册.md           #   部署指南（打包产物、客户端配置）
│   ├── TOOLS_API.md                    #   工具 API（91 个工具完整签名+返回值示例）
│   ├── HTTP_API.md                     #   HTTP 接口（请求体、响应体、成功/失败情况）
│   ├── IMPLEMENTATION.md               #   实现原理与机制（通信原理、公有方法、特殊机制、工具、Resource/Prompt）
│   ├── HANDOVER.md                     #   交接文档（架构设计、技术栈、47 条注意事项）
│   └── EDI系统接口与外部调用汇总.md    #   EDI 系统全量对外接口
│
├── tests/                              # 测试套件 (375 项)
│   ├── test_simulation_components.py   #   90 项：参数目录/Schema/校验管线/wire转换
│   ├── test_chat_service.py            #   28 项：会话/校验/重复调用/上下文/show_image
│   ├── test_grpc_client.py             #   24 项：终端结果/日志累积/异常处理
│   ├── test_report_generator.py        #   26 项：输出路径/模型名/spec_table/charts/components
│   ├── test_simulation.py              #   17 项：任务注册表/事件回调/生命周期
│   ├── test_mcp_content.py             #   20 项：Resources/Prompts 直接调用+MCP协议冒烟
│   ├── test_tool_registry.py           #   7 项：完整工具注册+Chat一致性的双重验证
│   ├── test_project_reader.py          #   5 项：S-expression 解析/元件提取
│   ├── test_component_tools.py         #   4 项：list/过滤/分页/参数查询
│   ├── test_compare_results.py         #   2 项：多 RAW 对比对齐/插值参考轴
│   ├── test_task_runner.py             #   9 项：异步队列生命周期/队列满/清理
│   ├── test_cst.py                     #   23 项：共享函数/静态解析/查询返回/导出流程 mock
│   ├── test_turbocharts_runner.py      #   3 项：串行执行器超时范围
│   ├── test_utils.py                   #   19 项：文件校验/错误响应/地址管理/链接生成/require_position/require_uuid
│   ├── test_settings.py                #   9 项：环境变量读取/范围限制/启动校验
│   ├── test_ansys.py                   #   9 项：HFSS 队列迁移后逻辑（mock COM/AEDT）
│   ├── test_bugfixes.py                #   33 项：历史 bug 修复回归测试
│   ├── test_extended_ops.py            #   14 项：工作区/模型库/原理图扩展工具 payload/校验
│   ├── test_schematic_library.py       #   9 项：原理图库/导出工具 payload/校验
│   └── test_health.py                  #   2 项：TCP 检查
│
├── scripts/                            # 构建与启动脚本
│   ├── build.ps1                       #   PyInstaller 打包脚本（体积检查+过滤敏感配置）
│   ├── edi_mcp_server.spec             #   PyInstaller spec（hiddenimports+excludes）
│   ├── start_local.bat                  #   本机模式启动脚本
│   ├── start_remote.bat                 #   远程模式启动脚本
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
| `test_mcp_content.py` | Resources 结构 / Prompts 参数校验 / MCP 协议 list/read/get | 20 |
| `test_tool_registry.py` | 完整注册验证 / Chat 一致性 / 破坏性工具 / 工具数动态统计 | 7 |
| `test_project_reader.py` | S-expression 解析 / 元件提取 | 5 |
| `test_component_tools.py` | 元件列表 / 类型过滤 / 分页 / 参数查询 | 4 |
| `test_turbocharts_runner.py` | 超时范围校验 | 3 |
| `test_health.py` | TCP 连接检查 | 2 |
| `test_compare_results.py` | 多 RAW 对比对齐 / 插值参考轴校验 | 2 |
| `test_task_runner.py` | 异步队列生命周期 / 结果写回 / 队列满 / 清理 | 9 |
| `test_cst.py` | 共享函数 / 静态解析 / 查询返回结构 / 导出流程 mock | 23 |
| `test_utils.py` | 文件校验 / 错误响应 / 地址管理 / 链接生成 / require_position / require_uuid | 19 |
| `test_settings.py` | 环境变量读取 / 范围限制 / 启动校验 | 9 |
| `test_ansys.py` | HFSS 队列迁移后逻辑（mock COM / AEDT） | 9 |
| `test_bugfixes.py` | 历史 bug 修复回归测试 | 33 |
| `test_extended_ops.py` | 工作区 / 模型库 / 原理图扩展工具 payload / 校验 | 14 |
| `test_schematic_library.py` | 原理图库 / 导出工具 payload / 校验 | 9 |
| `test_simulation_agent.py` | TR 集成：session/request_id/调用/错误映射/Resource/Prompt | 17 |

```powershell
uv run pytest -q                 # 全量 375 项
uv run pytest tests/ -v          # 详细输出
uv run pytest tests/test_simulation_components.py -v  # 单文件
```

---

## 打包

```powershell
uv build && uv publish           # PyPI
powershell -File scripts/build.ps1  # PyInstaller
# → dist/edi-mcp/（edi_mcp_server.exe + _internal/ + .env）
#   打包完成后自动冒烟测试（启动 exe → 健康检查 → 工具注册）
```

---

## 文档

| 文档 | 说明 |
|---|---|
| [部署指南](./docs/WIN7部署与启动手册.md) | 打包产物使用、客户端配置 |
| [工具 API](./docs/TOOLS_API.md) | 全部 91 个工具参数、返回值、示例 |
| [HTTP 接口](./docs/HTTP_API.md) | 全部 HTTP 路由的请求体、响应体、成功/失败情况 |
| [实现原理与机制](./docs/IMPLEMENTATION.md) | 通信原理、公有方法、特殊机制、工具、8 Resource + 9 Prompt |
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
- `analyze_image` 仅用户明确要求时调用，会上传到第三方

### 服务重启

- 重启后旧 MCP session 失效，客户端重新 initialize
- 仿真任务在内存中，重启后查询返回 TASK_NOT_FOUND
- 不自动重放工具调用


