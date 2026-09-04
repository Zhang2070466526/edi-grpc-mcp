# EDI gRPC MCP 项目交接文档

## 目录

- **[项目概述](#项目概述)**：项目定位、本地 MCP 模式
- **[代码仓库](#代码仓库)**：仓库地址
- **[目录结构](#目录结构)**：项目文件树
- **[技术栈](#技术栈)**：依赖库、版本
- **[MCP 工具清单](#MCP工具清单)**：全部工具分类
- **[配置说明](#配置说明)**：.env 配置
- **[启动方式](#启动方式)**：运行命令
- **[通信流程](#通信流程)**：gRPC / 本地调用
- **[并发控制](#并发控制)**：锁、信号量
- **[测试](#测试)**：测试命令
- **[打包](#打包)**：PyPI / PyInstaller
- **[工具注册机制](#工具注册机制)**：@mcp.tool() 装饰器
- **[扩展开发](#扩展开发)**：新增工具、重编译 proto
- **[关键设计决策](#关键设计决策)**：传输、异步语义、产物格式等
- **[注意事项](#注意事项)**：50+ 条避坑清单
- **[维护人](#维护人)**：负责人、版本

---

## 项目概述

将 EDA-PMDS/EDI 的 gRPC 接口和本地命令行工具封装为 MCP 服务，使 AI 客户端能通过自然语言操作 EDA 工程。

当前版本为**本地 MCP 模式**：每台电脑独立运行，服务、文件、EDI 均在本机，无需公共服务或远程 Agent。

## 代码仓库

D:\GitLabCode\edi-grpc-mcp

## 目录结构

```
proto/                  # protobuf 协议文件
servers/
  __init__.py            # 全局 FastMCP 实例 + 版本号
  registry_server.py     # 工具注册 + Web 路由注册
  utils.py                # 公共工具函数
  settings.py             # 统一配置加载
  task_runner.py          # 通用异步任务队列（EDA/HFSS/CST 复用）
  resources_prompts/       # 6 个 Resource + 8 个 Prompt
  multimodal_vision/      # 图片 + 视觉分析 + 文档工具
  report/                 # 仿真报告生成
  eda/                   # EDI 工程工具（37 个）
    __init__.py           # 公共 API + 工具清单
    config.py             # 配置 / 路径检测
    project_reader.py     # ProjectReader + S-expression 解析器
    grpc_client.py        # gRPC 通信层（FetchEvent → PerformAction）
    project_manage.py     # 工程管理（7 工具）
    simulation.py         # 仿真（7 工具）
    simulation_components.py  # 仿真器件（10 工具）
    simulation_component_catalog.json  # 参数目录 v2.0
    design_export.py      # 网表/截图（2 工具）
    signal_chain.py       # 信号链路追踪（1 工具）
    model_replace.py      # 模型替换（1 工具）
    model_search.py       # 模型库查询（3 工具）
    edi_launcher.py       # 启动 EDI + 服务诊断（3 工具）
  turbocharts/
    config.py             # run_turbocharts（信号量串行）
    convert_raw.py        # RAW 转图 + 曲线查询（2 工具）
    compare_results.py    # 仿真对比（1 工具）
  ansys/                  # ANSYS HFSS 工具（6 个）
    config.py             # 进程检测/COM 附着/锁文件
    project_manage.py     # 工程打开/关闭/启动/信息
    run_analysis.py       # 异步仿真
  cst/                    # CST 电磁仿真工具（5 个）
    __init__.py           # 公共 API re-export
    cst_api.py            # 安装检测/API 加载/会话管理
    simulate.py           # 仿真求解（异步，一次性会话）
    result_export.py      # 结果导出（S 参数 / 远场方向图）
  chat/                   # 聊天模块
    service.py            # 聊天服务（会话/LLM/工具闭环）
    routes.py             # Web 路由（/health /chat /ui /tools/list）
    index.html            # 聊天前端页面
start_servers.py         # 一键启动入口
tests/                   # 测试套件
scripts/                 # 打包脚本 + PyInstaller 配置 + 启动脚本
docs/                    # 文档（API 参考/实现原理/交接/gRPC 协议）
.mcp.json                # Claude Code 配置
.env                     # 环境变量（不提交 Git）
pyproject.toml           # uv 项目配置 + PyPI 元数据
LICENSE                  # MIT 许可证
README.md                # 项目主文档
```

## 技术栈

Python 3.12+ / uv 包管理 / FastMCP (mcp >= 1.0.0) / grpcio >= 1.81.0 / protobuf >= 7.35.0 / python-dotenv / httpx / matplotlib / numpy / psutil / pywin32

CST 电磁仿真依赖：CST 官方 Python 接口（cst.interface / cst.results，通过注册表定位，需本机安装 CST）

PyPI: https://pypi.org/project/edi-grpc-mcp/  |  当前版本：0.1.7

## MCP 工具清单

工具总数随版本变化（当前 55 个，以运行时 `/ready` 的 `tool_count` 为准），按功能分 11 类：

| 分类 | 数量 | 说明 |
|---|---|---|
| 工程管理 | 9 | 创建 / 扫描 / 打开 / 关闭工程、查询器件、分析变量 |
| 仿真器件 | 10 | 器件 Schema、增删改、状态、网表导入、原理图加载 |
| 仿真 | 8 | 同步 / 异步仿真、网表仿真、抗烧毁评估、任务查询 |
| 导出分析 | 3 | 导出网表、截图原理图、信号链路追踪 |
| 模型 / 启动 / 诊断 | 7 | 模型替换、启动 EDI、服务诊断、日志读取 |
| ANSYS HFSS | 6 | AEDT 工程开关、HFSS 异步仿真 |
| CST 电磁仿真 | 5 | 异步求解 .cst、导出 S 参数 / 远场方向图 |
| 图表 | 3 | RAW 曲线、转图、结果对比 |
| 图片 | 2 | 显示、视觉分析 |
| 文档 | 1 | 打开本地文档 |
| 报告 | 1 | 生成仿真报告 |

> 完整工具签名、参数、返回格式见 [TOOLS_API.md](./TOOLS_API.md)，工具一览表见 [README.md](../README.md)。

## 配置说明

每台电脑独立配置 `.env`，所有服务均为本机调用：

```
EDA_GRPC_SERVER=127.0.0.1:50055
EDI_PATH=C:\Program Files (x86)\EDI\EDI.exe    # 留空自动检测
TURBOCHARTS_PATH=C:\Program Files (x86)\EDI\turbocharts_app.exe  # 留空自动检测
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=50026
MCP_API_KEY=                              # 可选：留空不鉴权；配置后 /mcp /ui /chat 等要求 ?token= 匹配
```

## 启动方式

```powershell
cd D:\GitLabCode\edi-grpc-mcp
uv run python start_servers.py                        # HTTP 模式（推荐）
uv run python start_servers.py --transport stdio       # stdio 模式
```

## 通信流程

gRPC 工具：AI 客户端 -> MCP 工具 -> grpc_client.call_grpc() -> PerformAction + FetchEvent -> 返回结果

本地工具：AI 客户端 -> MCP 工具 -> subprocess -> 返回结果

## 并发控制

- EDA gRPC 操作：全局 RLock，最大并发 1
- 文件读取：允许并发（最多 4）
- Turbocharts：BoundedSemaphore(1)
- CST 求解：TaskRunner 单 worker 串行（异步任务队列）
- 单实例控制：启动时检查端口，被占用时自动结束占用进程再启动

## 测试

```powershell
uv run python tests/test_project_reader.py
uv run python tests/test_component_tools.py
uv run python tests/test_turbocharts_runner.py
uv run python tests/test_health.py
uv run python tests/test_tool_registry.py
```

## 打包

### PyPI（源码包）

```powershell
uv build
uv publish
```

首次使用需创建 `.pypirc` 配置 PyPI 令牌（已加入 `.gitignore`）。

发布后用户通过 `pip install edi-grpc-mcp` 安装，通过 `edi-grpc-mcp` 命令启动。

### PyInstaller（免安装二进制包）

```powershell
powershell -File scripts/build.ps1
# 输出: dist/edi-mcp/（含 edi_mcp_server.exe + start_server.bat + .env，约 90 MB）
# 打包完成后自动跑 scripts/smoke_test_exe.py 冒烟测试（启动 exe → 健康检查 → 鉴权 → 工具注册），不通过则打包失败
```

目录型打包，复制 `dist/edi-mcp/` 到目标电脑后创建 `.env` 即可运行。
```

## 工具注册机制

所有工具函数通过 `@mcp.tool()` 装饰器定义在 `servers/` 各子包中（eda/、ansys/、turbocharts/、multimodal_vision/、report/），由 `servers/registry_server.py` 统一导入触发注册。各子包的 `__init__.py` 作为公共 API re-export 所有工具函数。

```
project_manage.py  ->  eda/__init__.py  ->  registry_server.py  ->  start_servers.py
  (定义 @mcp.tool())     (re-export)           (import 触发注册)        (解析参数, 启动)
```

## 扩展开发

添加 gRPC 工具：在 servers/eda/ 对应文件中添加 @mcp.tool() 装饰器 -> registry_server.py import 模块

重新生成 proto：
```powershell
python -m grpc_tools.protoc -I proto --python_out=proto --grpc_python_out=proto proto/ecserver.proto
# 编辑 proto/ecserver_pb2_grpc.py：import ecserver_pb2 -> from proto import ecserver_pb2
```

## 关键设计决策

### 传输方式
- Streamable HTTP + stateless 模式（默认），端点 `/mcp`
- stdio 模式用于 Claude Code 等桌面客户端
- SSE 已删除，不支持

### 异步仿真语义
- `outcome_known`：是否收到 EDI 最终事件（SUCCEEDED/FAILED 时为 True）
- `task_success`：仅 outcome_known=True 有意义；None = 结果未知
- TIMEOUT/STREAM_DISCONNECTED：outcome_known=False, task_success=None
- MCP 自身异常：failure_source="mcp"

### 产物格式
- 所有文件生成工具返回统一的 `artifacts` 数组
- MCP 负责生成，客户端负责显示——message 用"已生成"不说"已显示"
- 报告自动返回 HTTP 预览链接（10 分钟有效）

### 配置管理
- 所有环境变量收敛到 `servers/settings.py` → Settings dataclass (frozen)
- 启动时 validate() 校验 gRPC 地址格式、端口范围、传输方式
- EDI_PATH / TURBOCHARTS_PATH / OPENCLAW_WORKSPACE 留空自动检测

### 访问控制
- 配置 `MCP_API_KEY` 后，`/mcp` `/ui` `/chat` `/tools/list` `/upload` 端点要求 URL 带 `?token=<key>` 匹配才放行，其余返回 401
- 留空则不鉴权（向后兼容）；用于「只允许指定 agent 访问」的场景
- 实现：`start_servers.py` 的 `_TokenAuthMiddleware` 中间件，绕开 `mcp.run()` 手动构建 Starlette app 后 `add_middleware`
- 启动日志会打印 token 值（`Auth: enabled (?token=xxx required on /mcp)`），便于复制；Chat 前端从 `/ui?token=` 的 URL 提取 token，页面内 fetch 自动带上

### Chat 与工具注册
- Chat 工具列表从 MCP 元数据自动生成，排除同步阻塞和 COM 依赖工具
- 破坏性操作需用户确认（支持肯定词），5 分钟过期
- 重启后旧 session 返回 Session not found，不伪造
- Chat 前端 `/ui`：侧栏工具分类 + 搜索 + 折叠、消息气泡 + 时间戳、EDI 在线状态圆点、输入区全宽对齐
- Chat 前端 session 连续性：后端在 session 失效时自动创建新 session 并返回新 id，前端同步更新，保证多轮对话上下文连续

---

## 注意事项

### 环境与兼容性
1. proto 目录名为 `proto`，避免与 grpcio 包名冲突
2. stdio 模式禁止向 stdout 输出任何内容
3. `.env` 通过 python-dotenv 加载，load_dotenv() 默认不覆盖系统环境变量
4. Windows 路径反斜杠在 `.env` 中不需要转义
5. Windows HTTP 使用 SelectorEventLoop 避免 WinError 64
6. PyPI 包仅含 Python 源码，不含 PyInstaller 二进制；Windows 专属（pywin32/COM）

### 超时与并发
7. gRPC 超时上限：open/close 300s, simulate 3600s, turbocharts 600s
8. EDA gRPC 操作全局 RLock 串行化，所有调用排队等待
9. 异步仿真 ThreadPoolExecutor(max_workers=1)，最多 8 个排队任务
10. Turbocharts BoundedSemaphore(1) 串行执行
11. ANSYS HFSS 单 worker 队列，最多 10 个，2h TTL 清理

### gRPC 通信
12. FetchEvent 必须在 PerformAction 之前建立，否则 EDI 返回 "external handler not ready"
13. task_id 和 client_uuid 由 MCP 侧生成，贯穿 FetchEvent → PerformAction → 状态/结果
14. ads_output 通过 FetchEvent 增量推送，原样追加不 strip，最终事件片段同样追加
15. PerformAction 回显需三重校验：client_uuid / task_id / event_type 全部匹配
16. gRPC 服务地址格式必须为 `host:port`

### ANSYS / COM
17. COM 附着支持多 ProgID 回退（AnsoftHfss.HfssScriptInterface / Ansoft.ElectronicsDesktop）
18. AEDT 锁文件管理：打开前检查/清理失效锁，PID 活跃时绝不删除，关闭后安全移除
19. AEDT 路径自动检测：环境变量 → 注册表 → 默认目录（按版本号取最新）

### 打包与构建
20. EXE 使用 exclude_binaries=True，DLL/Pyd 放 _internal/，不可单独分发
21. UPX 压缩已关闭（原生 DLL 兼容性）
22. 构建脚本体积阈值：目录 > 105MB 或 ZIP > 80MB 失败
23. matplotlib.use("Agg") 必须在 import matplotlib.pyplot 之前
24. Pillow AVIF/WebP 编码器已通过 .spec excludes 排除
25. 打包时自动过滤敏感配置（LLM_API_KEY 等），强制 MCP_TRANSPORT=streamable-http
    · excludes 不能排除 `unittest`：matplotlib 依赖的 pyparsing.testing 会 `import unittest`，排除后 exe 启动报 `ModuleNotFoundError`

### 重启与恢复
26. 重启后旧 MCP session 失效，客户端需重新 initialize
27. 仿真任务在内存中，重启后查询返回 TASK_NOT_FOUND
28. 不自动生成文件、不自动修改工程、不自动重跑仿真
29. `/ready` 端点初始化中返回 503，就绪后返回 200
30. 生命周期日志：MCP_STARTING → MCP_READY → MCP_STOPPING → MCP_STOPPED

### 图片与文档
31. show_image 始终返回 ImageContent，不依赖工作区
32. copy_image_to_workspace 已暂时隐藏（不使用 OpenClaw），不检测工作区；恢复时改回 `OPENCLAW_WORKSPACE_PATH = _get_openclaw_workspace()`
33. analyze_image 仅用户明确要求时调用，会上传到第三方
34. open_document 生成 10 分钟 HTTP token，仅本机 127.0.0.1 可访问

### 工具注册
35. 新增 MCP 工具自动注册到 Chat（除非加入 _CHAT_EXCLUDED_TOOLS）
36. `start_servers.py` 使用私有属性 _tool_manager，MCP 版本升级后需验证
37. 工具数量由启动时动态统计，不在文档中写死

### 新增接口
38. `attach_out_component` (ATTACH_OUT_COMPONENT=17)：为器件引脚挂载 Out 器件并自动连线
39. proto 重编译后需手动修复 `ecserver_pb2_grpc.py` 的 import 路径（`ecserver_pb2` → `from proto import ecserver_pb2`）

### 日志系统
40. 所有模块使用 `logging.getLogger(__name__)` 统一日志，输出到 `%TEMP%/edi/data/log/`
41. Chat 记录 request_id + 工具调用参数（脱敏），turbocharts 记录命令行和耗时
42. report 记录文件类型/模型名/图表数/器件数，vision 记录 HTTP 状态和响应体
43. gRPC 记录 SUBSCRIBING → ACCEPTED → SUCCEEDED/FAILED/TIMEOUT 完整阶段
44. 启动记录 MCP_STARTING → MCP_READY → MCP_STOPPING → MCP_STOPPED 生命周期

### Chat 增强
45. 支持 📎 文件上传 / 粘贴图片 / 拖拽文件，自动上传到 `%TEMP%/mcp/uploads/`
46. 图片上传后自动提示 LLM 调用 `analyze_image`，文档提示 `open_document`
47. session 失效自动创建新会话，对用户透明

### turbocharts
48. 多条 VSWR+CSV 自动拆分为多次调用，每次一条 VSWR，无需手动分次

### analyze_image 修复
49. `_encode()` 返回值 bug：成功时 MIME 字符串被误当作 error 返回
50. URL 拼接双 `/v1` 修复、DashScope Omni 缺少 `modalities: ["text"]` 修复
51. 默认 prompt 加强，content 数组兼容，短内容（<5 字符）拒绝

### CST 电磁仿真
52. CST 官方 Python 接口通过注册表定位（CST DESIGN ENVIRONMENT_AMD64.exe），需本机安装 CST
53. 求解与远场导出采用一次性会话（连接 → run_solver → 保存/导远场 → 关闭），不保持会话复用；导出 S 参数无会话直接读结果
54. CST 求解/导出逻辑原在参考脚本 `cst_libraries.py` 中，现已全部抽取到 `simulate.py`（求解）与 `result_export.py`（S 参数 + 远场方向图），参考脚本已删除
55. CST/HFSS 异步任务复用通用 `TaskRunner`（`servers/task_runner.py`）；EDA 仿真保留自有注册表（增量日志推送 + gRPC 回调 + ACCEPTED 状态是 TaskRunner 无法优雅承载的）
56. CST 求解/导出统一走 `cst_runner` 串行队列（含 `cst_export_snp` 经 `run_sync` 同步执行），避免无会话读结果与求解会话并发

## 维护人

- 负责人：--
- 更新时间：2026-09-03
- 当前版本：0.1.7


