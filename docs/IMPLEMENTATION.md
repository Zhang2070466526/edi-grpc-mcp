# 工具实现原理

每个 MCP 工具按底层通信方式分为 5 种实现类型：gRPC 远程调用、本地文件读取、subprocess 命令行、COM 对象、内存服务。本文逐一说明每种工具的协议交互、数据结构、校验流程、错误处理和设计决策。
>
> 相关文档：[TOOLS_API.md](./TOOLS_API.md)（69 个工具接口）、[HTTP_API.md](./HTTP_API.md)（HTTP 路由）。

---

## 目录

- **[一、gRPC 远程调用（39 个工具）](#一gRPC远程调用39个工具)**：通信协议、call_grpc 统一入口、11 步参数校验管线
- **[二、本地文件读取（6 个工具）](#二本地文件读取6个工具)**：.epp 格式、S-expression 解析、参数格式化
- **[三、内存服务（4 个工具）](#三内存服务4个工具)**：异步任务状态查询、服务诊断
- **[四、参数目录与 Schema](#四参数目录与-Schema)**：目录设计动机、核心函数、动态参数模式
- **[五、subprocess 命令行（4 个工具）](#五subprocess命令行4个工具)**：TurboCharts、RAW 曲线查询、结果对比、EDI 启动
- **[六、COM 对象与 CST 官方接口（ANSYS 6 个 + CST 5 个）](#六COM对象与CST官方接口)**：COM 附着、AEDT 检测、锁文件管理、CST 接口
- **[七、图片工具（2 个）](#七图片工具2个)**：show_image、视觉分析
- **[八、Chat 聊天服务](#八Chat聊天服务)**：会话管理、多轮闭环、工具 Schema、安全加固
- **[九、Resources 与 Prompts](#九Resources与-Prompts)**：只读资源、可复用工作流
- **[十、文档工具（1 个工具）](#十文档工具1个工具)**：open_document
- **[十一、报告渲染（1 个工具）](#十一报告渲染1个工具)**：16 步校验、调用流程
- **[十二、跨层设计原则](#十二跨层设计原则)**：校验分层、错误码、并发、outcome_known、日志等
- **[十三、工具设计动机与数据依赖](#十三工具设计动机与数据依赖)**：每个工具的动机 / 功能 / 依赖 / 底层机制

---

## 一、gRPC 远程调用（39 个工具）

所有操作 EDI 工程和仿真器件的工具共享同一套 gRPC 通信模型。核心实现在 `servers/eda/grpc_client.py`（通信层）和 `servers/eda/simulation_components.py`（参数校验层）。

### 1.1 通信协议

EDI 服务通过 `proto/ecserver.proto` 定义了 `ExternalCall` 服务，当前协议版本 v2，27 种事件类型：

```proto
service ExternalCall {
  rpc PerformAction(Request) returns (Response) {}    // 提交任务
  rpc FetchEvent(FetchEventRequest) returns (stream Event) {}  // 流式拉取结果
}
```

核心设计是 **异步任务模型**：`PerformAction` 只负责受理任务（返回 `code=0` 表示已受理），最终结果通过 `FetchEvent` 的流式通道异步推送。

### 1.2 call_grpc() 统一入口

所有 gRPC 工具都调用同一个函数，唯一的区别是 `task_type` 枚举值和 `payload` 字典：

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

第 1 步和第 2 步都受剩余的 `timeout_seconds` 约束。

#### 响应校验

`PerformAction` 返回后，MCP 对回显进行三重校验：

```python
if response.code != 0:
    return REJECTED       # EDI 未受理

if response.client_uuid != client_uuid:
    return PROTOCOL_MISMATCH  # client_uuid 不匹配

if response.task_id != task_id:
    return PROTOCOL_MISMATCH  # task_id 不匹配

if response.event_type not in (EVENT_TYPE_UNSPECIFIED, task_type):
    return PROTOCOL_MISMATCH  # event_type 不匹配
```

#### 消费事件流

三重筛选（`client_uuid + task_id + event_type` 全部匹配才处理）：

```python
for event in event_stream:
    if event.client_uuid != client_uuid:
        continue
    if event.task_id != task_id:
        continue
    if event.event_type != task_type:
        continue

    details, parse_error = _parse_payload_json(event.payload_json)

    # 增量收集 ads_output 日志（不 strip、不覆写）
    chunk = details.get("ads_output", "")
    if chunk:
        ads_output_chunks.append(chunk)  # 原样追加

    # 累积 details（后到的覆盖前面的同名字段）
    for key, value in details.items():
        if key != "ads_output":
            latest_details[key] = value

    # 触发异步回调
    if on_event:
        on_event({"phase": "EVENT", "status": status_name, ...})

    if event.status == RESULT_STATUS_SUCCESS:
        # payload 完整性校验：SUCCESS 的 JSON 必须可解析
        if parse_error:
            return PROTOCOL_MISMATCH
        return SUCCESS  # 终态

    if event.status == RESULT_STATUS_FAILED:
        return FAILED  # 终态

# 流结束但无终态
return STREAM_DISCONNECTED
```

#### 异常处理

| gRPC 错误码 | MCP 返回 |
|---|---|
| `DEADLINE_EXCEEDED` | `status: "TIMEOUT"`，保留已收日志 |
| 流建立后断连 | `status: "STREAM_DISCONNECTED"`，保留已收日志 |
| 流建立前断连 | `status: "GRPC_UNAVAILABLE"`（统一字典，不抛异常），message 提示「手动启动 EDI 软件后重试」 |

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

### 1.3 仿真器件参数校验

`_prepare_parameters()` 是仿真器件工具共享的校验管线。它执行 11 步校验，任何一步失败都返回带 `error_code` 的结构化错误：

```
1. 类型检查         parameters 必须是 dict（拒绝 None/[]/"" ）
2. 空值检查         create 允许空字典；update 必须非空
3. 控件存在         检查 component_type 在 catalog 中
4. 参数名解析       先查固定参数 → 再匹配动态模式 Freq[{n}]/Order[{n}]
5. 权限检查         create_allowed / update_allowed
6. 值结构检查      每个参数值必须是 {"value": ..., "unit": ...}
7. value 存在       不能为 null、数组、对象或空字符串
8. 值类型校验       number: 拒绝 NaN/Infinity；integer: 拒绝 1.5；string: 原样透传（可传变量名如 "FREQ"）
9. 枚举校验         如 CalcS 只能 "yes"/"no"
10. 单位检查        required → 必须有 unit；forbidden → 不能有 unit
                    unit 必须是有效非空字符串且在允许列表中
11. 别名冲突        如 Freq + Freq[1] → 两者都映射到 Freq[1] → 报错
输出: wire_params   如 Freq → Freq[1]（公开名→线名转换）
```

**动态参数模式**：`Freq[{index}]` 和 `Order[{index}]` 通过正则匹配支持 1-32 组。用户可以使用快捷名 `Freq`（→ `Freq[1]`）或显式名 `Freq[2]`。两者同时出现时，第 11 步检测重复。

**权限模型**：每个参数显式声明 `create_allowed` 和 `update_allowed`（无隐式默认值）。`BandwidthForNoise` 为 `create_allowed: false, update_allowed: false`（EDI 内部参数，使用默认值）。

### 1.4 工程管理工具

| 工具 | EventType | payload 字段 | 说明 |
|---|---|---|---|
| `open_edi_project` | OPEN_PROJECT(1) | `project_path` | 已有工程窗口时复用 |
| `close_edi_project` | CLOSE_PROJECT(8) | `project_path`, `need_save` | 可选保存 |

### 1.5 仿真工具

#### 同步仿真

`simulate_project` 直接调用 `call_grpc(SIMULATE_PROJECT, ...)`，阻塞等待仿真完成。期间实时收集 `ads_output`。

#### 异步仿真

异步仿真通过内存任务注册表解耦提交和查询：

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

任务生命周期：
```
QUEUED → ACCEPTED → RUNNING → SUCCEEDED / FAILED
                                   ↓
                            2 小时后自动清理（_prune_tasks）
```

**队列上限**：同一 `with _sim_lock` 临界区内完成"统计待处理任务数 → 判断是否超过 8 → 创建新任务"，保证原子性。超限返回 `SIMULATION_QUEUE_FULL`。

**线程安全**：`_sim_lock`（`threading.Lock`）保护 `_sim_tasks` 字典。`_get_task_snapshot()` 在锁内创建浅拷贝，避免并发修改。`_SIM_EXECUTOR(max_workers=1)` 确保同一时间只有一个 gRPC 调用在进行。

**任务快照隔离**：查询状态/结果时，`_get_task_snapshot()` 在锁内创建 `dict(task)` + `list(log_chunks)` 的浅拷贝，然后脱离锁返回。写入方只改原始字典，读取方只看快照。

#### 网表仿真

`simulate_netlist`：
1. MCP 校验 `netlist_path` 存在 → `call_grpc(SIMULATE_NETLIST, {"netlist_path": ...})`
2. EDI 服务：复制网表到临时目录 → 执行 ADS → 复制 `result.raw` 到原网表同级 `history/` → 清理临时目录
3. 返回 `result_path` 和完整 `ads_output`

`simulate_netlist_with_ads`：直接调用 ADS 仿真控制器（`CALL_SIMULATION_CONTROLLER`）。

### 1.6 仿真器件管理（协议 v3）

| 工具 | EventType | 关键实现 |
|---|---|---|
| `create_simulation_component` | CREATE(11) | **v3 不传 parameters**。使用 EDI 工厂默认值创建，创建后根据 `instance_name` 调用 `update_simulation_component` 设参 |
| `update_simulation_component` | UPDATE(15) | **三路类型推断**：显式 component_type > 磁盘查找 > `COMPONENT_TYPE_REQUIRED` 错误。显式类型与实际磁盘类型不一致返回 `COMPONENT_TYPE_MISMATCH` |
| `delete_simulation_component` | DELETE(12) | **不做本地预检查**。直接由 EDI 按 instance_name 执行。通用删除，不限器件类型 |
| `set_component_active_state` | SET_ACTIVE_STATE(14) | 状态规范化 `.strip().upper()`。确定性设置（非切换），重复调用具有幂等性 |
| `generate_schematic_from_netlist` | GENERATE(13) | **双重确认**：`clear_before_import=true` 必须同时 `confirm_clear=true`。`confirm_clear` 不进入 gRPC payload。默认追加到 main 原理图 |
| `replace_port_component` | REPLACE_PORT_COMPONENT(16) | payload: `project_path`, `target_instance_name`, `replacement_component_type`(TermG/P_nToneG), `parameters`。Chat 层需确认 |
| `attach_out_component` | ATTACH_OUT_COMPONENT(17) | payload: `project_path`, `target_instance_name`, 可选 `pin_index`。自动创建 Out 器件并连线 |
| `list_schematic_components` | LIST_SCHEMATIC_COMPONENTS(18) | payload: `project_path`。返回原理图全部器件及完整参数 |
| `get_schematic_component_info` | GET_SCHEMATIC_COMPONENT_INFO(19) | payload: `project_path`, `instance_name`。返回单个器件完整信息 |

**update 类型推断的完整流程**：

```python
explicit_type = component_type.strip() if component_type else ""
component, _ = _find_component_by_instance(project_path, instance_name)
actual_type = component.get("type", "") if component else ""

if actual_type:
    # 磁盘上找到了实例
    if explicit_type and explicit_type != actual_type:
        return COMPONENT_TYPE_MISMATCH  # 显式类型和实际类型冲突
    ct = actual_type  # 使用磁盘上的实际类型

elif explicit_type:
    # 磁盘上没有，但用户提供了类型（如 EDI 中新建但未保存）
    ct = explicit_type  # 信任用户提供的类型

else:
    # 既不在磁盘上，用户也没提供
    return COMPONENT_TYPE_REQUIRED  # 无法确定类型

# 无条件执行完整校验 + wire 转换
wire_params, error = _prepare_parameters(ct, parameters, op="update")
```

### 1.7 导出与模型

| 工具 | EventType | MCP 层校验 |
|---|---|---|
| `export_project_netlist` | VIEW_PROJECT_NETLIST(3) | 仅校验 `project_path` |
| `capture_schematic` | CAPTURE_SCHEMATIC(7) | 额外校验 `img_path` 扩展名（PNG/JPG/BMP/SVG）和路径 resolve |
| `replace_models_from_csv` | MODEL_REPLACE(6) | 额外校验 `csv_path` 存在且后缀 `.csv` |
| `get_model_category_params` | GET_MODEL_CATEGORY_PARAMS(24) | 无业务参数，`payload_json` 为空对象 |
| `search_public_models` | SEARCH_PUBLIC_MODELS(25) | `sub_type` 非空；`filters` 数组原样转发 |
| `search_personal_models` | SEARCH_PERSONAL_MODELS(26) | 同 `search_public_models` |
| `search_schematic_from_public_library` | SEARCH_SCHEMATIC_FROM_PUBLIC_LIBRARY(38) | `search_name` 必需（拓扑描述关键词） |
| `search_schematic_from_personal_library` | SEARCH_SCHEMATIC_FROM_PERSONAL_LIBRARY(39) | 同公共库 |
| `use_schematic_from_library_create_project` | USE_SCHEMATIC_FROM_LIBRARY_CREATE_PROJECT(36) | `file_uuid` 非空 |
| `use_schematic_from_library_import` | USE_SCHEMATIC_FROM_LIBRARY_IMPORT(37) | `file_uuid` 非空 + `project_path` 校验 |
| `export_schematic_components_to_csv` | EXPORT_SCHEMATIC_COMPONENTS_TO_CSV(40) | `save_path` 非空 |

### 1.8 工作区 / 原理图扩展（9 个工具）

| 工具 | EventType | MCP 层校验 |
|---|---|---|
| `list_ideal_components` | LIST_IDEAL_COMPONENTS(27) | 无业务参数，`payload_json` 为空对象 |
| `create_workspace` | CREATE_WORKSPACE(28) | `path` 非空 |
| `switch_workspace` | SWITCH_WORKSPACE(29) | `path` 非空 |
| `load_performance_component_from_mms` | LOAD_PERFORMANCE_COMPONENT_FROM_MMS(30) | `original_uuid` 非空 |
| `add_performance_component` | ADD_PERFORMANCE_COMPONENT(31) | `component_uuid` 非空 + `position` 有限数值校验 |
| `add_ideal_component` | ADD_IDEAL_COMPONENT(32) | `component_type` 非空 + `position` 校验 |
| `clear_schematic` | CLEAR_SCHEMATIC(33) | `confirm_clear=true` 双重确认（MCP 层） |
| `add_wire` | ADD_WIRE(34) | 5 字段必填 + `pin_index` 0~2147483647 整数 |
| `get_current_workspace` | GET_CURRENT_WORKSPACE(35) | 无业务参数，`payload_json` 为空对象 |

---

## 二、本地文件读取（6 个工具）

这些工具不经过 gRPC，直接读取磁盘上的 `.epp` 工程文件。核心实现在 `servers/eda/project_reader.py`。

### 2.1 .epp 工程格式

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

### 2.2 S-expression 解析器

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

解析器状态机：

```
pos=0
├─ 跳过空白
├─ '(' → 递归解析子表达式直到 ')' → 返回列表
├─ '"' → 读取引号字符串（处理 \", \n, \t 转义）→ 返回字符串
└─ 其他 → 读取裸词直到空白或括号 → 返回字符串
```

**关键安全措施**：`ProjectReader.read_schematic(name)` 拒绝 `..` 和路径分隔符：

```python
if ".." in name or "/" in name or "\\" in name:
    return None
```

### 2.3 paramsinfo 解析

原理图里的 `(paramsinfo "...")` 节点包含 JSON 格式的参数信息。存在两种结构：

- **普通参数**：`{"Value": "50", "CurrentUnit": "Ohm", "Tunable": "false"}`
- **Var 变量**：`{"Initial": "29", "Max": "", "Min": "", "Status": "Disable"}`

`parse_paramsinfo(raw)` 将这两种结构统一为小写 key 的字典：

```python
{
    "R": {
        "value": "50",          # 来自 Value 或 Initial
        "unit": "Ohm",          # 来自 CurrentUnit 或 DefaultUnit
        "default_unit": "Ohm",
        "tunable": False,
        "visible": True,
        "initial": "",
        "max": "", "min": "",
        "status": "",
    }
}
```

`parse_components(schematic_text)` 从 S-expression 提取所有 `(component ...)` 节点，并调用 `parse_paramsinfo` 解析参数信息。返回值直接包含已解析的 `paramsinfo` 字典——下游不应再次调用 `parse_paramsinfo`。

### 2.4 参数格式化

`_format_component_parameters(component_type, paramsinfo)` 将 `parse_components` 返回的已解析字典转换为面向用户的格式：

- 直接从 `paramsinfo["key"]["value"]` 读取，**不重复解析**
- 无单位参数不返回 `"unit"` 键（不存在，不是空字符串）
- wire 参数名反向映射为公开名：`Freq[1]` → `Freq`

```python
# 输入: {"Freq[1]": {"value": "1.0", "unit": "GHz"}, "Pts": {"value": "101"}}
# 通过 _from_wire_parameters() 反向映射
# 输出: {"Freq": {"value": "1.0", "unit": "GHz"}, "Pts": {"value": "101"}}
```

**wire→public 反向映射规则**：
1. 查固定参数的 `wire_name` → `public_name` 映射（`Freq[1]` → `Freq`）
2. 如果是动态模式且不在固定映射中（`Freq[2]`），匹配正则生成 `Freq[2]` → `Freq[2]`
3. 无法映射的保持原样

### 2.5 工具列表

| 工具 | 读取内容 | 特点 |
|---|---|---|
| `list_epp_projects` | 文件夹扫描 | `rglob("*.epp")`，最多 1000 个，返回名称/路径/大小 |
| `get_project_summary` | metadata + schematics + netlist + RAW | 聚合：元数据、原理图列表、元件类型分布、仿真器件配置、最新 RAW 文件 |
| `analyze_variables` | 所有原理图 | 识别 Var 元件定义 → 找到引用该变量的参数 → 列出 Sweep 配置 |
| `list_simulation_components` | 所有原理图 | `parse_components` → 支持类型/名称模糊/原理图过滤 + 分页；SP/HB/XDB 做 wire→public 映射 |

---

## 三、内存服务（4 个工具）

读内存/运行时状态的工具，不调 gRPC、不读磁盘文件：

| 工具 | 数据源 | 说明 |
|---|---|---|
| `get_simulation_async_status` | `_sim_tasks` 内存注册表 | 查询异步仿真进度和增量日志 |
| `get_simulation_async_result` | `_sim_tasks` | 获取异步仿真最终结果 |
| `list_eda_tasks` | `_sim_tasks` | 列出当前异步仿真任务 |
| `get_service_status` | gRPC channel 缓存 + queue busy 标志 | 诊断通道健康度和队列占用 |

---

## 四、参数目录与 Schema

参数目录 `simulation_component_catalog.json`（v2.0.0）是 MCP 层对 EDI 参数知识的本地编码。它不替代 EDI 的校验，而是让 **MCP 在本地完成大部分参数校验**，减少无效 gRPC 调用和错误轮次。

### 3.1 设计动机

- **AI 友好**：`get_simulation_component_schema` 返回每个参数的类型、单位、权限——AI 不需要猜测参数名或单位格式
- **校验前置**：90% 的参数错误在 MCP 层就能被拦截（非法参数名、错误单位、类型不匹配）
- **权限控制**：`BandwidthForNoise` 这种内部参数直接标记为不可设置，AI 永远不会尝试传它
- **wire 透明**：AI 使用公开名（`Freq`），MCP 自动转换为 EDI 需要的线名（`Freq[1]`）

### 3.2 核心函数

| 函数 | 输入 | 输出 | 逻辑 |
|---|---|---|---|
| `_load_catalog()` | — | `dict` | `@lru_cache(maxsize=1)` 加载 JSON，失败返回 `{}` |
| `_resolve_parameter_schema(name)` | `"Freq"` | `(schema_dict, "Freq[1]")` | 先查 `parameters` 字典 → 再用正则匹配 `parameter_patterns` → 提取 index → 验证范围 |
| `_prepare_parameters(op, allow_empty)` | 公开参数 dict | `(wire_dict, None)` 或 `(None, error_dict)` | 11 步校验管线（见 §1.3） |
| `_to_wire_parameters()` | `{"Freq": ...}` | `{"Freq[1]": ...}` | 逐参数调 `_resolve_parameter_schema` |
| `_from_wire_parameters()` | `{"Freq[1]": ...}` | `{"Freq": ...}` | 固定映射优先 → 动态正则补全 → 不可映射的保持原样 |
| `_find_component_by_instance(path, name)` | `"C:/test.epp"`, `"HB1"` | `(component_dict, None)` 或 `(None, error_dict)` | 遍历所有原理图，按 `name` 精确匹配。多处返回 `AMBIGUOUS_INSTANCE_NAME` |

### 3.3 动态参数模式

HB 和 XDB 支持多音设置，需要多组 `Freq[n]`/`Order[n]`。目录用 `parameter_patterns` 表达：

```json
{
  "public_pattern": "Freq[{index}]",
  "wire_pattern": "Freq[{index}]",
  "index_min": 1,
  "index_max": 32,
  "value_type": "string",
  "unit_required": true,
  "units": ["Hz", "kHz", "MHz", "GHz"],
  "create_allowed": true,
  "update_allowed": true
}
```

解析器将 `{index}` 替换为 `(\d+)` 生成正则，匹配 `Freq[1]` ~ `Freq[32]`。同时保留固定映射 `Freq → Freq[1]` 作为快捷方式。

> 连续量参数（`Freq`/`Freq[{index}]`、`Start`/`Stop`/`Step`、`GC_XdB`/`GC_InputFreq`/`GC_OutputFreq`/`GC_InputPowerTol`/`GC_OutputPowerTol`/`GC_MaxInputPower`、`GroupDelayAperture`）的 `value_type` 为 `string` 而非 `number`：允许设置变量引用（如 `"FREQ"` 跟随 ParamSweep 扫频），数字串（如 `"2"`）也照常透传，空字符串会被拒绝。仅离散量（`Pts`、端口号、`Order`、`StatusLevel`）保持 `integer`，`CalcS` 等保持 `boolean`。

---

## 五、subprocess 命令行（4 个工具）

### 4.1 TurboCharts

`turbocharts_app.exe` 是 EDI 套件中的命令行工具，负责将 ADS RAW 仿真结果转换为曲线图和 CSV。MCP 通过 `subprocess.run` 调用它。

**串行化执行器**（`servers/turbocharts/config.py`）：

```python
_TURBOCHARTS_SEMAPHORE = threading.BoundedSemaphore(1)

def run_turbocharts(command, timeout_seconds=120):
    with _TURBOCHARTS_SEMAPHORE:  # 一次只运行一个 turbocharts 进程
        return subprocess.run(
            list(command),
            capture_output=True, text=True,
            timeout=timeout_seconds,
            creationflags=CREATE_NO_WINDOW,  # Windows: 不弹命令行窗口
        )
```

**命令行构造**（`turbocharts_convert`）：

```python
cmd = [TURBOCHARTS_PATH, "--raw", raw_path, "--img", img_path, "--type", chart_type]
if csv_path:   cmd.extend(["--csv", csv_path])
if linename:   cmd.extend(["--linename", linename])
if dependency: cmd.extend(["--dependcy", dependency])  # 注意：程序的参数名就是 --dependcy
if ac_config:  cmd.extend(["--ac", ac_config])
```

关键点：
- `--dependcy` 是 `turbocharts_app.exe` 的真实参数名（少一个 n，非拼写错误）
- MCP 层校验 `img_path` 扩展名（PNG/JPG/BMP/SVG）
- `timeout_seconds` 范围 1-600

### 4.2 RAW 曲线查询

`list_result_curves` 解析 ADS RAW 文件头部，返回可用曲线名。支持两种格式：

**MDS 格式**（EDI 当前使用的格式）：

```
File Format: MDS
Plotname: SP SP1[1] freq=(1 GHz->10 GHz)
No. Variables: 11
Variables:
    0 freq frequency type=real indep=yes
    1 S[1,1] s-param type=complex indep=no
    2 S[2,1] s-param type=complex indep=no
Values:
    0 1e9
    1 0.5-0.1j
```

解析器状态机：
```
逐行读取:
├─ "Plotname:" → 保存前一个 dataset，创建新 dataset
│   └─ 从 plot_name 中提取 freq=(...) → dependencies
├─ "Variables:" → 进入变量读取模式
├─ "Values:" → 退出变量读取模式（停止收集变量）
├─ 变量行（在 Variables: 和 Values: 之间）:
│   └─ 解析: index, name, description, type=real/complex, indep=yes/no
│       ├─ indep=yes → dependencies
│       └─ indep=no → _suggest_curves() 生成推荐曲线名
```

**XML 格式**：

```xml
<Dataset name="SP1">
  <Number name="freq" type="real"/>
  <Complex name="S(1,1)"/>
  <Complex name="S(2,1)"/>
</Dataset>
```

解析器用正则 `<Number|Complex|Real name="...">` 提取，标签名决定类型：`Complex`→complex，`Number`/`Real`→real。

**截断保护**：当前读取前 65536 字节。如果读取量达到上限，返回中包含 `warning` 字段提示结果可能不完整。

**曲线推荐规则**（`_suggest_curves`）：

| 变量类型 | 生成的曲线名 | 约束 |
|---|---|---|
| `S.delay[x,y]` (real) | `real_delayS[x,y]` | **最高优先级**，先于通用 real 判断 |
| real 类型 | `real_{name}` | |
| complex S[n,n] | `DB_S[n,n]`, `real_S[n,n]`, `phase_S[n,n]`, `VSWR_S[n,n]` | 反射参数加 VSWR |
| complex S[n,m] (n≠m) | `DB_S[n,m]`, `real_S[n,m]`, `phase_S[n,m]` | 传输参数不加 VSWR |
| 其他 complex | `DB_{name}`, `real_{name}`, `phase_{name}` | |

### 4.3 仿真结果对比

`compare_simulation_results` 对比 2-8 个 RAW 文件中同一条曲线：

```
1. 逐 RAW 调 turbocharts 导出临时 CSV
2. _read_curve_csv_xy() 读取 x/y 数据
3. 对齐策略:
   - "intersection": 取所有文件共有的 x 数据点
   - "interpolation": 以 reference_index 为基准插值
4. 计算差异指标（每对曲线的 max_abs_diff, mean_abs_diff, rms_diff）
5. Matplotlib 生成对比叠图
6. 可选导出对比 CSV
```

### 4.4 EDI 启动

`launch_edi`：

```
1. TCP connect 检查 gRPC 端口是否已就绪 → 已运行则跳过
2. subprocess.Popen([EDI.exe], cwd=EDI目录)
3. 轮询 TCP connect 等待 gRPC 就绪（默认 30 秒）
```

---

## 六、COM 对象与 CST 官方接口（ANSYS 6 个 + CST 5 个）

核心实现在 `servers/ansys/config.py`（进程检测/COM 附着/锁文件管理）。

### 5.1 COM 附着

通过 Windows COM 附着到已运行的 AEDT 实例，或启动新实例：

```python
_COM_PROGIDS = ("AnsoftHfss.HfssScriptInterface", "Ansoft.ElectronicsDesktop")

def _attach_aedt():
    for progid in _COM_PROGIDS:
        try:
            app = GetActiveObject(progid)
            return app, app.GetAppDesktop()
        except Exception:
            continue
    raise RuntimeError(f"GetActiveObject failed")
```

每个 COM 调用都在独立的 `pythoncom.CoInitialize()` / `CoUninitialize()` 上下文中执行。

### 5.2 AEDT 检测

```python
def aedt_is_running():
    return len(get_aedt_pids()) > 0

def get_aedt_pids():
    # psutil 遍历进程，匹配 ansysedt.exe
```

`_find_aedt()` 的搜索路径：环境变量 `AEDT_PATH` → Windows 注册表（`SOFTWARE\...\Uninstall` 找 ANSYS 安装位置）→ 默认目录（`C:\Program Files\AnsysEM\` 下按版本号排序取最新）。

### 5.3 锁文件管理

AEDT 打开工程时创建 `.aedt.lock`，内有一行 `DesktopProcessID=<pid>`。MCP 层的清理策略：

```
cleanup_stale_project_lock(project_path):
  ├─ 读取 .aedt.lock → 提取 DesktopProcessID
  ├─ PID 在进程列表中 → lock_active（拒绝删除）
  ├─ PID 不在进程列表中 → stale_lock_removed（安全删除）
  └─ 无法解析 PID → lock_unknown_format（保留）
```

**安全原则**：PID 存活时绝不删除锁文件。只有当进程确实已退出时才清理残留锁。

### 5.4 工具

| 工具 | 实现 |
|---|---|
| `open_hfss_project` | 清理失效锁 → 如 AEDT 已运行则 COM 附着 `OpenProject` / `SetActiveProject`；否则 `subprocess.Popen(ansysedt.exe project)` → 轮询 COM 验证 |
| `close_hfss_project` | COM `CloseProject`（可选 `Save` 前保存）→ 等待 2 秒让 AEDT 自己删锁 → 检查并清理残留锁 |
| `launch_aedt` | 已运行则返回状态；否则 subprocess 启动 → 轮询 COM 就绪 |
| `get_hfss_project_info` | COM 附着 → 读取 `GetProjectList`, `GetActiveProject`, `GetActiveDesign` |
| `start_hfss_analysis_async` | 验证 AEDT 运行 + setup 存在 → `hfss_runner.submit(..., require_idle=True)` 原子提交 → worker 执行 `design.Analyze(setup)` → 校验结果目录含结果文件 |
| `get_hfss_analysis_status` | 读取 `hfss_runner.snapshot`，可选 `refresh_from_aedt` 查询 `AreThereSimulationsRunning` |

HFSS 任务队列：复用通用 `TaskRunner`（`servers/task_runner.py`，单 worker 串行）；单任务互斥通过 `submit(require_idle=True)` 原子「检查+提交」实现。

### 5.5 CST 官方 Python 接口

CST 工具不走 ANSYS COM，而是用 CST 官方 Python 接口（`servers/cst/cst_api.py`，通过注册表定位安装路径）：

| 模块 | 性质 | 用途 |
|---|---|---|
| `cst.interface` | COM 自动化（连接/启动 CST） | `connect_to_any_or_new()` 打开会话、`run_solver`、`execute_vba_code` 导远场 |
| `cst.results` | 只读结果 API（无会话） | `ProjectFile(...).get_3d().get_tree_items()` 读结果树、判断是否已求解、读 S 参数 |

5 个工具按通信方式分两类：

| 工具 | 通信方式 |
|---|---|
| `cst_solve_async` / `cst_solve_query` | `cst.interface` 会话（run_solver） |
| `cst_export_snp` | `cst.results` 无会话读结果 |
| `cst_export_farfield` / `cst_export_farfield_query` | 混合：`cst.results` 检测已求解 + `cst.interface` 会话 execute_vba 导出 |

求解与远场导出共用全局串行队列 `cst_runner`（`TaskRunner`，见 `servers/cst/cst_api.py`），CST 一次只能跑一个会话。

---

## 七、图片工具（2 个）

### 6.1 show_image

```
1. _validate_image_path(path):
   ├─ expanduser + resolve（阻止 .. 遍历）
   ├─ 拒绝网络路径（\\ 和 //）
   ├─ 检查文件存在
   └─ 检查扩展名（.png/.jpg/.jpeg/.gif/.webp/.bmp）
2. 文件大小判断:
   ├─ ≤10MB → Base64 编码 → 返回 ImageContent + TextContent
   └─ >10MB → 只返回 TextContent（本地路径 + 查看建议）
```

### 6.2 copy_image_to_workspace（已隐藏）

当前不使用 OpenClaw，该工具已暂时隐藏（`OPENCLAW_WORKSPACE_PATH` 恒为 `None`，不检测工作区）。原条件注册逻辑：`OPENCLAW_WORKSPACE` 有效时支持 `.env` 配置或自动检测（edi-mcp 同级 `rfclaw/openclaw-service/state/workspace`，回退到 `~/.openclaw/workspace`），复制到 `{workspace}/media/edi/mcp-cache/`。

返回关键字段：
- `media_path`：相对工作区的路径（如 `media/edi/mcp-cache/S11.png`），客户端可直接用于 MEDIA 指令
- `media_type`：MIME 类型（如 `image/png`）

其他细节：
- 文件名 = `{安全源文件名}_{MD5前8位}{扩展名}`（稳定文件名，同一源文件总是相同目标）
- 上限 40MB
- 超过 24h 的缓存自动清理
- `show_image` 成功时不会自动调用此工具

### 6.3 临时图片 HTTP 服务

`/images/{token}` 路由，10 分钟过期。供 Chat 界面渲染 `show_image` 返回的图片。不依赖 OpenClaw 工作区。

### 6.4 视觉分析（analyze_image）

调用配置的视觉模型分析本地图片内容。核心实现在 `servers/multimodal_vision/vision_analyzer.py`。

**与 show_image 的区别**：
- `show_image` → 把图片返回给 MCP 客户端渲染（不调用模型）
- `analyze_image` → 调用第三方视觉模型，返回结构化文字分析

**配置**（独立于聊天 LLM 配置）：
```ini
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
VISION_TIMEOUT_SECONDS=45
VISION_MAX_IMAGE_MB=10
```

**调用流程**：
```
1. 配置检查 → 三项全部非空即开启
2. 图片校验 → 路径/扩展名/Pillow 内容验证/大小限制
3. Base64 编码 → data:image/png;base64,...
4. 并发控制 → BoundedSemaphore(2)，超限返回 VISION_BUSY
5. POST /v1/chat/completions（OpenAI 兼容 Vision API）
   └─ system prompt 防止图片提示注入
6. 响应解析 → 提取 analysis/usage，返回结构化结果
```

**安全措施**：
- 未置三项时返回 VISION_NOT_CONFIGURED
- 工具描述明确告知图片会被上传
- system prompt 声明图片中文字不执行
- 日志只记录 HTTP 状态/耗时/模型名/图片大小，不记 Base64/API Key/分析内容
- 返回 `content_is_untrusted: true` 提醒外层 Agent
- 不依赖 OPENCLAW_WORKSPACE
- show_image 成功后不自动调用

**错误码**（10 个）：
`VISION_NOT_CONFIGURED` `IMAGE_NOT_FOUND` `UNSUPPORTED_IMAGE_FORMAT`
`IMAGE_TOO_LARGE` `INVALID_IMAGE` `VISION_TIMEOUT`
`VISION_AUTH_FAILED` `VISION_RATE_LIMITED` `VISION_BUSY`
`VISION_PROVIDER_ERROR` `INVALID_VISION_RESPONSE`

---

## 八、Chat 聊天服务

核心实现在 `servers/chat/service.py`。支持 LLM 多轮工具调用闭环。

### 7.1 会话管理

```python
@dataclass
class ChatSession:
    session_id: str
    messages: list[dict]               # OpenAI 格式消息历史
    current_project_path: str | None   # 当前选定工程
    current_project_name: str | None
    last_folder_path: str | None       # 最近扫描目录
    last_projects: list[dict]          # 最近工程列表
    last_simulation_task_id: str | None
    simulation_task_ids: list[str]     # 历史任务（最近 20 个）
    updated_at: float
```

会话 TTL 2 小时，全局上限 100 个。每 5 分钟清理一次过期会话。

### 7.2 多轮闭环

```
用户消息 → ChatService.chat(session_id, message)
  ├─ system prompt（动态注入当前工程/任务上下文）
  ├─ 最多 5 轮:
  │   ├─ POST {LLM_BASE_URL}/v1/chat/completions
  │   │   └─ tools: CHAT_TOOLS_SCHEMA（从 MCP 元数据自动生成，排除同步/COM 工具后通常 32 个）
  │   ├─ 无 tool_calls → 返回最终回复
  │   └─ 有 tool_calls:
  │       ├─ _validate(tool_name, args, session):
  │       │   ├─ 未知工具 → 拒绝
  │       │   ├─ args 不是 dict → 拒绝
  │       │   ├─ 必填字段空字符串 → 拒绝
  │       │   ├─ project_path → 从 session 上下文自动补齐
  │       │   └─ task_id → 从 session.last_simulation_task_id 自动补齐
  │       ├─ 重复调用保护: fingerprint = f"{tool}:{sorted_json(args)}"
  │       ├─ asyncio.to_thread(执行工具函数)
  │       ├─ _update_context(): 更新 session 的工程/任务状态
  │       └─ 工具结果序列化 → 作为 tool message 交回 LLM
  └─ 超 5 轮 → 返回错误
```

### 7.3 工具 Schema 构建

Chat 工具列表和 Schema 从 MCP 注册表自动生成（`_auto_build_chat_tools()`），不再手工维护两套列表。FastMCP 的 `t.parameters` 已是标准 JSON Schema，直接用作 OpenAI function-calling 参数定义，永远不会和函数签名不一致。

### 7.4 Web 路由

| 路由 | 功能 |
|---|---|
| `GET /ui` | 返回单页聊天应用（工具面板 + 聊天界面，支持 📎 文件上传） |
| `GET /health` | TCP 检测 gRPC 端口 + 检查 `turbocharts_app.exe` 存在 → JSON |
| `POST /chat` | `{session_id, message}` → `ChatResponse(reply, activities, media, context)` |
| `POST /upload` | multipart/form-data → 保存到 `%TEMP%/mcp/uploads/`，返回本地路径 |
| `GET /tools/list` | MCP 工具名和描述 JSON 列表 |
| `GET /images/{token}` | 临时图片访问（10 分钟过期，inline 渲染） |

### 7.5 安全加固

**会话锁**：同 session 并发请求串行化（`asyncio.Lock`），10 秒超时返回提示。

**输入限制**：
- 单条消息最大 20K 字符
- session_id 最大 128 字符
- 单轮工具调用最多 8 个
- 工具返回结果截断至 100K 字符

**破坏性工具确认门**（`delete_simulation_component`、`replace_models_from_csv`）：
- 模型请求执行破坏性工具时，Chat 层拦截并保存 `PendingAction`
- 向用户展示操作摘要和影响范围
- 用户需明确回复"确认"后才执行
- 一次确认只能执行一次，5 分钟过期
- 确认执行时使用原始保存参数，防止模型在确认前后篡改目标

**默认值透明**（产生文件 / 采用默认值时先告知用户）：
- 内置 Chat 的 system prompt 规则 11：产生输出文件（截图/图表/报告）或采用默认值时，先告知用户输出位置/默认值并询问是否调整
- 外部 agent（OpenClaw 等）通过 FastMCP `instructions` 下发同一规则（告知，暂不强制询问，避免打断 agent 自动执行流程）
- `edi://reference/operation-guide` Resource 也列出该规则，供 agent 主动读取

---

## 九、Resources 与 Prompts

MCP 协议除了 Tool，还定义了 Resource（只读上下文）和 Prompt（可复用工作流模板）。实现在 `servers/resources_prompts/`（按语义拆为 resources_service / resources_reference / prompts_project / prompts_simulation / prompts_component / prompts_report 六个文件）。

### 8.1 Resources

通过 `@mcp.resource(uri, mime_type=...)` 装饰器注册。客户端通过 `resources/list` 和 `resources/read` 访问。

| URI | MIME | 内容来源 | 关键字段 |
|---|---|---|---|
| `edi://service/overview` | `application/json` | 动态生成 | `server_version`, `protocol_version`, `grpc_target`, `workspace_copy_enabled`, `safety_rules` |
| `edi://reference/simulation-components` | `application/json` | `_load_catalog()` | 与 `get_simulation_component_schema` 同源 |
| `edi://reference/operation-guide` | `text/markdown` | 静态维护 | 操作安全约束（创建/删除/网表导入/默认值透明等） |
| `edi://service/status` | `application/json` | 动态生成 | 与 `get_service_status` 同源（gRPC 通道、队列占用） |
| `edi://reference/error-codes` | `text/markdown` | 静态维护 | gRPC 状态码词典及建议动作 |

`workspace_copy_enabled` 的判断逻辑：

```python
from servers.multimodal_vision import OPENCLAW_WORKSPACE_PATH
workspace_enabled = OPENCLAW_WORKSPACE_PATH is not None
```

直接复用 `workspace_copy.OPENCLAW_WORKSPACE_PATH`，保证 Resource 返回值与实际工具注册状态一致（不依赖环境变量字符串解析）。

### 8.2 Prompts

通过 `@mcp.prompt(name=..., title=..., description=...)` 装饰器注册。返回 `list[Message]`（`role` + `content`）。客户端通过 `prompts/list` 和 `prompts/get` 访问。

**设计原则**：
- Prompt 只返回**消息模板**，不直接执行工具
- 非法参数直接拒绝，不静默回退（`action="bad"` → 返回错误消息，不变成 `create`）
- `update` 缺 `instance_name` 拒绝
- 仿真 Prompt 包含轮询限制（最多查询一次，间隔 ≥10s，单次最多 3 次）
- 核心工作流必须只靠 Tools 也能完成（Resource 是增强，不是依赖）

---

## 十、文档工具（1 个工具）

`open_document` 实现在 `servers/multimodal_vision/document.py`，支持 link / local 两种模式。

**`open_document`**：打开本地文档。
- `mode="link"`（默认）：为本地文档生成 10 分钟临时 HTTP 链接
  - Token 映射 `/documents/{token}`，10 分钟过期
  - 链接内保存 `disposition` 参数，PDF inline 预览、DOCX attachment 下载
- `mode="local"`：使用 `os.startfile()` 调用系统默认程序打开
  - 支持 10 种格式（.pdf/.doc/.docx/.xls/.xlsx/.ppt/.pptx/.txt/.csv/.rtf）
- 安全：UNC 拒绝、绝对路径校验、nosniff 头
- 工具描述明确"仅用户要求时调用，生成报告后不得自动打开"

---

## 十一、报告渲染（1 个工具）

`generate_simulation_report` 生成本地仿真报告（PDF/DOCX）。核心实现在 `servers/report/generator.py`。

**调用流程**：
```
1. 输出路径校验 → 绝对路径/后缀 .pdf .docx/父目录存在/overwrite 覆盖检查
2. model_name 校验 → 非空，最长 200 字符
3. spec_table 校验 → 二维数组/每行列数一致/单元格类型(string int float)
4. charts 校验 → 每项含 path(绝对/PNG/JPG)+title，缺失图片记 warning
5. components 校验 → 四项全字符串
6. schematic 校验 → 绝对路径，缺失记 warning
7. 构建 report payload → POST REPORT_RENDER_URL
8. HTTP 状态映射 → 400(REPORT_VALIDATION_FAILED) 409(OUTPUT_FILE_BUSY) 500(REPORT_RENDER_FAILED)
9. 服务不可用 → REPORT_SERVICE_UNAVAILABLE
10. 验证输出文件存在 → REPORT_OUTPUT_NOT_FOUND
```

**配置**：
```ini
REPORT_RENDER_URL=http://127.0.0.1:17867/api/v1/reports/render
REPORT_RENDER_TIMEOUT_SECONDS=45
```

**设计原则**：
- 只校验数据并调用渲染 API，不自动执行仿真、生成曲线或编造指标
- 默认禁止覆盖（`overwrite=false`），防止意外覆盖已有报告
- 器件厂家和规格不得猜测，无来源时留空
- 服务不可用时工具仍注册，返回 `REPORT_SERVICE_UNAVAILABLE`
- Chat Schema 自动生成，新增 MCP 工具无需手动同步到 Chat 层（排除列表 `_CHAT_EXCLUDED_TOOLS` 除外）

**11 个错误码**：`INVALID_OUTPUT_PATH` `OUTPUT_DIRECTORY_NOT_FOUND`
`OUTPUT_ALREADY_EXISTS` `INVALID_REPORT_PARAMETERS` `INVALID_CHART_PATH`
`REPORT_SERVICE_UNAVAILABLE` `REPORT_RENDER_TIMEOUT` `REPORT_VALIDATION_FAILED`
`OUTPUT_FILE_BUSY` `REPORT_RENDER_FAILED` `INVALID_REPORT_RESPONSE`
`REPORT_OUTPUT_NOT_FOUND`

---

## 十二、跨层设计原则

### 11.1 参数校验分层

AI 的请求经过 5 层校验才能到达 EDI 服务端，每一层拦截不同类别的错误：

```
Chat _validate()           空字符串拒绝、project_path/task_id 自动补齐
  ↓
工具函数入口              路径校验（validate_project_path/validate_file + resolve）
  ↓
_prepare_parameters()      类型/单位/权限/wire 名/别名冲突（MCP 本地）
  ↓
call_grpc()                JSON 序列化、超时控制、锁获取
  ↓
EDI gRPC 服务             最终业务校验和执行
```

### 11.2 错误码体系

| 层级 | 字段 | 来源 | 示例 |
|---|---|---|---|
| MCP 本地校验 | `error_code` | `_param_error()` / `_component_error()` | `INVALID_PARAMETERS`, `COMPONENT_NOT_FOUND`, `DUPLICATE_PARAMETER_ALIAS`, `CLEAR_CONFIRMATION_REQUIRED` |
| gRPC 通信层 | `status` | `_terminal_result()` | `SUCCEEDED`, `FAILED`, `TIMEOUT`, `STREAM_DISCONNECTED`, `GRPC_UNAVAILABLE`, `PROTOCOL_MISMATCH` |

两者的语义不同：`error_code` 表示已知的错误类型（AI 可以据此修正参数重试），`status` 表示通信终态（指示是否需要人工介入）。

完整的 `error_code` 列表（18 个）：

```
COMPONENT_SCHEMA_UNAVAILABLE    UNSUPPORTED_COMPONENT_TYPE
UNSUPPORTED_PARAMETER           CREATE_PARAMETER_NOT_ALLOWED
UPDATE_PARAMETER_NOT_ALLOWED    INVALID_PARAMETERS
INVALID_PARAMETER_VALUE         MISSING_VALUE
INVALID_VALUE                   INVALID_ENUM_VALUE
MISSING_UNIT                    UNSUPPORTED_UNIT
DUPLICATE_PARAMETER_ALIAS       EMPTY_INSTANCE_NAME
COMPONENT_NOT_FOUND             AMBIGUOUS_INSTANCE_NAME
COMPONENT_TYPE_MISMATCH         COMPONENT_TYPE_REQUIRED
INVALID_ACTIVE_STATE            CLEAR_CONFIRMATION_REQUIRED
FILE_NOT_FOUND                  INVALID_STATUS
SIMULATION_QUEUE_FULL           TASK_NOT_FOUND
```

### 11.3 并发控制

| 资源 | 锁类型 | 原因 |
|---|---|---|
| EDA gRPC 操作 | `threading.RLock` 全局锁 | EDI 进程同一时间只能做一件事。可重入锁确保同一个线程可以嵌套调用 |
| 异步仿真任务（EDA） | `threading.Lock` | 保护 `_sim_tasks` 字典的读写 |
| CST / HFSS 任务队列 | `TaskRunner`（单 worker 线程池 + 内部锁） | CST/AEDT 单实例，同一时间只能跑一个求解/导出 |
| TurboCharts 进程 | `BoundedSemaphore(1)` | 一次只能运行一个 turbocharts 实例 |
| ANSYS COM 操作 | `threading.RLock` | 保护 AEDT COM 对象访问 |
| ANSYS 工程路径 | 无锁（低频操作） | `_OPEN_PROJECT_PATHS` 字典，并发冲突概率极低 |
| Chat 会话 | `threading.Lock` | 保护 `_sessions` 字典的读写 |
| 图片 token | `threading.RLock` | 保护 `_IMAGE_TOKENS` 字典（注册、查询、过期清理） |

### 11.4 向后兼容

协议 v2 不兼容 v1。`UPSERT_SIMULATION_COMPONENT` 已被拆分为 `CREATE_SIMULATION_COMPONENT`(11) + `UPDATE_SIMULATION_COMPONENT`(15)，新增 `GENERATE_SCHEMATIC_FROM_NETLIST`(13) 和 `SET_COMPONENT_ACTIVE_STATE`(14)。EDI 服务、MCP 服务、protobuf 生成代码必须成套升级，禁止新旧混用。

### 11.5 非幂等操作保护

以下操作禁止在 `TIMEOUT`/`STREAM_DISCONNECTED` 后自动重试：

- `create_simulation_component`（可能创建重复器件）
- `generate_schematic_from_netlist` 的追加模式（可能重复导入）
- `generate_schematic_from_netlist` 的清空模式（可能再次清空）

`set_component_active_state` 是确定性设置，可以安全重试。

### 11.6 返回字段语义 — outcome_known / task_success

gRPC 层通过 `_terminal_result()` 统一构建返回结构，关键字段：

```
completed      MCP 侧任务是否结束（线程退出、超时、断连都是 completed=True）
outcome_known  是否收到了 EDI 的最终事件（SUCCEEDED 或 FAILED）
task_success   仅 outcome_known=True 时有意义；None = EDI 实际状态未知
failure_source 异常来源："mcp" 表示 MCP 自身异常，不是 EDI 业务失败
```

语义矩阵：

| 状态 | success | outcome_known | task_success |
|---|---|---|---|
| SUCCEEDED | True | True | True |
| FAILED（EDI） | False | True | False |
| REJECTED | False | True | False |
| TIMEOUT | False | False | None |
| STREAM_DISCONNECTED | False | False | None |
| GRPC_UNAVAILABLE | False | False | None |
| PROTOCOL_MISMATCH | False | False | None |
| MCP 异常 | False | False | None（failure_source="mcp"） |

`_run_sim_task()` 不覆盖 gRPC 层已计算的 `task_success`，
只在字段不存在时从 `outcome_known` 和 `status` 推导。

### 11.7 配置统一

所有环境变量收敛到 `servers/settings.py` → `Settings` dataclass（frozen，lru_cache 单例）。
其他模块通过 `get_settings()` 读取，不再直接调用 `os.getenv()`。

启动时 `start_servers.py` 调用 `settings.validate()`，校验：
- EDA_GRPC_SERVER 格式（host:port）
- gRPC 端口范围（1-65535）
- MCP_TRANSPORT 合法性（streamable-http / stdio）
- MCP_PORT 范围

### 11.8 Chat 破坏性工具确认

注册时标记 5 个破坏性工具，参数感知确认：

| 工具 | 确认条件 |
|---|---|
| delete_simulation_component | 无条件确认 |
| replace_models_from_csv | 无条件确认 |
| replace_port_component | 无条件确认 |
| close_edi_project | need_save=true 时确认 |
| generate_simulation_report | overwrite=true 时确认 |
| generate_schematic_from_netlist | clear_before_import=true 时确认（Chat 层补 confirm_clear） |

确认支持简单肯定词（确认/是/yes/ok/好的），无操作 5 分钟过期。

### 11.9 HFSS 任务管理

- TTL 2 小时自动清理（`_prune_hfss_tasks()`）
- 最大 50 个任务（含历史）
- 统一 `outcome_known` / `task_success` 字段
- TASK_NOT_FOUND 返回 `success=False, outcome_known=False, task_success=None`

### 11.10 工作区自动检测

`OPENCLAW_WORKSPACE` 查找顺序：

1. `.env` 中 `OPENCLAW_WORKSPACE` 配置路径（优先）
2. edi-mcp 同级 `rfclaw/openclaw-service/state/workspace`
3. 用户目录 `~/.openclaw/workspace`（兜底）

（当前已隐藏：不执行检测，`OPENCLAW_WORKSPACE_PATH` 恒为 `None`。）

### 11.11 产物统一格式（artifacts）

所有文件生成工具返回统一的 `artifacts` 数组：

```json
{
  "artifacts": [
    {"type": "image", "path": "C:/.../sp.png", "name": "sp.png", "generated_by": "turbocharts_convert"}
  ],
  "message": "曲线图已生成。"
}
```

适用工具：`turbocharts_convert`、`capture_schematic`、`compare_simulation_results`、`generate_simulation_report`。

`message` 用"已生成"，不用"已显示"——MCP 不保证客户端渲染成功。

### 11.12 重启恢复机制

- `/ready` 端点：初始化中返回 503，完成后返回 200（含 `transport`/`stateless`/`tool_count`/`started_at`）
- 优雅关闭：SIGINT/SIGTERM → 停止接收新请求 → 退出进程
- 生命周期日志：`MCP_STARTING` → `MCP_READY` → `MCP_STOPPING` → `MCP_STOPPED`
- 旧 session_id 返回 `Session not found`，客户端需重新 initialize
- 重启后仿真任务状态丢失（内存），查询旧 task_id 返回 TASK_NOT_FOUND
- 不自动生成文件、不自动修改工程、不自动重放工具调用

### 11.13 日志系统

所有模块统一使用 `logging.getLogger(__name__)`，输出到 `%TEMP%/edi/data/log/edi_mcp_YYYYMM.log`（RotatingFileHandler，10MB × 5 备份）。

| 模块 | Logger | 记录内容 |
|---|---|---|
| `start_servers` | `edi_mcp` | 生命周期：STARTING → READY → STOPPING → STOPPED |
| `grpc_client` | `eda.grpc_client` | SUBSCRIBING → ACCEPTED → SUCCEEDED/FAILED/TIMEOUT |
| `simulation` | `eda.simulation` | 任务创建 + 完成/异常 + outcome_known |
| `turbocharts` | `turbocharts` | 命令行执行 + return_code + 耗时 |
| `turbocharts.convert` | `turbocharts.convert` | raw/img/type/linename/csv/dep/ac 全参数 |
| `turbocharts.compare` | `turbocharts.compare` | files/curve/type/align/ref/csv 全参数 |
| `vision` | `multimodal.analyze` | API 状态码 + 模型名 + 图片大小 + 耗时 + 错误响应体 |
| `report` | `report.generator` | 文件类型 + 模型名 + 图表数 + 器件数 + 规格行数 |
| `chat_service` | `chat_service` | request_id + 工具名 + 脱敏参数 + 耗时 |

Chat 工具调用日志对路径做脱敏处理（只记录文件名），不暴露用户目录结构。

### 11.15 gRPC 通道管理

- 模块级 channel 缓存：`_EDA_LOCK` 串行化下复用同一 channel，避免每次 TCP 握手
- `_get_channel()` 含健康检查（`grpc.channel_ready_future`），EDI 重启后自动重建
- `_ChannelHandle` 上下文管理器：退出时不关闭缓存 channel
- `_is_queue_busy()` 公开函数：封装私有 `_is_owned()`，供状态查询使用
- 异常分类：`DEADLINE_EXCEEDED`→TIMEOUT，`RESOURCE_EXHAUSTED`→PAYLOAD_TOO_LARGE，其余按已受理/未受理区分

### 11.16 诊断工具

- `get_service_status`：gRPC 通道状态 + 队列占用 + 上限信息
- `edi://reference/error-codes`：10 种状态码的含义 + 建议动作 Markdown 表
- `edi://service/status`：实时运行时状态（JSON，与 get_service_status 同源）
- `troubleshoot_edi_error(status)`：按 5 种错误类型提供诊断步骤的 Prompt

### 11.14 新增工具：attach_out_component

`ATTACH_OUT_COMPONENT(17)` — 为指定器件的目标引脚挂载 Out 器件并自动连线。

参数：`project_path`、`target_instance_name`、可选 `pin_index`（0 开始）。

服务端行为：自动判断引脚朝向 → 计算放置位置（100 坐标单位） → 顺时针尝试四个方向（各向外扩展 10 单位检测重叠） → 创建器件和网线在同一撤销组内。单引脚器件可省略 `pin_index`，多引脚器件必须提供。

---

## 十三、工具设计动机与数据依赖

> 本节从「为什么需要这个工具」的视角，说明每个工具的设计动机、它解决的问题，以及它与其它工具的数据依赖关系（谁为它提供输入、它的输出供谁使用）。实现细节见上文对应章节。

### 12.0 核心依赖链（总览）

```
list_epp_projects ──(工程路径)──> open_edi_project ──(已打开工程)──┬─> simulate_project / start_simulation_async
                                                                   ├─> 器件操作（create/update/delete/...）
                                                                   ├─> capture_schematic / export_project_netlist
                                                                   └─> list_schematic_components

start_simulation_async ──(task_id)──> get_simulation_async_status / get_simulation_async_result
simulate_* / simulate_netlist ──(result.raw)──> list_result_curves ──(曲线名)──> turbocharts_convert / compare_simulation_results

get_simulation_component_schema ──(参数 schema)──> create/update_simulation_component
list_simulation_components / list_schematic_components ──(instance_name)──> update/delete/set_state/attach_out

get_project_summary + turbocharts_convert + capture_schematic + simulate_* ──> generate_simulation_report ──(PDF/DOCX)──> open_document
```

### 12.1 工程管理（9 个）

| 工具 | 动机（为什么） | 解决的功能 | 依赖（输入来自） | 被依赖（输出供） |
|---|---|---|---|---|
| `list_epp_projects` | AI 不知道工作区有哪些工程，一切操作的入口 | 扫描 `.epp` 工程返回路径清单 | —（入口工具） | 几乎所有需要 `project_path` 的工具 |
| `create_project` | AI 需要新建工程 | 按名称/作者/父目录创建工程 | — | 后续打开/仿真等操作的前提 |
| `open_edi_project` | gRPC 操作要求工程已在 EDI 打开 | 打开工程到 EDI | `list_epp_projects`（工程路径） | 仿真/器件操作/截图/网表等 |
| `close_edi_project` | 释放 EDI 资源、落盘保存 | 关闭工程（可选保存） | `list_epp_projects`（工程路径） | —（收尾操作） |
| `list_schematic_components` | 本地读磁盘看不到 EDI 未保存修改和运行态 | gRPC 实时列器件（含 active_state/state） | 已打开的工程 | LLM 判断器件实时状态 |
| `get_schematic_component_info` | 按实例名单查器件详情（实时） | gRPC 单查器件完整信息 | `list_schematic_components`（instance_name） | LLM 查器件详情 |
| `get_project_summary` | 一次看全工程概览，避免零散多轮查询 | 聚合元数据/原理图/器件/仿真配置/最新结果 | —（本地读） | LLM 了解全貌、`generate_simulation_report` |
| `analyze_variables` | EDA 参数化设计依赖 Var/Sweep，需理解变量关系 | 分析 Var 定义、引用、Sweep 配置 | —（本地读） | LLM 理解参数化设计 |
| `get_components_static_params` | 查器件物理固有参数（重量/尺寸/封装/厂商/成本） | 转发到 `POST /api/v1/components/static-params/`，返回 code/message/data | 选型列表的 `alternative_model_id` | LLM 查器件物理属性 |

### 12.2 仿真器件（10 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `get_simulation_component_schema` | AI 不知道器件支持哪些参数/单位/权限 | 返回 SP/HB/XDB 参数 schema | —（读 catalog） | `create/update_simulation_component` 参数校验 |
| `list_simulation_components` | 本地快速列器件（不占 EDI 槽位），看已保存配置 | 本地列器件，支持过滤/分页/隐藏参数 | —（本地读） | `update/delete/set_state/attach_out` 提供 instance_name |
| `create_simulation_component` | AI 需要新建仿真器件 | 用 EDI 工厂默认参数创建 | `get_simulation_component_schema`（类型） | `update_simulation_component`（创建后设参） |
| `update_simulation_component` | 修改器件参数 | 按实例名更新参数（三路类型推断） | `list_simulation_components`（instance_name）、`get_simulation_component_schema`（校验）、`create_simulation_component`（创建后） | — |
| `delete_simulation_component` | 删除不需要的器件 | 按实例名删器件及连线 | `list_simulation_components`（instance_name） | — |
| `set_component_active_state` | 仿真时临时禁用/短路器件（不改参数） | 确定性设置 NORMAL/DISABLED/SHORTED | `list_simulation_components`（instance_name） | — |
| `generate_schematic_from_netlist` | 从网表快速重建/追加原理图 | 网表导入生成 main 原理图 | `export_project_netlist`/`simulate_netlist`（netlist 文件） | — |
| `replace_port_component` | 端口类型切换（TermG↔P_nToneG）保留连线 | 替换端口器件类型 | `list_schematic_components`（instance_name） | — |
| `attach_out_component` | 给器件引脚挂 Out 器件观察输出 | 挂载 Out 并自动连线 | `list_schematic_components`（instance_name） | — |
| `replace_schematic_from_file` | 用现成 .ep 文件整体替换原理图 | 整体替换原理图 | 现成 schematic.ep 文件 | — |

### 12.3 仿真（8 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `simulate_project` | 执行工程仿真 | 同步阻塞仿真，实时收日志 | `open_edi_project`（已打开工程） | 结果 RAW → 图表/报告 |
| `start_simulation_async` | 长仿真不能同步阻塞 | 异步启动，返回 task_id | `open_edi_project` | `get_simulation_async_status/result`（依赖 task_id） |
| `get_simulation_async_status` | 查进度和实时日志 | 查询异步仿真状态 | `start_simulation_async`（task_id） | LLM 判断进度 |
| `get_simulation_async_result` | 取最终结果和完整日志 | 获取仿真结果 | `start_simulation_async`（task_id） | `list_result_curves`/`turbocharts_convert`/报告 |
| `list_eda_tasks` | 看有哪些仿真在跑/排队 | 列出异步任务 | — | LLM 判断仿真状态 |
| `simulate_netlist` | 不打开工程直接仿真网表 | 仿真网表，归档 result.raw | `export_project_netlist`（netlist 文件） | `list_result_curves`/`turbocharts_convert` |
| `simulate_netlist_with_ads` | 直接调 ADS 控制器 | ADS 仿真网表 | `export_project_netlist`（netlist 文件） | — |
| `simulate_anti_burnout` | 评估器件的抗烧毁风险 | 对具备抗烧毁数据的器件执行输入功率仿真和风险评估 | 已打开的工程 | LLM 判断器件抗烧毁风险 |

### 12.4 导出分析（4 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `export_project_netlist` | 查看/导出工程网表 | 导出网表文件路径 | `open_edi_project` | `simulate_netlist`/`generate_schematic_from_netlist` |
| `capture_schematic` | 截图原理图供分析/报告 | 截取原理图为图片 | `open_edi_project` | `generate_simulation_report`、`show_image`/`analyze_image` |
| `export_schematic_components_to_csv` | 生成模型替换用的器件 CSV | 导出有效器件为 CSV | `project_path` | `replace_models_from_csv`（消费该 CSV） |
| `get_signal_chain` | 理解信号如何从源流到负载 | 解析网表，节点接力追踪链路 | 网表（本地/gRPC） | `simulate_anti_burnout`（抗烧毁评估前置）、LLM 理解链路 |

### 12.5 模型库 / 原理图库（10 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `replace_models_from_csv` | 批量替换元件模型（CSV 驱动） | 按 CSV 批量替换 | CSV 文件 | — |
| `get_model_category_params` | 了解模型库有哪些分类和参数 | 获取分类及参数列表 | —（模型服务） | `search_public_models`/`search_personal_models` 前置 |
| `search_public_models` | 从公共模型库选型 | 按子类查询公共库 | `sub_type` | 选型、`add_performance_component` |
| `search_personal_models` | 从个人模型库选型 | 按子类查询个人库 | `sub_type` | 同上 |
| `load_performance_component_from_mms` | 从 MMS 引入性能模型 | 导入 MMS 模型到本地模型库 | `original_uuid` | `add_performance_component`（导入后放置） |
| `add_performance_component` | 把模型库 Component 放进原理图 | 按 UUID+坐标放置性能器件 | `load_performance_component_from_mms`（先导入） | — |
| `search_schematic_from_public_library` | 从公共原理图库找拓扑 | 按拓扑描述查询公共库 | `search_name` | `use_schematic_from_library_*`（先搜后用） |
| `search_schematic_from_personal_library` | 从个人原理图库找拓扑 | 按拓扑描述查询个人库 | `search_name` | 同上 |
| `use_schematic_from_library_create_project` | 用库内容快速建工程 | 下载并创建新工程 | 搜索结果的 `id` | — |
| `use_schematic_from_library_import` | 用库内容替换工程原理图 | 下载并替换原理图 | 搜索结果的 `id` + `project_path` | — |

### 12.5.5 工作区（3 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `create_workspace` | 新建工作区 | 创建工作区（不切换） | — | — |
| `switch_workspace` | 指定下次启动使用的工作区 | 保存工作区路径，重启生效 | — | `get_current_workspace`（重启后生效） |
| `get_current_workspace` | 确定当前工作区（唯一事实来源） | 查询当前加载的工作区目录 | — | `edi://projects` Resource |

### 12.5.6 原理图扩展（4 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `list_ideal_components` | 知道有哪些内置器件可用 | 列出内置器件类型及说明 | — | `add_ideal_component`（先查后放） |
| `add_ideal_component` | 按坐标放内置器件 | 工厂默认参数创建 | `list_ideal_components`（类型） | — |
| `clear_schematic` | 清空原理图重建 | 删除全部器件和网段（破坏性） | 工程路径 | — |
| `add_wire` | 手动连线 | 连接两器件引脚 | `list_schematic_components`（实例名） | — |

### 12.5.7 启动 / 诊断（3 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `launch_edi` | 确保 EDI 在运行（gRPC 可用） | 启动 EDI 并等 gRPC 就绪 | — | 所有 gRPC 工具（前提） |
| `get_service_status` | 诊断 gRPC 通道/队列健康 | 只读查通道状态和队列占用 | — | `troubleshoot_edi_error` Prompt、LLM 诊断 |
| `get_service_logs` | 诊断 EDI 服务运行异常 | 读取服务端日志并统计 ERROR/WARN/异常堆栈 | `EDI_LOG_DIR` 日志目录 | LLM 诊断 |

### 12.6 图表（3 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `list_result_curves` | RAW 曲线名难猜，画图前要先解析 | 解析 RAW 返回可用曲线名 | `simulate_*` 生成的 result.raw | `turbocharts_convert`/`compare_simulation_results` |
| `turbocharts_convert` | RAW → 曲线图/CSV | 转图（支持 VSWR 自动拆分） | `list_result_curves`（曲线名）+ result.raw | `generate_simulation_report` |
| `compare_simulation_results` | 对比多个仿真的同一条曲线 | 多 RAW 对比叠图 + 差异指标 | `list_result_curves`（曲线名）+ 多个 result.raw | — |

### 12.7 ANSYS HFSS（6 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `launch_aedt` | 确保 AEDT 运行 | 启动 AEDT | — | ANSYS 其它工具前提 |
| `open_hfss_project` | 打开 HFSS 工程 | COM 附着打开 `.aedt` | `launch_aedt`（AEDT 运行） | `start_hfss_analysis_async` |
| `close_hfss_project` | 关闭 HFSS 工程释放锁 | COM 关闭 + 锁清理 | 工程名/路径 | — |
| `get_hfss_project_info` | 只读查 AEDT 项目/设计 | 查项目列表和活动设计 | AEDT 运行 | LLM 了解 AEDT 状态 |
| `start_hfss_analysis_async` | 异步跑 HFSS 仿真 | 提交 setup 分析任务 | `open_hfss_project`（工程打开） | `get_hfss_analysis_status` |
| `get_hfss_analysis_status` | 查 HFSS 仿真状态 | 查任务状态（可选刷新 AEDT） | `start_hfss_analysis_async`（task_id） | LLM 判断 HFSS 进度 |

### 12.7.5 CST 电磁仿真（5 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `cst_solve_async` | 电磁求解耗时长，需异步不阻塞 MCP | 提交求解任务（一次性会话） | `.cst` 模型文件 | `cst_solve_query` |
| `cst_solve_query` | 查求解任务，一次拿进度+结果 | 返回进度，完成时附 model_path | `cst_solve_async`（task_id） | `cst_export_snp` |
| `cst_export_snp` | 把 S 参数导出成通用格式 | 导出 Touchstone `.sNp` | 已求解的 `.cst` | 后续画图/分析 |
| `cst_export_farfield` | 把远场方向图导出成 ASCII txt | 自动判断求解，导出远场 `.txt` | 已求解或配 farfield 监视器的 `.cst` | 后续画图/分析 |
| `cst_export_farfield_query` | 查远场导出任务，一次拿进度+结果 | 返回进度，完成时附 txt 列表 | `cst_export_farfield`（task_id） | 后续画图/分析 |

### 12.8 图片（2 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `show_image` | 把本地图片返回给客户端渲染 | 返回 MCP ImageContent | — | 查看 `capture_schematic` 等生成的图片 |
| `analyze_image` | 让视觉模型理解图片内容（如原理图） | 调用视觉模型分析 | —（图片路径） | 分析 `capture_schematic` 截图 |

### 12.9 文档（1 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `open_document` | PDF/DOCX 需要预览（HTTP 链接）或用系统程序打开 | link 模式生成 10 分钟链接，local 模式 os.startfile 打开 | `generate_simulation_report`（PDF/DOCX） | LLM 查看报告 |

### 12.10 报告（1 个）

| 工具 | 动机 | 功能 | 依赖 | 被依赖 |
|---|---|---|---|---|
| `generate_simulation_report` | 把仿真结果整理成正式 PDF/DOCX 报告 | 聚合数据 + 调渲染服务 | `get_project_summary`（工程信息）、`turbocharts_convert`（曲线图）、`capture_schematic`（拓扑图）、`simulate_*`（结果数据） | `open_document`（查看报告） |

### 12.11 解析文件类工具详解

有 6 个本地读取类工具的核心工作是「从磁盘文件里解析结构化数据」。它们不经过 gRPC，直接读 `.epp` 工程目录或 RAW 结果文件。下面是各自「解析什么文件、怎么解析、为什么解析」。

**通用解析能力**（都在 `servers/eda/project_reader.py`，被下面多个工具复用）：

| 函数 | 作用 |
|---|---|
| `ProjectReader` | 定位 `.epp` 工程目录，按相对路径读取 metadata/schematic/netlist 文件 |
| `parse_sexp()` | 递归下降解析 EDI 的 Lisp 风格 S-expression（处理嵌套括号、引号转义、裸词） |
| `parse_paramsinfo()` | 解析 paramsinfo JSON（兼容普通参数和 Var 变量的两种结构） |
| `parse_components()` | 从 schematic.ep 的 S-expression 中提取所有 `(component ...)` 节点 |

**逐工具解析说明**：

| 工具 | 解析的文件 | 怎么解析 | 为什么解析 |
|---|---|---|---|
| `list_epp_projects` | 文件夹下的 `*.epp` | `Path.rglob("*.epp")` 递归遍历 + `stat()` 读大小 | 拿到工程路径清单，是后续所有操作的入口 |
| `get_project_summary` | `.epp` 目录（metadata.ep / schematics.ep / schematic.ep / netlist.log / `*.raw`） | `parse_sexp` 解析元数据 → `parse_components` 统计器件 → `rglob` 找 RAW | 一次聚合工程全貌（元数据/器件分布/仿真配置/最新结果） |
| `analyze_variables` | 各原理图的 schematic.ep | `parse_components` 提取器件 → `parse_paramsinfo` 解析参数 → 识别 Var/Sweep 类型 | 理解参数化设计（Var 定义、引用关系、Sweep 扫描配置） |
| `list_simulation_components` | 各原理图的 schematic.ep | `parse_components` 提取器件 → `parse_paramsinfo` 解析参数 → wire→public 名称映射 | 本地快速查看已保存器件配置（不占 EDI 槽位） |
| `list_result_curves` | ADS RAW 文件头（MDS / XML 两种格式） | `_parse_raw_header` 读前 64KB → 识别 MDS（逐行解析 Plotname/Variables/Values）或 XML（正则匹配 `<Number/Complex/Real name=...>`） | 拿到可用曲线名，避免画图时猜错曲线名 |
| `compare_simulation_results` | turbocharts 导出的 CSV | `_read_curve_csv_xy` 读前两列（x / y） | 对齐多个仿真的同一条曲线，计算差异指标 |

**关键文件格式示例**：

S-expression（schematic.ep 里的 component 节点）：
```lisp
(component uuid-001
  (type "ResG")
  (name "R1")
  (pin 1)
  (pin 2)
  (paramsinfo "{\"R\":{\"Value\":\"50\",\"CurrentUnit\":\"Ohm\"}}"))
```

paramsinfo JSON（两种结构，`parse_paramsinfo` 统一为小写 key）：
```json
// 普通参数
{"Value": "50", "CurrentUnit": "Ohm", "Tunable": "false"}
// Var 变量
{"Initial": "29", "Max": "", "Min": "", "Status": "Disable"}
```

ADS RAW 文件头（MDS 格式，`list_result_curves` 解析的对象）：
```
File Format: MDS
Plotname: SP SP1[1] freq=(1 GHz->10 GHz)
No. Variables: 11
Variables:
    0 freq frequency type=real indep=yes
    1 S[1,1] s-param type=complex indep=no
```

> 其余「解析文件」发生在 EDI gRPC 服务端而非 MCP 本地：`replace_models_from_csv` 由服务端解析 CSV、`generate_schematic_from_netlist`/`simulate_netlist` 由服务端解析 netlist.log，MCP 只负责校验文件存在并传路径。

### 12.12 通信与执行机制速查

其余工具按底层执行方式分为 6 类。每类的「设计动机」如下，详细实现见对应章节（避免重复）：

| 机制 | 涉及工具 | 为什么这样设计 | 详细实现见 |
|---|---|---|---|
| gRPC 异步调用 | 39 个 EDA 工具 | EDI 是单实例桌面程序，同一时间只能做一件事，用「提交 + 流式推送」避免长连接阻塞 | 第一节 |
| subprocess 调用 | 3 个 turbocharts | `turbocharts_app.exe` 一次只能跑一个实例（多开会冲突），必须串行 | 第四节 |
| COM 自动化 | 6 个 ANSYS | AEDT 是 Windows COM 程序，无 gRPC 接口；锁文件防多进程同时打开 | 第五节 |
| 异步任务队列 | start_simulation_async / start_hfss_analysis_async | 仿真可能几分钟到几小时，用「提交即返回 task_id + 后台执行」解耦 | 第一节、第七节 |
| HTTP + 临时 Token | 报告 / 视觉分析 / 文档 | 独立服务只能走 HTTP；PDF/图片要浏览器预览需临时 URL | 第六节、第十节 |
| 参数校验管线 | 器件参数 / 报告 | AI 传参易错，前置校验拦掉大部分无效调用 | 第一节、第十节 |
