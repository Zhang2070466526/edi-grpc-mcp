# EDI gRPC MCP 实现原理与机制

> 本文档是这个项目**所有机制**的权威说明：从底层通信原理、公有设置与公有方法、特殊机制（远程 / 进程白名单 / 产物传输），到 91 个工具的逐类实现，再到 8 个 Resource 与 9 个 Prompt。要改代码 / 排查问题 / 理解某个设计决策，都从这里找。

> 相关文档：[TOOLS_API.md](./TOOLS_API.md)（91 个工具接口参数/返回）、[HTTP_API.md](./HTTP_API.md)（HTTP 路由请求/响应体）。

---

## 目录

**按「想干什么」快速定位**：

| 想干什么 | 看这里 |
|---|---|
| 理解某个工具走什么协议、怎么实现 | [四、工具实现](#四工具实现) + [一、底层通信原理](#一底层通信原理) |
| 排查远程连不上 / 421 / 产物打不开 | [三、特殊机制](#三特殊机制)（远程 / 白名单 / 产物传输） |
| 查配置项 / 错误码 / 返回字段 / 校验管线 | [二、公有设置与公有方法](#二公有设置与公有方法) |
| 查 Resource / Prompt | [五、Resources](#五resources8-个) / [六、Prompts](#六prompts9-个) |

**章节明细**：

- **[一、底层通信原理](#一底层通信原理)** —— 7 种通信类型 + gRPC / 任务 / 本地 / subprocess / COM 的机制
  - `1.1` 通信类型总览
  - `1.2` gRPC 异步调用模型
  - `1.3` 异步任务模型与队列
  - `1.4` 本地文件读取
  - `1.5` subprocess 命令行
  - `1.6` COM 与 CST 接口

- **[二、公有设置与公有方法](#二公有设置与公有方法)** —— Settings 配置、utils 公有方法、错误码、返回语义、参数校验
  - `2.1` Settings 配置
  - `2.2` utils 公有方法
  - `2.3` 错误码体系
  - `2.4` 返回字段语义
  - `2.5` 产物统一格式
  - `2.6` 参数校验分层与参数目录

- **[三、特殊机制](#三特殊机制)** —— 远程访问、进程白名单、产物传输、并发、重启、日志、Chat 确认
  - `3.1` 远程访问
  - `3.2` 进程白名单
  - `3.3` 产物传输
  - `3.4` 并发与排队
  - `3.5` 重启恢复与生命周期
  - `3.6` 日志系统
  - `3.7` Chat 会话与破坏性确认

- **[四、工具实现](#四工具实现)** —— 91 个工具按通信类型分组 + 设计动机
  - `4.1` 工程管理
  - `4.2` 仿真器件
  - `4.3` 仿真
  - `4.4` 导出与模型 / 软IP
  - `4.5` 工作区 / 原理图扩展
  - `4.6` 图表
  - `4.7` ANSYS HFSS 与 CST
  - `4.8` 启动 / 诊断
  - `4.9` 图片 / 文档 / 报告
  - `4.10` TR 仿真集成
  - `4.11` 工具设计动机与数据依赖

- **[五、Resources（8 个）](#五resources8-个)** —— 只读上下文/参考资料逐条详解
  - `edi://service/overview` — 服务概览
  - `edi://service/status` — 实时运行状态
  - `edi://projects` — 工作区工程目录
  - `edi://reference/simulation-components` — 仿真器件参数参考
  - `edi://reference/operation-guide` — 操作安全约束
  - `edi://reference/error-codes` — 错误码词典
  - `edi://reference/turbocharts-guide` — RAW 转图说明
  - `edi://integration/workflow` — TR 仿真工作流规则

- **[六、Prompts（9 个）](#六prompts9-个)** —— 可复用工作流模板逐条详解
  - `inspect_edi_project` — 工程只读检查
  - `run_and_review_simulation` — 仿真执行 + 日志分析
  - `configure_simulation_component` — 配置仿真器件
  - `create_simulation_report` — 生成仿真报告
  - `troubleshoot_edi_error` — 诊断调用错误
  - `assess_anti_burnout` — 抗烧毁评估
  - `select_component` — 器件选型
  - `analyze_signal_chain` — 信号链分析
  - `run_tr_simulation` — TR 仿真工作流

---

## 一、底层通信原理

### 1.1 通信类型总览

每个 MCP 工具按底层通信方式分为 7 种实现类型：

| 类型 | 工具数 | 核心实现 | 说明 |
|---|---|---|---|
| gRPC 远程调用 | 43 | `servers/eda/grpc_client.py` | 操作 EDI 工程/器件，统一走 `call_grpc()` |
| 本地文件读取 | 7 | `servers/eda/project_reader.py` | 直接读 `.epp` 工程磁盘文件 / EDI 日志，不经 gRPC |
| 内存服务 | 4 | `simulation.py` / `grpc_client.py` | 读运行时状态（任务注册表、通道状态） |
| subprocess 命令行 | 4 | `turbocharts/config.py` 等 | 调 `turbocharts_app.exe`、启动 EDI |
| COM / CST 接口 | 11 | `ansys/config.py` / `cst/cst_api.py` | ANSYS 6 个走 COM，CST 5 个走官方 API |
| 图片 / 文档 / 报告 | 4 | `multimodal_vision/` / `report/` | 视觉分析、文档预览、报告渲染 |
| TR HTTP 转发 | 17 | `tr_simulation/client.py` | 转发给 SimulationAgent（外部 HTTP 服务，会话复用 + 幂等 request_id） |

### 1.2 gRPC 异步调用模型

EDI 服务通过 `proto/ecserver.proto` 定义 `ExternalCall` 服务（协议版本 v2，45 种事件类型）：

```proto
service ExternalCall {
  rpc PerformAction(Request) returns (Response) {}          // 提交任务
  rpc FetchEvent(FetchEventRequest) returns (stream Event) {} // 流式拉取结果
}
```

核心设计是 **异步任务模型**：`PerformAction` 只负责受理任务（返回 `code=0` 表示已受理），最终结果通过 `FetchEvent` 的流式通道异步推送。

#### 统一入口 call_grpc()

所有 gRPC 工具都调用同一个函数，唯一区别是 `task_type` 枚举值和 `payload` 字典：

```python
def call_grpc(
    task_type: int,        # ecserver_pb2 枚举值（如 OPEN_PROJECT=1）
    payload: dict,         # 任务参数（project_path, instance_name 等）
    timeout_seconds: int,  # 总超时秒数
    max_timeout_seconds: int = 3600,
    task_id: str | None = None,
    client_uuid: str | None = None,
    on_event: Callable | None = None,  # 异步任务的增量回调
) -> dict:
```

#### 串行锁

EDA 操作必须串行化——同时打开工程又执行仿真会导致状态冲突。`_EDA_LOCK = threading.RLock()` 是有超时的可重入锁：

```python
acquired = _EDA_LOCK.acquire(timeout=timeout_seconds)
if not acquired:
    return _terminal_result(success=False, status="QUEUE_TIMEOUT", ...)
try:
    return _call_grpc_unlocked(...)
finally:
    _EDA_LOCK.release()
```

#### 调用顺序：先订阅后提交

EDI 服务要求 FetchEvent 必须在 PerformAction 之前建立，否则返回 "external handler not ready"。MCP 侧严格遵循：

```
1. stub.FetchEvent(client_uuid) → 建立流式订阅
2. stub.PerformAction(request)  → 提交任务
3. 消费事件流                   → 等待终态
```

第 1、2 步都受剩余的 `timeout_seconds` 约束。

#### 响应校验（三重回显）

`PerformAction` 返回后，MCP 对回显进行三重校验：

```python
if response.code != 0:
    return REJECTED       # EDI 未受理
if response.client_uuid != client_uuid:
    return PROTOCOL_MISMATCH
if response.task_id != task_id:
    return PROTOCOL_MISMATCH
if response.event_type not in (EVENT_TYPE_UNSPECIFIED, task_type):
    return PROTOCOL_MISMATCH
```

#### 消费事件流

三重筛选（`client_uuid + task_id + event_type` 全部匹配才处理），增量收集 `ads_output` 日志（不 strip、不覆写），累积 `latest_details`（后到覆盖同名字段），触发 `on_event` 回调：

```python
for event in event_stream:
    if event.client_uuid != client_uuid: continue
    if event.task_id != task_id: continue
    if event.event_type != task_type: continue

    details, parse_error = _parse_payload_json(event.payload_json)
    chunk = details.get("ads_output", "")
    if chunk:
        ads_output_chunks.append(chunk)   # 原样追加
    for key, value in details.items():
        if key != "ads_output":
            latest_details[key] = value

    if event.status == RESULT_STATUS_SUCCESS:
        if parse_error:
            return PROTOCOL_MISMATCH   # SUCCESS 的 JSON 必须可解析
        return SUCCESS
    if event.status == RESULT_STATUS_FAILED:
        return FAILED

return STREAM_DISCONNECTED   # 流结束但无终态
```

#### 异常处理

| gRPC 错误码 | MCP 返回 |
|---|---|
| `DEADLINE_EXCEEDED` | `status: "TIMEOUT"`，保留已收日志 |
| 流建立后断连 | `status: "STREAM_DISCONNECTED"`，保留已收日志 |
| 流建立前断连 | `status: "GRPC_UNAVAILABLE"`，message 提示「手动启动 EDI 软件后重试」 |
| `RESOURCE_EXHAUSTED` | `status: "PAYLOAD_TOO_LARGE"`（消息 >256MB） |

#### 返回结构

```python
{
    "success": bool,         # 是否成功
    "completed": bool,       # 是否已到终态
    "status": str,           # 终态标识
    "message": str,          # 描述
    "project_path": str,     # 工程路径
    "result_path": str,      # 结果路径（如 RAW 文件）
    "ads_output": str,       # 增量拼接的完整仿真器日志
    "log_complete": bool,    # 日志是否完整接收
    "details": dict,         # 原始 payload 字段
}
```

### 1.3 异步任务模型与队列

#### 通用 TaskRunner

EDA / HFSS / CST 三类长任务复用同一个通用队列 `servers/task_runner.py`（单 worker 线程池 + 内部锁），语义统一：

- 提交返回 `task_id`；队列满 → 有界拒绝（`QUEUE_FULL` / `TASK_LIMIT_REACHED`）
- 单实例工具策略按软件分：**HFSS 用 `require_idle=True`（忙就拒，不排队）**；**CST 不用 `require_idle`（忙则排队）**
- 容量按条目数判满（`len(_tasks) >= max_tasks`，含已完成未过期的）
- 完成后保留 `ttl_seconds`（默认 2h）供查询，过期清理；`max_run_seconds` 兜底防卡死

#### 三套队列

| 队列 | 实现 | 容量 | 忙时行为 | 状态机 |
|---|---|---|---|---|
| EDA 仿真 | `simulation.py` `_sim_tasks` + `_SIM_EXECUTOR(1)` | 在途上限 8（`>=8` → `SIMULATION_QUEUE_FULL`） | 排队（FIFO） | QUEUED→ACCEPTED→RUNNING→SUCCEEDED/FAILED |
| HFSS | `task_runner.py` `hfss_runner` | `max_tasks=50` | **拒绝**（`ANALYSIS_BUSY`） | QUEUED→RUNNING→SUCCEEDED/FAILED |
| CST | `cst_runner` | `max_tasks=50` | **排队** | QUEUED→RUNNING→SUCCEEDED/FAILED |

> 三种工具三种策略，对应各自软件约束：AEDT 是单实例桌面（只能跑一个求解 → 忙就拒）；CST 批量导出可排队；EDA 有自己的队列上限。统一到「返回语义 + 错误码」即可，不强行合并实现。

#### EDA 异步仿真

```
start_simulation_async()
  ├─ 创建 task 记录（task_id, client_uuid, status=QUEUED）
  ├─ 检查队列上限（最多 8 个，原子检查+插入）
  ├─ submit 到 ThreadPoolExecutor(max_workers=1)
  └─ 立即返回 task_id

后台线程 _run_sim_task():
  ├─ call_grpc(SIMULATE_PROJECT, on_event=_handle_sim_event)
  ├─ on_event 回调更新 _sim_tasks[task_id] 的 status/log_chunks/result_path
  └─ 最终: task["result"] = 完整结果, task["finished_at"] = 时间戳
```

**线程安全**：`_sim_lock`（`threading.Lock`）保护 `_sim_tasks` 字典；`_get_task_snapshot()` 在锁内创建浅拷贝，写入方改原字典、读取方看快照，避免并发修改。

### 1.4 本地文件读取

这些工具不经过 gRPC，直接读磁盘上的 `.epp` 工程文件。核心实现在 `servers/eda/project_reader.py`。

#### .epp 工程格式

`.epp` 不是单个文件，而是一个目录：

```
ProjectName/
  ProjectName.epp          ← 标记文件（内容固定为 "EDI-PROJECT"）
  project/
    metadata.ep             ← S-expression 格式元数据
  schematics/
    schematics.ep           ← 原理图列表
    main/
      schematic.ep          ← main 原理图（S-expression 格式）
  netlist.log               ← ADS 网表
  history/
    result.raw              ← 仿真结果
```

#### S-expression 解析器

`parse_sexp(text)` 是递归下降解析器，处理 EDI 使用的 Lisp 风格格式：

```
(block
  (source "EDI")
  (component uuid-001
    (type "ResG")
    (name "R1")
    (component_uuid "model-123")
    (pin 1)
    (pin 2)
    (paramsinfo "{\"R\":{\"Value\":\"50\",\"CurrentUnit\":\"Ohm\"}}")
  )
)
```

解析器状态机：跳过空白 → `'('` 递归解析子表达式 → `'"'` 读引号字符串（处理 `\"`/`\n`/`\t` 转义）→ 其他读裸词。

**关键安全措施**：`ProjectReader.read_schematic(name)` 拒绝 `..` 和路径分隔符：

```python
if ".." in name or "/" in name or "\\" in name:
    return None
```

#### paramsinfo 解析

原理图里的 `(paramsinfo "...")` 节点包含 JSON 格式参数信息，两种结构：

- **普通参数**：`{"Value": "50", "CurrentUnit": "Ohm", "Tunable": "false"}`
- **Var 变量**：`{"Initial": "29", "Max": "", "Min": "", "Status": "Disable"}`

`parse_paramsinfo(raw)` 统一为小写 key 字典；`parse_components()` 提取所有 `(component ...)` 节点并解析参数，返回值直接含已解析的 `paramsinfo`——下游不应再次调用 `parse_paramsinfo`。

#### 本地读取工具

| 工具 | 读取内容 | 特点 |
|---|---|---|
| `list_epp_projects` | 文件夹扫描 | `rglob("*.epp")`，最多 1000 个，返回名称/路径/大小 |
| `get_project_summary` | metadata + schematics + netlist + RAW | 聚合元数据、原理图列表、元件类型分布、仿真器件配置、最新 RAW |
| `analyze_variables` | 所有原理图 | 识别 Var 定义 → 找引用 → 列 Sweep 配置 |
| `list_simulation_components` | 所有原理图 | 支持类型/名称模糊/原理图过滤 + 分页；SP/HB/XDB 做 wire→public 映射 |

### 1.5 subprocess 命令行

#### TurboCharts

`turbocharts_app.exe` 是 EDI 套件的命令行工具，把 ADS RAW 转成曲线图和 CSV。MCP 通过 `subprocess.run` 调用，串行信号量保护（一次只跑一个实例）：

```python
_TURBOCHARTS_SEMAPHORE = threading.BoundedSemaphore(1)

def run_turbocharts(command, timeout_seconds=120):
    with _TURBOCHARTS_SEMAPHORE:
        return subprocess.run(list(command), capture_output=True, text=True,
                              timeout=timeout_seconds, creationflags=CREATE_NO_WINDOW)
```

命令行构造（`turbocharts_convert`）：

```python
cmd = [TURBOCHARTS_PATH, "--raw", raw_path, "--img", output_path, "--type", chart_type]
if csv_path:   cmd.extend(["--csv", csv_path])
if linename:   cmd.extend(["--linename", linename])
if dependency: cmd.extend(["--dependcy", dependency])  # 程序参数名就是 --dependcy（少一个 n）
if ac_config:  cmd.extend(["--ac", ac_config])
```

关键点：`--dependcy` 是真实参数名（非拼写错误）；MCP 校验 `output_path` 扩展名（PNG/JPG/BMP/SVG）；`timeout_seconds` 范围 1-600。

#### RAW 曲线查询（list_result_curves）

解析 ADS RAW 文件头，支持 MDS 和 XML 两种格式。MDS 解析状态机逐行读：`Plotname:` 提取 dependencies → `Variables:` 进入变量模式 → `Values:` 退出 → 变量行解析 index/name/type/indep。**截断保护**：只读前 65536 字节，达上限返回 `warning`。

曲线推荐规则（`_suggest_curves`）：`S.delay[x,y]` 最高优先级；complex S[n,n] 反射参数加 VSWR，传输参数不加。

#### 仿真结果对比（compare_simulation_results）

对比 2-8 个 RAW 同一条曲线：逐 RAW 导出临时 CSV → 读取 x/y → 对齐（`intersection` 取交集 / `interpolation` 以 reference_index 插值）→ 计算差异指标 → Matplotlib 叠图 → 可选导出 CSV。

#### EDI 启动（launch_edi）

TCP 检查 gRPC 端口是否就绪 → 已运行跳过 → `subprocess.Popen([EDI.exe], cwd=EDI目录)` → 轮询 TCP 等待就绪（默认 30 秒）。

### 1.6 COM 与 CST 接口

#### ANSYS HFSS（COM 附着）

核心在 `servers/ansys/config.py`。通过 Windows COM 附着到已运行的 AEDT 或启动新实例：

```python
_COM_PROGIDS = ("AnsoftHfss.HfssScriptInterface", "Ansoft.ElectronicsDesktop")

def _attach_aedt():
    for progid in _COM_PROGIDS:
        try:
            app = GetActiveObject(progid)
            return app, app.GetAppDesktop()
        except Exception:
            continue
    raise RuntimeError("GetActiveObject failed")
```

每个 COM 调用在独立 `pythoncom.CoInitialize()` / `CoUninitialize()` 上下文执行。`_find_aedt()` 搜索路径：环境变量 `AEDT_PATH` → 注册表 → 默认目录（`C:\Program Files\AnsysEM\` 按版本排序取最新）。

**锁文件管理**：AEDT 打开工程创建 `.aedt.lock`（内有一行 `DesktopProcessID=<pid>`）。清理策略：PID 存活 → 拒绝删除；PID 不在进程列表 → 安全删除残留锁；无法解析 → 保留。

#### CST 官方 Python 接口

CST 工具不走 ANSYS COM，用 CST 官方接口（`servers/cst/cst_api.py`，注册表定位安装路径）：

| 模块 | 性质 | 用途 |
|---|---|---|
| `cst.interface` | COM 自动化（连接/启动 CST） | `connect_to_any_or_new()`、`run_solver`、`execute_vba_code` 导远场 |
| `cst.results` | 只读结果 API（无会话） | `ProjectFile(...).get_3d()` 读结果树、判断已求解、读 S 参数 |

求解与远场导出共用全局串行队列 `cst_runner`（CST 一次只能跑一个会话）。

---

## 二、公有设置与公有方法

### 2.1 配置统一（Settings）

所有环境变量收敛到 `servers/settings.py` → `Settings` dataclass（`frozen=True` + `@lru_cache` 单例）。其他模块通过 `get_settings()` 读取，不再直接 `os.getenv()`。字段名小写下划线，大小写不敏感匹配环境变量（`eda_grpc_server` ↔ `EDA_GRPC_SERVER`）。

启动时 `start_servers.py` 调用 `settings.validate()` 做业务级格式校验（不阻断启动，仅打印警告）：gRPC 地址 host:port 格式、端口范围、传输方式合法性、监听地址格式、SimulationAgent URL 前缀。

**完整字段清单**：

| 字段 | 默认 | 作用 |
|---|---|---|
| `eda_grpc_server` | `127.0.0.1:50055` | EDI gRPC 地址 |
| `mcp_port` | `50026` | HTTP 服务端口 |
| `mcp_transport` | `streamable-http` | `streamable-http` / `stdio` |
| `mcp_stateless_http` | `True` | 无状态 HTTP（多客户端） |
| `mcp_allowed_processes` | 空 | 进程白名单（逗号分隔，留空禁用） |
| `mcp_bind_host` | `127.0.0.1` | 监听地址（`0.0.0.0` = 远程） |
| `mcp_extra_allowed_hosts` | 空 | 手工补 allowed_hosts（看到 421 才需配） |
| `mcp_probe_enabled` | `True` | 进程探针中间件开关 |
| `edi_path` / `turbocharts_path` / `aedt_path` | 空 | 空 = 自动检测 |
| `edi_log_dir` | `C:\Program Files (x86)\EDI\logs` | EDI 日志目录 |
| `llm_api_key` / `llm_base_url` / `llm_model` | 空 | Chat 的 LLM 配置 |
| `vision_*` | 空 | 视觉分析（独立于 Chat） |
| `report_render_url` | `http://127.0.0.1:17867/api/v1/reports/render` | 报告渲染服务 |
| `simulation_agent_url` | `http://127.0.0.1:17866` | TR 仿真代理（HTTP 转发） |

### 2.2 公有方法（utils.py）

跨模块复用的校验/响应/地址工具，都在 `servers/utils.py`：

| 函数 | 作用 |
|---|---|
| `validate_file(path, extensions)` | 文件存在 + 扩展名校验，返回错误信息或空串 |
| `require_file(path, extensions)` | 同上，返回 `(path, err)` 二元组供工具入口直接 return |
| `require_nonempty(value, error_code)` | 非空字符串校验，返回 `(value, err)` |
| `require_position(position)` | 坐标 `{x, y}` 有限数值校验 |
| `require_uuid(value)` | UUID 格式校验（`Uuid::isValid`） |
| `is_network_path(path)` / `tcp_port_open(host, port)` | 网络路径检测 / TCP 端口探测 |
| `decode_local_text(raw)` | 本地文本解码：**BOM → cp936 优先 → utf-8 兜底**（GBK 源不静默错字） |
| `error_response(code, message, retryable)` | 统一错误信封（`success:false` + `error_code` + `hint`） |
| `submitted_response(task_id, status)` | 异步任务提交响应（`success:true` + `task_id` + `hint:poll`） |
| `queue_full_response(code, retryable)` | 队列满响应（`hint:retry_safe`） |
| `build_artifact(type_, path, generated_by)` | 产物统一格式（见 2.5） |
| `build_file_link(path, label)` | `file://` + Markdown 链接（本机产物） |
| `get_server_base_url()` / `set_server_address()` | 运行时地址（Host 推导，见 3.1） |
| `local_host_names()` / `build_transport_security()` | 远程 Host 枚举 + 传输安全（见 3.1） |
| `registered_tools(mcp)` | 读已注册工具列表 |
| `server_uptime_seconds()` | 服务运行时长 |

### 2.3 错误码体系

两层语义：

| 层级 | 字段 | 来源 | 示例 |
|---|---|---|---|
| MCP 本地校验 | `error_code` | `error_response()` / `_param_error()` | `INVALID_PARAMETERS`、`COMPONENT_NOT_FOUND`、`CLEAR_CONFIRMATION_REQUIRED` |
| gRPC 通信层 | `status` | `_terminal_result()` | `SUCCEEDED`、`TIMEOUT`、`GRPC_UNAVAILABLE`、`PROTOCOL_MISMATCH` |

`error_code` 表示**已知错误类型**（AI 可据此修正参数重试）；`status` 表示**通信终态**（指示是否需人工介入）。

`hint` 是机器可读动作指令（agent 优先读它，不做布尔推断）：

| status | hint | 含义 |
|---|---|---|
| SUCCEEDED | `ok` | 成功 |
| FAILED | `task_failed` | EDI 业务失败（工具没坏） |
| TIMEOUT / STREAM_DISCONNECTED | `outcome_unknown` | 结果未知，别当失败、别自动重试非幂等 |
| GRPC_UNAVAILABLE | `service_unavailable` | EDI 没起，先 `launch_edi` 再试 |
| QUEUE_TIMEOUT | `busy` | 排队超时，稍后重试 |

### 2.4 返回字段语义 — outcome_known / task_success

gRPC 层通过 `_terminal_result()` 统一构建返回，关键字段：

```
completed       MCP 侧任务是否结束（线程退出、超时、断连都是 completed=True）
outcome_known   是否收到 EDI 最终事件（SUCCEEDED 或 FAILED）
task_success    仅 outcome_known=True 时有意义；None = EDI 实际状态未知
hint            由 status 派生的动作指令
```

语义矩阵：

| 状态 | success | outcome_known | task_success |
|---|---|---|---|
| SUCCEEDED | True | True | True |
| FAILED（EDI） | False | True | False |
| REJECTED | False | True | False |
| TIMEOUT / STREAM_DISCONNECTED / GRPC_UNAVAILABLE | False | False | None |

`_run_sim_task()` 不覆盖 gRPC 层已算的 `task_success`，只在字段缺失时从 `outcome_known` 和 `status` 推导。

### 2.5 产物统一格式（artifacts）

所有文件生成工具返回统一的 `artifacts` 数组：

```json
{
  "artifacts": [
    {"type": "image", "path": "C:/.../sp.png", "name": "sp.png", "generated_by": "turbocharts_convert"}
  ],
  "message": "曲线图已生成。"
}
```

适用工具：`turbocharts_convert`、`capture_schematic`、`compare_simulation_results`、`generate_simulation_report`。`message` 用「已生成」不用「已显示」——MCP 不保证客户端渲染成功。

### 2.6 参数校验分层与参数目录

#### 五层校验

AI 的请求经过 5 层校验才能到 EDI 服务端，每层拦截不同类别的错误：

```
Chat _validate()           空字符串拒绝、project_path/task_id 自动补齐
  ↓
工具函数入口              路径校验（require_file + resolve）
  ↓
_prepare_parameters()      类型/单位/权限/wire 名/别名冲突（MCP 本地）
  ↓
call_grpc()                JSON 序列化、超时控制、锁获取
  ↓
EDI gRPC 服务             最终业务校验和执行
```

#### 参数目录与 11 步校验管线

参数目录 `simulation_component_catalog.json`（v2.0.0）是 MCP 层对 EDI 参数知识的本地编码——不替代 EDI 校验，而是让 **MCP 本地完成大部分校验**，减少无效 gRPC 调用。设计动机：AI 友好（schema 返回类型/单位/权限）、校验前置（90% 参数错误本地拦截）、权限控制（`BandwidthForNoise` 标记不可设）、wire 透明（公开名 `Freq` → 线名 `Freq[1]`）。

`_prepare_parameters()` 是仿真器件工具共享的 11 步校验管线，任何一步失败返回带 `error_code` 的结构化错误：

```
1. 类型检查         parameters 必须是 dict
2. 空值检查         create 允许空字典；update 必须非空
3. 控件存在         检查 component_type 在 catalog 中
4. 参数名解析       先查固定参数 → 再匹配动态模式 Freq[{n}]/Order[{n}]
5. 权限检查         create_allowed / update_allowed
6. 值结构检查       每个参数值必须是 {"value": ..., "unit": ...}
7. value 存在       不能为 null、数组、对象或空字符串
8. 值类型校验       number: 拒绝 NaN/Infinity；integer: 拒绝 1.5；string: 原样透传
9. 枚举校验         如 CalcS 只能 "yes"/"no"
10. 单位检查        required → 必须有 unit；forbidden → 不能有 unit
11. 别名冲突        如 Freq + Freq[1] → 都映射到 Freq[1] → 报错
输出: wire_params   如 Freq → Freq[1]（公开名→线名转换）
```

**动态参数模式**：`Freq[{index}]` / `Order[{index}]` 正则匹配 1-32 组；快捷名 `Freq`（→ `Freq[1]`）与显式 `Freq[2]` 同时出现时第 11 步检测重复。连续量参数（`Freq`、`Start/Stop/Step` 等）`value_type=string` 允许变量引用（如 `"FREQ"` 跟随扫频），仅离散量保持 `integer`。

---

## 三、特殊机制

### 3.1 远程访问

MCP 服务默认只监听本机 `127.0.0.1:50026`；加 `--host 0.0.0.0` 监听所有网卡。远程模式要打通三个环节：

#### ① 监听地址（`--host`）

`main()` 加 `--host`（默认仍是 `127.0.0.1`，本机零变化）；`settings.py` 加 `mcp_bind_host`；`uvicorn.run(host=host)`。

#### ② transport_security：非环回一律 421

FastMCP 只在构造时 host ∈ 环回才自动填 `allowed_hosts`；本仓在 import 期构造 FastMCP，`start_servers.py` 是构造后才改 `mcp.settings.host` → **该字段不会重算**，导致非环回访问被 `421 Invalid Host header` 拒绝（实测只加 `--host` 也连不上）。

修法：在 `mcp.streamable_http_app()` **之前**显式重建 `transport_security`：

```python
# servers/utils.py
def local_host_names() -> list[str]:
    """环回 + 全部网卡 IP（psutil）+ 主机名/FQDN（含大小写）。"""
    # getaddrinfo(gethostname) 会漏网卡，必须用 psutil.net_if_addrs()

def build_transport_security(extra_hosts=()):
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,   # 绝不关闭
        allowed_hosts=patterns,                  # 裸名 + 裸名:* 两种模式
    )

# start_servers.py —— 必须在 streamable_http_app() 之前
if not is_loopback_host(host) or extra_hosts:
    mcp.settings.transport_security = build_transport_security(extra_hosts)
```

**必须守的两条**：① 不要关 `enable_dns_rebinding_protection`（关了伪造 `Host: evil.com` 会被放行）；② 枚举必须完整（`getaddrinfo(gethostname)` 会漏 VPN 网卡，且只列 IP 时主机名访问被 421）→ 用 psutil + 带主机名，留 `MCP_EXTRA_ALLOWED_HOSTS` 兜底。**排查口诀：看到 421 就把客户端用的 IP/主机名补进白名单。**

#### ③ 产物链接地址：按请求 Host 推导

`get_server_base_url()` 把 `0.0.0.0` 强映射成 `127.0.0.1` → 远程主机的产物链接会指向它自己的 localhost。修法：用 `ContextVar` 记录当前请求的 `Host`，产物链接按它推导：

```python
# servers/utils.py
_request_base_url: ContextVar[str] = ContextVar("mcp_request_base_url", default="")

def get_server_base_url() -> str:
    base = _request_base_url.get()          # 优先请求 Host
    if base: return base
    public_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{public_host}:{port}"   # 无上下文回落到启动地址

# start_servers.py —— 允许列表内的 Host 才采信（防伪造）
class RequestBaseURLMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        if host and len(host) <= 255 and host_without_port(host).lower() in self._allowed:
            set_request_base_url(f"{request.url.scheme or 'http'}://{host}")
        return await call_next(request)
```

**为什么必须校验 Host**：`Host` 是客户端自己写的，伪造 `Host: evil.example.com` 会把产物链接污染成 `http://evil.com/...`（等于把下载权递出去）。所以只有 Host ∈ 本机允许列表才写进上下文，否则回落。

**已知限制**：异步任务在 `TaskRunner` 的 `ThreadPoolExecutor` worker 里跑，**contextvar 是空的**（不像 `anyio.to_thread` 会复制 context）→ 异步产物链接会回落 `127.0.0.1`。将来做「异步任务自动注册链接」需补 `MCP_PUBLIC_BASE_URL` 兜底。

### 3.2 进程白名单

MCP 服务默认只监听本机，通过进程白名单实现「只放行指定 agent 进程、拒绝其它进程」。核心在 `servers/process_guard.py`，接线在 `start_servers.py`。

**机制**：反查「连接由哪个进程发起」。OS 的 TCP 连接表记录每条连接的 owning PID，客户端无法伪造：

```
请求 → 来源端口(request.client.port) → psutil.net_connections 反查 PID → exe 路径 + 命令行 → 白名单子串比对 → 放行 / 403
```

**核心函数**（`servers/process_guard.py`）：
- `find_client_process(client_port, server_port)`：遍历 `psutil.net_connections(kind="inet")`，找 `raddr.port == server_port 且 laddr.port == client_port` 的已建立连接，返回 `(exe 完整路径, 命令行)`。
- `resolve_client_process(...)`：带 60s TTL 缓存的查询（keep-alive 复用端口时避免每次扫全表）。
- `ProcessProbeMiddleware`：探针，只打印来源进程（`exe=` + `cmd=`），不拦截。
- `ProcessWhitelistMiddleware`：白名单拦截，命中放行、未命中 403。

**接线**：本机模式且白名单有值 → 挂 `ProcessWhitelistMiddleware`；本机留空或**远程模式（`--host` 非环回，白名单自动忽略）** → 挂 `ProcessProbeMiddleware`。远程反查不到来源进程（客户端源端口不在本机 TCP 表），若仍启用会把所有远程请求拦成 403。

**匹配规则**：每条白名单条目**精确对应进程身份，不做裸子串/后缀匹配**——路径型条目（含 `/`）= exe 路径精确相等或目录前缀；关键词型 = 完整命令行 token 或 exe basename。反查不到来源进程 → 默认拒绝（`fail_open=False`）。

**路径豁免** `_PROTECTED_PREFIXES = ("/mcp",)`：只对 `/mcp`（agent 直连的 MCP 端点）做白名单校验；`/ui` `/chat` `/tools/list` `/upload`（浏览器访问）和 `/health` `/ready` `/metrics`（诊断）放行。

**为什么不用 token / clientInfo**：同机同用户下，配置里的 token 和客户端自报的 `clientInfo.name` 都能被其它进程读到/伪造；只有 OS 记录的「连接来源 PID」无法伪造。局限：进程名/路径理论上可被「改名 + 放同路径」绕过，100% 隔离需 OS 用户隔离。

### 3.3 产物传输

仿真结果、截图、报告都生成在引擎机，远程客户端要能拿到文件本身（而非只有引擎机路径）。

| 产物 | 现状 | 远程可用 |
|---|---|---|
| `show_image` ≤10MB | base64 内嵌 `ImageContent` | ✅ |
| `show_image` >10MB | 返回引擎机本地路径 | ❌ |
| `open_document` link | 生成 `/documents/{token}` HTTP 链接 | ⚠️ 靠 3.1 Host 推导 |
| 产物 `file://` / `markdown_link` | `build_file_link()`（4 个工具） | ❌ `file://` URI 指向客户端本机 |
| `.snp` / `.raw` / 任意非办公产物 | `open_document` 白名单只有 10 种办公格式 | ❌ 完全拿不到 |

**核心方案：`fetch_artifact(file_path, ttl_seconds=600)`** —— 把引擎机任意产物注册成可下载 URL：

```python
@mcp.tool()
def fetch_artifact(file_path: str, ttl_seconds: int = 600) -> dict:
    p = Path(file_path).expanduser().resolve()
    if not p.is_file():
        return {"success": False, "error_code": "FILE_NOT_FOUND", ...}
    ttl = max(30, min(int(ttl_seconds), 3600))
    token, url = _doc_store.register(str(p), disposition="attachment", ttl=ttl)  # 需 per-token ttl
    return {"success": True, "url": url, "file_name": p.name,
            "size_bytes": p.stat().st_size, "sha256": _sha256_file(p), "expires_in": ttl}
```

关键点：**不校验扩展名**（只校验存在），复用 `/documents/{token}` 路由与 store，URL 里的 host 由 3.1 的请求 Host 推导给出。大文件 `sha256` 用手写分块循环（`hashlib.file_digest` 是 3.11+ API，本仓 `requires-python >=3.10`）。

**产物通道的鉴权方式（刻意设计）**：`/documents/{token}`、`/images/{token}` 不在 Bearer 保护清单里——它们靠**不可猜 token**（`secrets.token_urlsafe(24)`）+ 短 TTL 自我鉴权。因为主机浏览器/`curl`/下载器直接拉这些 URL，带不了 MCP 的 `Authorization` 头。

**安全边界**：`fetch_artifact` 是「远程机任意文件读取」通道。配合本期「不做鉴权」⇒ 任何能连到远程机的人都能下载远程机任意文件（含 `.env`）。用户已接受（目录不限制），日后可加目录白名单。

### 3.4 并发与排队

并发锁一览：

| 资源 | 锁类型 | 原因 |
|---|---|---|
| EDA gRPC 操作 | `threading.RLock` 全局锁 | EDI 进程同一时间只能做一件事 |
| 异步仿真任务（EDA） | `threading.Lock` | 保护 `_sim_tasks` 字典 |
| CST / HFSS 任务队列 | `TaskRunner`（单 worker） | CST/AEDT 单实例，同时只能跑一个 |
| TurboCharts 进程 | `BoundedSemaphore(1)` | 一次只能跑一个实例 |
| ANSYS COM 操作 | `threading.RLock` | 保护 AEDT COM 对象 |
| Chat 会话 | `threading.Lock` / `asyncio.Lock` | 保护 `_sessions` 字典、同会话串行 |
| 图片 token | `threading.RLock` | 保护 `_IMAGE_TOKENS` 字典 |
| 慢同步工具（`turbocharts_convert` / `compare_simulation_results` / `generate_simulation_report`） | `per_tool_mutex`（各一把 `threading.Lock`） | offload 后同类工具串行，防并发争用共享资源 |

#### 同步工具统一 offload（已实现）

FastMCP 对**同步工具**是直接调用（无 `to_thread`）→ 工具跑在**事件循环线程**上，执行期间整个服务停摆：产物下载、`/health`/`/ready`、其它会话的轮询全部排队。实测 `turbocharts_convert`（1.6s）期间探活从 3ms 涨到 1344ms。

**实现**：在 `servers/__init__.py` monkeypatch `Tool.run`，所有同步工具在 MCP 调用时整段丢到工作线程（`anyio.to_thread.run_sync` + 子线程 `asyncio.run` 驱动），事件循环立即回来接单；工具函数本身保持 sync（直接调用仍返回 dict，Python API 不变）。异步任务不受影响（worker 在 `ThreadPoolExecutor` 里）。

**配套加锁**：offload 后同类工具并发会争用共享资源（turbocharts 子进程、Matplotlib 全局状态、报告渲染服务）。`servers/utils.py` 的 `per_tool_mutex` 装饰器给 `turbocharts_convert` / `compare_simulation_results` / `generate_simulation_report` 各加一把独立锁，同类工具互斥、不同工具互不阻塞（实测不锁时并发 2 个 turbocharts 耗时 3.7×）。

### 3.5 重启恢复与生命周期

- `/ready` 端点：初始化中返回 503，完成后 200（含 `transport`/`stateless`/`tool_count`/`started_at`/`tools_hash`）
- 优雅关闭：SIGINT/SIGTERM → 停止接收新请求 → 退出进程
- 生命周期日志：`MCP_STARTING` → `MCP_READY` → `MCP_STOPPING` → `MCP_STOPPED`
- 旧 session_id 返回 `Session not found`，客户端重新 initialize
- 重启后仿真任务状态丢失（内存），查询旧 task_id 返回 `TASK_NOT_FOUND`（`outcome_known=false`）
- 不自动生成文件、不自动修改工程、不自动重放工具调用

### 3.6 日志系统

所有模块统一 `logging.getLogger(__name__)`，输出到 `%TEMP%/edi/data/log/edi_mcp_YYYYMM.log`（RotatingFileHandler，10MB × 5 备份）。

| 模块 | Logger | 记录内容 |
|---|---|---|
| `start_servers` | `edi_mcp` | 生命周期 STARTING → READY → STOPPING → STOPPED |
| `grpc_client` | `eda.grpc_client` | SUBSCRIBING → ACCEPTED → SUCCEEDED/FAILED/TIMEOUT |
| `simulation` | `eda.simulation` | 任务创建 + 完成/异常 + outcome_known |
| `turbocharts` | `turbocharts` | 命令行执行 + return_code + 耗时 |
| `vision` | `multimodal.analyze` | API 状态码 + 模型名 + 图片大小 + 耗时 |
| `report` | `report.generator` | 文件类型 + 模型名 + 图表数 + 器件数 |
| `chat_service` | `chat_service` | request_id + 工具名 + 脱敏参数 + 耗时 |

Chat 工具调用日志对路径做脱敏（只记文件名），不暴露用户目录结构。

### 3.7 Chat 会话与破坏性确认

#### 会话管理

`ChatSession` 记录消息历史、当前工程、最近扫描目录、仿真任务等上下文。会话 TTL 2 小时，全局上限 100 个，每 5 分钟清理。同 session 并发请求串行化（`asyncio.Lock`，10 秒超时）。

#### 破坏性工具确认门

注册时标记破坏性工具，参数感知确认：

| 工具 | 确认条件 |
|---|---|
| `delete_simulation_component` / `replace_models_from_csv` / `replace_port_component` | 无条件确认 |
| `close_edi_project` | `need_save=true` 时确认 |
| `generate_simulation_report` | `overwrite=true` 时确认 |
| `generate_schematic_from_netlist` | `clear_before_import=true` 时确认 |

Chat 层拦截 → 保存 `PendingAction` → 展示操作摘要 → 用户明确回复「确认」才执行 → 一次确认执行一次，5 分钟过期 → 执行时用原始保存参数（防模型篡改）。

#### 默认值透明

产生输出文件（截图/图表/报告）或采用默认值时，先告知用户输出位置/默认值并询问是否调整。内置 Chat system prompt 规则 11 强制执行；外部 agent 通过 FastMCP `instructions` 下发同一规则；`edi://reference/operation-guide` Resource 也列出该规则。

---

## 四、工具实现（按通信类型分组）

底层通信模型见第一章，本节按工具列出各工具的 EventType / payload / 关键实现点。

### 4.1 工程管理

| 工具 | EventType | 通信 | 说明 |
|---|---|---|---|
| `create_project` | CREATE_PROJECT(23) | gRPC | `name` + 可选 `author`/父目录，创建 .epp 工程（不打开、不显示向导） |
| `open_edi_project` | OPEN_PROJECT(1) | gRPC | `project_path`；已有工程窗口时复用 |
| `close_edi_project` | CLOSE_PROJECT(8) | gRPC | `project_path`, `need_save` 可选保存 |
| `list_schematic_components` | LIST_SCHEMATIC_COMPONENTS(18) | gRPC | 返回原理图全部器件及完整参数（比本地读实时） |
| `get_schematic_component_info` | GET_SCHEMATIC_COMPONENT_INFO(19) | gRPC | `project_path`, `instance_name` 单查器件 |
| `get_components_static_params` | — | gRPC→MMS | 转发到 `POST /api/v1/components/static-params/`，查重量/尺寸/封装/厂商 |
| `batch_query_component` | BATCH_QUERY_COMPONENT | gRPC→MMS | 按型号批量查询，按输入顺序对齐、未命中补 null |
| `list_epp_projects` | — | 本地 | `rglob("*.epp")` 扫描 |
| `get_project_summary` | — | 本地 | 聚合元数据/原理图/器件/仿真配置/最新 RAW |
| `analyze_variables` | — | 本地 | Var 定义 + 引用 + Sweep 配置 |

### 4.2 仿真器件（协议 v3）

| 工具 | EventType | 关键实现 |
|---|---|---|
| `create_simulation_component` | CREATE(11) | **v3 不传 parameters**，用工厂默认值创建，创建后按 `instance_name` 调 update 设参 |
| `update_simulation_component` | UPDATE(15) | **三路类型推断**（见下），无条件执行完整校验 + wire 转换 |
| `delete_simulation_component` | DELETE(12) | 不做本地预检查，直接由 EDI 按 instance_name 执行 |
| `set_component_active_state` | SET_ACTIVE_STATE(14) | 状态规范化 `.strip().upper()`，确定性设置（幂等） |
| `generate_schematic_from_netlist` | GENERATE(13) | 双重确认：`clear_before_import=true` 必须同时 `confirm_clear=true` |
| `replace_port_component` | REPLACE_PORT_COMPONENT(16) | TermG/P_nToneG 替换，保留连线，Chat 层需确认 |
| `attach_out_component` | ATTACH_OUT_COMPONENT(17) | 自动判断引脚朝向 → 计算放置位置 → 顺时针四方向检测重叠 → 器件+网线同一撤销组 |
| `list_simulation_components` | — | 本地读 + wire→public 映射 |
| `get_simulation_component_schema` | — | 读 catalog，与 `edi://reference/simulation-components` 同源 |
| `replace_schematic_from_file` | LOAD_SCHEMATIC_FROM_FILE | 从 .ep 文件整体替换原理图 |

**update 三路类型推断**：

```python
explicit_type = component_type.strip() if component_type else ""
component, _ = _find_component_by_instance(project_path, instance_name)
actual_type = component.get("type", "") if component else ""

if actual_type:                       # 磁盘找到了实例
    if explicit_type and explicit_type != actual_type:
        return COMPONENT_TYPE_MISMATCH
    ct = actual_type
elif explicit_type:                   # 磁盘没有，用户提供（如 EDI 新建未保存）
    ct = explicit_type
else:                                 # 两者都没有
    return COMPONENT_TYPE_REQUIRED
```

### 4.3 仿真

- `start_simulation_async`：异步启动（见 1.3 异步任务模型），立即返回 `task_id`
- `get_simulation_async_status` / `get_simulation_async_result`：读 `_sim_tasks` 内存注册表
- `list_eda_tasks`：列出当前异步任务，按状态过滤
- `simulate_netlist`：校验 `netlist_path` 存在 → `call_grpc(SIMULATE_NETLIST)` → EDI 复制网表到临时目录 → 执行 ADS → 复制 `result.raw` 到 `history/` → 清理临时目录
- `simulate_netlist_with_ads`：直接调 ADS 仿真控制器（`CALL_SIMULATION_CONTROLLER`）
- `simulate_anti_burnout`：对具备抗烧毁数据的器件执行输入功率仿真和风险评估

### 4.4 导出与模型 / 软 IP

| 工具 | EventType | MCP 层校验 |
|---|---|---|
| `export_project_netlist` | VIEW_PROJECT_NETLIST(3) | 仅校验 `project_path` |
| `capture_schematic` | CAPTURE_SCHEMATIC(7) | 校验 `output_path` 扩展名（PNG/JPG/BMP/SVG）+ resolve |
| `replace_models_from_csv` | MODEL_REPLACE(6) | `csv_path` 存在且后缀 `.csv` |
| `get_model_category_params` | GET_MODEL_CATEGORY_PARAMS(24) | 无业务参数 |
| `search_public_models` / `search_personal_models` | SEARCH_*_MODELS(25/26) | `sub_type` 非空；`filters` 原样转发 |
| `search_schematic_from_public_library` / `search_schematic_from_personal_library` | SEARCH_SCHEMATIC_*(38/39) | `search_name` 必需 |
| `use_schematic_from_library_create_project` / `use_schematic_from_library_import` | USE_SCHEMATIC_*(36/37) | `file_uuid` 非空（import 加 `project_path`） |
| `export_schematic_components_to_csv` | EXPORT_SCHEMATIC_COMPONENTS_TO_CSV(40) | `csv_path` 非空 |
| `get_signal_chain` | — | 本地 | 解析网表，节点接力追踪信号链路（源→负载），方向 forward/backward |
| `search_soft_ip_categories` | SEARCH_SOFT_IP_CATEGORIES(42) | 无业务参数 |
| `search_public_soft_ip_models` / `search_personal_soft_ip_models` | SEARCH_*_SOFT_IP_MODELS(43/44) | `filters` 原样转发 |
| `download_soft_ip_model` | DOWNLOAD_SOFT_IP_MODEL(45) | `id` UUID + `save_path` 非空 + `freq`>0 + 可选 `bandwidth`（有限数值） |

### 4.5 工作区 / 原理图扩展

| 工具 | EventType | MCP 层校验 |
|---|---|---|
| `list_ideal_components` | LIST_IDEAL_COMPONENTS(27) | 无业务参数 |
| `create_workspace` / `switch_workspace` | CREATE/SWITCH_WORKSPACE(28/29) | `path` 非空 |
| `load_performance_component_from_mms` | LOAD_PERFORMANCE_COMPONENT_FROM_MMS(30) | `original_uuid` 非空 |
| `add_performance_component` | ADD_PERFORMANCE_COMPONENT(31) | `component_uuid` 非空 + `position` 有限数值 |
| `add_ideal_component` | ADD_IDEAL_COMPONENT(32) | `component_type` 非空 + `position` 校验 |
| `clear_schematic` | CLEAR_SCHEMATIC(33) | `confirm_clear=true` 双重确认 |
| `add_wire` | ADD_WIRE(34) | 5 字段必填 + `pin_index` 0~2147483647 |
| `get_current_workspace` | GET_CURRENT_WORKSPACE(35) | 无业务参数 |

### 4.6 图表

| 工具 | 通信 | 关键实现 |
|---|---|---|
| `list_result_curves` | subprocess | 解析 RAW 头（MDS/XML 两种格式），前 65536 字节，`_suggest_curves` 推荐曲线名 |
| `turbocharts_convert` | subprocess | `run_turbocharts()` 串行信号量，`--linename`/`--dependcy`（真实参数名少一个 n） |
| `compare_simulation_results` | subprocess | 2-8 个 RAW 对比，alignment=intersection/interpolation，Matplotlib 叠图 |

### 4.7 ANSYS HFSS 与 CST

| 工具 | 通信 | 关键实现 |
|---|---|---|
| `launch_aedt` | COM | 已运行返回状态；否则 subprocess 启动 → 轮询 COM 就绪 |
| `open_hfss_project` | COM | 清理失效锁 → COM 附着 OpenProject / subprocess 启动 |
| `close_hfss_project` | COM | CloseProject（可选 Save）→ 等 2 秒 → 清残留锁 |
| `get_hfss_project_info` | COM | 读 GetProjectList / GetActiveProject / GetActiveDesign |
| `start_hfss_analysis_async` | COM | `hfss_runner.submit(require_idle=True)` 忙则拒绝 |
| `get_hfss_analysis_status` | 内存 | 读 hfss_runner.snapshot，可选 refresh_from_aedt |
| `cst_solve_async` / `cst_solve_query` | CST 会话 | `cst.interface` run_solver，`cst_runner` 排队 |
| `cst_export_snp` | CST 无会话 | `cst.results` 读 S 参数导出 Touchstone |
| `cst_export_farfield` / `cst_export_farfield_query` | 混合 | `cst.results` 检测已求解 + `cst.interface` execute_vba 导远场 |

### 4.8 启动 / 诊断

| 工具 | 通信 | 关键实现 |
|---|---|---|
| `launch_edi` | subprocess | TCP 检查 gRPC 就绪 → 已运行跳过 → Popen EDI → 轮询就绪（30s） |
| `get_service_status` | 内存 | gRPC channel 缓存 + queue busy 标志，只读不占槽位 |
| `get_service_logs` | 本地 | 读 EDI 日志目录，统计 ERROR/WARN/异常堆栈 |

### 4.9 图片 / 文档 / 报告

- **`show_image`**：`_validate_image_path`（resolve 防 `..` 遍历、拒网络路径、扩展名白名单）→ ≤10MB 内嵌 `ImageContent`，>10MB 只返回本地路径。
- **`analyze_image`**：调第三方视觉模型。三项配置全非空即开启；`BoundedSemaphore(2)` 并发控制；system prompt 防图片提示注入；日志不记 Base64/API Key/分析内容；返回 `content_is_untrusted: true`。
- **`open_document`**：`mode="link"` 生成 10 分钟 HTTP 链接（`/documents/{token}`），`mode="local"` 用 `os.startfile()` 打开；10 种办公格式白名单；UNC 拒绝、nosniff 头。
- **`generate_simulation_report`**：16 步校验（输出路径/模型名/spec_table/charts/components/schematic）→ POST 渲染服务 → 状态码映射错误码（400→`REPORT_VALIDATION_FAILED`、409→`OUTPUT_FILE_BUSY`、500→`REPORT_RENDER_FAILED`）。只校验数据调 API，不自动仿真、不编造指标、默认禁止覆盖。

### 4.10 TR 仿真集成（HTTP 转发）

TR 的 17 个 `tr_*` 工具不直接操作 EDI，而是把调用转发给外部服务 **SimulationAgent**（默认 `http://127.0.0.1:17866`，仅监听本机、无鉴权），核心在 `servers/tr_simulation/client.py`。

**HTTP 客户端机制**：

| 机制 | 说明 |
|---|---|
| 会话复用 | 有 `epp_path` 的工具按归一化路径映射固定 session_id；无 `epp_path` 的复用「当前最近 session」 |
| 稳定 request_id | 对 (tool, arguments) 取 sha256，相同参数重试命中原 operation（幂等去重，避免超时重试重复仿真） |
| 后台轮询 | 后台工具在 handler 内轮询 operation 到终态（间隔 1s，超时 600s） |
| 会话自愈 | 404（SimulationAgent 重启）时重建会话重试一次 |
| 错误映射 | HTTP 4xx/5xx → `TR_TOOL_FAILED`；连接失败/超时 → `TR_SERVICE_UNAVAILABLE` / `TR_SERVICE_TIMEOUT` |

**17 个工具**（按工作流阶段）：

| 工具 | 功能 |
|---|---|
| `tr_get_simulation_capabilities` | 查询 TR 仿真支持的指标、单位、结果语义、必需参数 |
| `tr_find_paths` | 查找端口和全部有效有向端口组合 |
| `tr_get_project_netlist` / `tr_read_netlist` | 获取工程当前真实网表 / 读网表修订版 |
| `tr_read_guide` | 读取 guides 目录中的 Word 指南 |
| `tr_query_components` / `tr_query_schematic_components` | 查器件类别/厂家/规格、原理图器件 |
| `tr_set_workflow_plan` | 持久化用户确认的链路仿真计划 |
| `tr_modify_netlist` | 创建可追溯的网表修订版 + 注入指标控制器 |
| `tr_run_simulation` | 执行网表修订版，经 EDI/ADS 取 result.raw |
| `tr_parse_raw` | 解析 result.raw → CSV/曲线图/标准化指标结果 |
| `tr_execute_simulation_plan` | 按已确认计划批量执行多个指标 |
| `tr_get_workflow_state` | 查询会话持久化的计划/工程/链路/指标状态 |
| `tr_prepare_report` | 生成报告草稿和判定证据 |
| `tr_generate_document` | 校验报告草稿并生成 PDF/DOCX |
| `tr_sync_project_components` | 网表变更同步回工程原理图 |
| `tr_restore_schematic` | 原理图回退到会话初始备份 |

### 4.11 工具设计动机与数据依赖

从「为什么需要这个工具」的视角说明每个工具的设计动机、解决的问题、与其它工具的数据依赖（谁提供输入、输出供谁）。

#### 工程管理

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `list_epp_projects` | AI 不知道工作区有哪些工程，一切操作的入口 | —（入口） | 几乎所有需要 `project_path` 的工具 |
| `create_project` | 新建工程（不显示向导） | — | 后续打开/仿真的前提 |
| `open_edi_project` | gRPC 操作要求工程已在 EDI 打开 | `list_epp_projects` | 仿真/器件/截图/网表 |
| `close_edi_project` | 释放 EDI 资源、落盘保存 | `list_epp_projects` | —（收尾） |
| `list_schematic_components` | 本地读磁盘看不到 EDI 未保存修改和运行态 | 已打开工程 | LLM 判断器件实时状态 |
| `get_schematic_component_info` | 按实例名单查器件详情（实时） | `list_schematic_components` | LLM 查器件详情 |
| `get_project_summary` | 一次看全工程概览，避免零散多轮查询 | —（本地读） | LLM 了解全貌、报告 |
| `analyze_variables` | EDA 参数化设计依赖 Var/Sweep，需理解变量关系 | —（本地读） | LLM 理解参数化设计 |
| `get_components_static_params` | 查器件物理固有参数（重量/尺寸/封装/厂商） | 选型列表 | LLM 查器件物理属性 |
| `batch_query_component` | 按型号批量确认模型是否存在（选型前置） | —（型号列表） | 选型时确认型号有效性 |

#### 仿真器件

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `get_simulation_component_schema` | AI 不知道器件支持哪些参数/单位/权限 | —（读 catalog） | create/update 参数校验 |
| `list_simulation_components` | 本地快速列器件（不占 EDI 槽位） | —（本地读） | update/delete/set_state 提供 instance_name |
| `create_simulation_component` | 新建仿真器件 | schema（类型） | update（创建后设参） |
| `update_simulation_component` | 修改器件参数 | list（instance_name）+ schema（校验） | — |
| `delete_simulation_component` | 删除不需要的器件 | list（instance_name） | — |
| `set_component_active_state` | 仿真时临时禁用/短路器件（不改参数） | list（instance_name） | — |
| `generate_schematic_from_netlist` | 从网表快速重建/追加原理图 | export_project_netlist（netlist 文件） | — |
| `replace_port_component` | 端口类型切换（TermG↔P_nToneG）保留连线 | list（instance_name） | — |
| `attach_out_component` | 给器件引脚挂 Out 器件观察输出 | list（instance_name） | — |
| `replace_schematic_from_file` | 用现成 .ep 文件整体替换原理图 | 现成 .ep 文件 | — |

#### 仿真

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `start_simulation_async` | 长仿真不能同步阻塞 | open_edi_project | get_status/result |
| `get_simulation_async_status` | 查进度和实时日志 | start（task_id） | LLM 判断进度 |
| `get_simulation_async_result` | 取最终结果和完整日志 | start（task_id） | list_result_curves / 报告 |
| `list_eda_tasks` | 看有哪些仿真在跑/排队 | — | LLM 判断仿真状态 |
| `simulate_netlist` | 不打开工程直接仿真网表 | export_project_netlist（netlist 文件） | list_result_curves / 报告 |
| `simulate_netlist_with_ads` | 直接调 ADS 控制器 | export_project_netlist（netlist 文件） | — |
| `simulate_anti_burnout` | 评估器件抗烧毁风险 | 已打开工程 | LLM 判断抗烧毁风险 |

#### 导出与分析

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `export_project_netlist` | 查看/导出工程网表 | open_edi_project | simulate_netlist / generate_schematic_from_netlist |
| `capture_schematic` | 截图原理图供分析/报告 | open_edi_project | 报告、show_image |
| `export_schematic_components_to_csv` | 生成模型替换用的器件 CSV | project_path | replace_models_from_csv |
| `get_signal_chain` | 理解信号从源到负载 | 网表 | simulate_anti_burnout 前置 |

#### 模型库 / 原理图库

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `replace_models_from_csv` | 批量替换元件模型（CSV 驱动） | CSV 文件 | — |
| `get_model_category_params` | 了解模型库分类和参数 | —（模型服务） | search_*_models 前置 |
| `search_public_models` / `search_personal_models` | 从公共/个人模型库选型 | sub_type | 选型、add_performance_component |
| `load_performance_component_from_mms` | 从 MMS 引入性能模型 | original_uuid | add_performance_component |
| `add_performance_component` | 把模型库 Component 放进原理图 | load（先导入） | — |
| `search_schematic_from_public_library` / `search_schematic_from_personal_library` | 从原理图库找拓扑 | search_name | use_schematic_from_library_* |
| `use_schematic_from_library_create_project` / `use_schematic_from_library_import` | 用库内容建工程/替换原理图 | 搜索结果的 id | — |

#### 软 IP

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `search_soft_ip_categories` | 了解软 IP 有哪些分类 | — | search_soft_ip_models 前置 |
| `search_public_soft_ip_models` / `search_personal_soft_ip_models` | 查软 IP 模型 | filters | download_soft_ip_model 前置 |
| `download_soft_ip_model` | 下载软 IP AEDT 模型 | id + save_path + freq（+可选 bandwidth） | — |

#### 工作区 / 原理图扩展

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `create_workspace` / `switch_workspace` | 新建/切换工作区 | — | get_current_workspace |
| `get_current_workspace` | 确定当前工作区（唯一事实来源） | — | edi://projects Resource |
| `list_ideal_components` | 知道有哪些内置器件 | — | add_ideal_component |
| `add_ideal_component` | 按坐标放内置器件 | list_ideal_components | — |
| `clear_schematic` | 清空原理图重建 | 工程路径 | — |
| `add_wire` | 手动连线 | list_schematic_components | — |

#### 启动 / 诊断

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `launch_edi` | 确保 EDI 在运行（gRPC 可用） | — | 所有 gRPC 工具前提 |
| `get_service_status` | 诊断 gRPC 通道/队列健康 | — | 诊断 |
| `get_service_logs` | 诊断 EDI 服务运行异常 | 日志目录 | LLM 诊断 |

#### 图表

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `list_result_curves` | RAW 曲线名难猜，画图前先解析 | result.raw | turbocharts_convert / compare |
| `turbocharts_convert` | RAW → 曲线图/CSV | list_result_curves（曲线名） | 报告 |
| `compare_simulation_results` | 对比多个仿真的同一条曲线 | list_result_curves + 多个 result.raw | — |

#### ANSYS HFSS / CST

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `launch_aedt` | 确保 AEDT 运行 | — | ANSYS 其它工具前提 |
| `open_hfss_project` | 打开 HFSS 工程 | launch_aedt | start_hfss_analysis_async |
| `close_hfss_project` | 关闭工程释放锁 | 工程名/路径 | — |
| `get_hfss_project_info` | 只读查 AEDT 项目/设计 | AEDT 运行 | LLM 了解 AEDT 状态 |
| `start_hfss_analysis_async` | 异步跑 HFSS 仿真 | open_hfss_project | get_hfss_analysis_status |
| `get_hfss_analysis_status` | 查 HFSS 仿真状态 | start（task_id） | LLM 判断进度 |
| `cst_solve_async` / `cst_solve_query` | 电磁求解（异步） | .cst 模型 | cst_export_* |
| `cst_export_snp` / `cst_export_farfield` | 导出 S 参数/远场 | 已求解 .cst | 画图/分析 |

#### 图片 / 文档 / 报告

| 工具 | 动机 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|
| `show_image` | 把本地图片返回客户端渲染 | — | 查看截图 |
| `analyze_image` | 让视觉模型理解图片 | —（图片路径） | 分析截图 |
| `open_document` | PDF/DOCX 需要预览/系统打开 | 报告 | LLM 查看报告 |
| `generate_simulation_report` | 整理成正式 PDF/DOCX 报告 | summary + 曲线图 + 截图 + 结果 | open_document 查看 |

---

## 五、Resources（8 个）

Resource 是**只读上下文/参考资料**，客户端通过 `resources/list` → `resources/read` 访问。固定 URI、无参数、返回 JSON 或 Markdown。

注册机制：`servers/resources_prompts/` 内 `@mcp.resource(uri, name, title, description, mime_type)` 装饰器定义，`registry_server.py` 的 `import servers.resources_prompts` 触发注册。其中 6 个在 `resources_prompts/` 包内，另 2 个由业务包自带（turbocharts、tr_simulation）。

### 1. `edi://service/overview` — 服务概览

- **用途**：让 LLM 一次性了解服务能力、版本、安全规则，作为会话开场上下文。
- **干什么**：返回服务元信息（版本号、协议版本、gRPC 目标、工作区状态、安全规则标志）。
- **怎么实现**：纯静态组装，读 `SERVER_VERSION`、`EDA_GRPC_SERVER` 常量拼 dict。**不含任何密钥/路径敏感信息**（有测试断言 `sk-`、`API_KEY` 不出现）。

### 2. `edi://service/status` — 实时运行时状态

- **用途**：诊断 gRPC 通道健康度、队列占用、工具集指纹。
- **干什么**：返回 `grpc_target` / `channel_state` / `channel_cached` / `queue_locked` / `tool_count` / `tools_hash`。
- **怎么实现**：与 `get_service_status` 共享数据源——`get_cached_channel()` + `channel_ready_future` 探测状态，`is_queue_busy()` 读执行槽占用；`tools_hash` 是版本指纹（md5(sorted 名)[:8]），算法与 `/ready` 一致。

### 3. `edi://projects` — 工作区工程目录

- **用途**：让 LLM 无需翻文件系统就知道「工作区有哪些工程」。
- **干什么**：返回 `workspace` + `projects_dir` + `count` + 精简 `projects` 列表（每工程只有 name/path/size）。
- **怎么实现**：`_current_workspace()` 调 `GET_CURRENT_WORKSPACE` 拿目录，再 `list_epp_projects` 扫描——不本地猜测路径，接口返回什么就用什么。失败时返回空清单 + `warning`。

### 4. `edi://reference/simulation-components` — 仿真器件参数参考

- **用途**：SP/HB/XDB 器件的参数 Schema 参考（公开参数名、gRPC 名、值类型、单位、权限）。
- **怎么实现**：直接 `return _load_catalog()`，与 `get_simulation_component_schema` 同源，**不维护两套定义**。

### 5. `edi://reference/operation-guide` — 操作安全约束

- **用途**：给 LLM 的操作「红线」，防止破坏性/危险操作。
- **干什么**：返回 Markdown 安全规则清单（TIMEOUT 后禁止自动重试创建/导入、clear_before_import 双重确认、不猜路径、产生输出文件先告知用户等）。
- **怎么实现**：硬编码 Markdown 返回（`text/markdown`），与工具 docstring、错误码词典口径一致。

### 6. `edi://reference/error-codes` — 错误码词典

- **用途**：让 LLM 根据 gRPC 返回的 status 选择正确重试/排查策略。
- **干什么**：返回状态码 → 含义 → 建议动作对照表（SUCCEEDED/FAILED/REJECTED/QUEUE_TIMEOUT/TIMEOUT/STREAM_DISCONNECTED/GRPC_UNAVAILABLE/PAYLOAD_TOO_LARGE/PROTOCOL_MISMATCH/TASK_NOT_FOUND）+ 重试原则。
- **核心原则**：TIMEOUT/STREAM_DISCONNECTED 时 `outcome_known=false` 别假设失败；非幂等禁止自动重试；查询类可安全重试一次。

### 7. `edi://reference/turbocharts-guide` — RAW 转图说明

- **用途**：`turbocharts_convert` / `list_result_curves` 之前的参考——参数怎么传、曲线名（`--linename`）单位与线段名怎么写。
- **怎么实现**：`servers/turbocharts/resource.py` 直接读引擎自带《RAW 转图像工具使用说明.txt》**原文**（不复制内容，引擎升级换文件即生效）；文件缺失返回带路径与单位要点的提示，不抛异常。

### 8. `edi://integration/workflow` — TR 仿真工作流规则

- **用途**：调用 `tr_*` 工具前必须遵循的工作流规则（会话复用、参数确认、失败纠错、报告事实保护、原理图同步与回退）。
- **怎么实现**：`servers/tr_simulation/resource.py` 的 `resource_tr_workflow()` 调 `fetch_workflow()` **实时从 SimulationAgent 拉取** `/api/v1/integration/workflow`，版本化、不复制内容。

---

## 六、Prompts（9 个）

Prompt 是**可复用工作流模板**，客户端通过 `prompts/list` → `prompts/get` 访问。带参数（如 `project_path`），返回 `[{"role": "user", "content": "..."}]` 给 LLM 的步骤指令。

设计原则：Prompt 只返回消息模板不直接执行工具；非法参数直接拒绝不静默回退；仿真 Prompt 含轮询限制（最多查一次、间隔 ≥10s、单次最多 3 次）；核心工作流只靠 Tools 也能完成（Resource 是增强不是依赖）。

### 1. `inspect_edi_project` — 工程只读检查

只读查看工程全貌，不改工程、不启动仿真。按 `detail_level` 分档引导 `get_project_summary` → `analyze_variables` → `list_simulation_components`。

### 2. `run_and_review_simulation` — 仿真执行 + 日志分析

默认异步流程（`start_simulation_async` → 查一次状态 → `get_simulation_async_result`），含「不紧密轮询、单次最多 3 次」限流；`analyze_log=True` 时分析 `ads_output`。

### 3. `configure_simulation_component` — 配置仿真器件

create/update 两条分支：先查 Schema → 映射需求为合法参数 → 用户确认 → 调 create/update。强调「参数名必须与 Schema 一致、不编造」。

### 4. `create_simulation_report` — 生成仿真报告

固定 10 步模板：确认输出路径 → 查工程 → 查已有结果（不自动重跑）→ 列曲线 → 转图 → 截原理图 → 整理 spec_table/components → 调 `generate_simulation_report`。

### 5. `troubleshoot_edi_error` — 诊断调用错误

读 `edi://reference/error-codes` → 查 `get_service_status` → 按 status 分支给建议（TIMEOUT 不重试、GRPC_UNAVAILABLE 启动 EDI、QUEUE_TIMEOUT 查长任务、REJECTED 修参数）。

### 6. `assess_anti_burnout` — 抗烧毁评估

调 `simulate_anti_burnout` → 处理已知失败原因（PORT1 功率 >40dBm、多通道合路）→ 按裕量升序汇总表格。约束「max_input_power 单位可能 dBm/W，比较前统一换算 dBm」。

### 7. `select_component` — 器件选型（含替换闭环）

8 步闭环：`get_model_category_params` 确认子类 → **依次**搜公共/个人库（gRPC 串行不并行）→ 提取过滤 → `get_components_static_params` 查厂商/尺寸 → 对比表 → 推荐 → 确认后取 original 三列 → 生成 CSV 调 `replace_models_from_csv`。重点标注字段映射（`model_uuid` → `original_uuids`；`component_type/instance_name/model_id` → CSV 列），防 LLM 编造。

### 8. `analyze_signal_chain` — 信号链分析

步骤 0 若跑过抗烧毁先 `export_project_netlist` 刷新网表（防 PowerPin 污染）→ `get_signal_chain` 追踪 → 逐级说明器件作用 → 处理 warning → 结合 result.raw 说明功率。

### 9. `run_tr_simulation` — TR 仿真工作流

注入版本化工作流规则，引导 Agent 依次调 `tr_get_simulation_capabilities` → `tr_find_paths` → 确认 → `tr_*` 仿真 → 报告。`servers/tr_simulation/prompts.py` 的 `prompt_run_tr_simulation(epp_path="")`，`epp_path` 缺省时由 Agent 先向用户确认。


