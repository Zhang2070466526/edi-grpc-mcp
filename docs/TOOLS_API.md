# EDI gRPC MCP API 参考

> 所有函数均可通过 `from servers.xxx import func` 直接调用，无需启动 MCP 服务。
>
> 相关文档：[HTTP_API.md](./HTTP_API.md)（HTTP 路由）、[IMPLEMENTATION.md](./IMPLEMENTATION.md)（实现原理）。

```python
# 安装
pip install edi-grpc-mcp

# 使用
from servers.eda.project_manage import list_epp_projects
```

---

## 目录

- **[工程管理（9 个）](#工程管理9个)**：创建 / 扫描 / 打开 / 关闭工程、查询器件、分析变量
- **[仿真（8 个）](#仿真8个)**：同步 / 异步仿真、网表仿真、抗烧毁评估、任务查询
- **[导出与分析（4 个）](#导出与分析4个)**：导出网表、截图原理图、器件 CSV、信号链路追踪
- **[模型库 / 原理图库（10 个）](#模型库--原理图库10个)**：模型分类/查询/搜索、MMS 导入、器件放置、原理图库搜索/使用
- **[工作区（3 个）](#工作区3个)**：创建 / 切换 / 查询当前工作区
- **[原理图扩展（4 个）](#原理图扩展4个)**：内置器件、清空原理图、连线
- **[启动 / 诊断（3 个）](#启动--诊断3个)**：启动 EDI、服务诊断、日志读取
- **[ANSYS HFSS（6 个）](#ansys-hfss6个)**：AEDT 工程开关、HFSS 异步仿真
- **[CST 电磁仿真（5 个）](#cst电磁仿真5个)**：异步求解 .cst、导出 S 参数 / 远场方向图
- **[图表（3 个）](#图表3个)**：RAW 曲线解析、转图、结果对比
- **[图片（2 个）](#图片2个)**：显示图片、视觉分析
- **[仿真器件管理（10 个）](#仿真器件管理10个协议-v3)**：器件 Schema、增删改、状态、网表导入、原理图加载
- **[Resources & Prompts](#resources--prompts)**：只读资源 + 可复用工作流
- **[文档（1 个）](#文档1个)**：打开本地文档
- **[报告（1 个）](#报告1个)**：生成仿真报告
- **[辅助](#辅助)**：Chat 上下文
- **[gRPC 工具统一返回格式](#gRPC工具统一返回格式)**：gRPC 工具的返回结构 + status 语义

---

## 工程管理（9 个）

### `list_epp_projects`

```python
from servers.eda.project_manage import list_epp_projects

list_epp_projects(folder_path: str) -> dict
```

扫描文件夹中所有 `.epp` 工程文件。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `folder_path` | str | 是 | 要扫描的文件夹绝对路径 |

返回：
```python
{
    "success": True,
    "folder": "C:/Users/JGL/EDI-Workspace",
    "count": 3,
    "projects": [
        {"name": "demo1", "path": "C:/Users/JGL/EDI-Workspace/demo1.epp", "size": 0},
    ]
}
```

---

### `create_project`

```python
from servers.eda.project_manage import create_project

create_project(name: str, author: str = "", path: str = "", timeout_seconds: int = 60) -> dict
```

创建新的 EDI 工程（不显示创建向导、不自动打开）。工程文件路径为 `<path>/<name>/<name>.epp`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `name` | str | 是 | — | 工程名（合法 Windows 文件名） |
| `author` | str | 否 | "" | 作者（空则默认 bm） |
| `path` | str | 否 | "" | 工程父目录（空则用工作区 projects 目录） |
| `timeout_seconds` | int | 否 | 60 | 最长等待时间 |

返回（gRPC 统一结构，成功时 `details` 含 `project_path`）：
```python
{"success": True, "status": "SUCCEEDED", "details": {"project_path": "D:/projects/demo_project/demo_project.epp"}}
```

---

### `open_edi_project`

```python
from servers.eda.project_manage import open_edi_project

open_edi_project(project_path: str, timeout_seconds: int = 60) -> dict
```

打开 `.epp` 工程。通过 gRPC 调用 EDI 服务。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数（上限 300） |

返回：
```python
{
    "success": True,
    "completed": True,
    "status": "SUCCEEDED",
    "message": "OPEN_PROJECT 成功",
    "project_path": "C:/Projects/test/test.epp",
    "details": {"project_path": "C:/Projects/test/test.epp"}
}
```

---

### `close_edi_project`

```python
from servers.eda.project_manage import close_edi_project

close_edi_project(project_path: str, need_save: bool = False, timeout_seconds: int = 60) -> dict
```

关闭 `.epp` 工程。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `need_save` | bool | 否 | False | 关闭前是否保存 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `list_schematic_components`

```python
from servers.eda.project_manage import list_schematic_components

list_schematic_components(project_path: str, timeout_seconds: int = 60) -> dict
```

通过 gRPC 查询原理图全部器件（含完整参数），比本地文件读取更实时，能看到 EDI 未保存的修改和运行态（active_state/state）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

返回（gRPC 统一结构，业务字段在 `details` 中）：
```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "component_count": 5,
        "components": [
            {"instance_name": "R1", "component_type": "R",
             "general_type": "", "sub_type": "",
             "active_state": 0, "state": "NORMAL", "parameters": {...}}
        ]
    }
}
```

---

### `get_schematic_component_info`

```python
from servers.eda.project_manage import get_schematic_component_info

get_schematic_component_info(project_path: str, instance_name: str, timeout_seconds: int = 60) -> dict
```

通过 gRPC 按实例名查询单个器件的完整信息（实时，含未保存修改和 active_state/state）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `instance_name` | str | 是 | — | 器件实例名（如 "R1"） |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

返回（gRPC 统一结构，业务字段在 `details` 中）：
```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "instance_name": "R1",
        "component": {
            "instance_name": "R1", "component_type": "R",
            "general_type": "", "sub_type": "",
            "active_state": 0, "state": "NORMAL", "parameters": {...}
        }
    }
}
```

---

### `get_components_static_params`

```python
from servers.eda.project_manage import get_components_static_params

get_components_static_params(
    original_uuids: list[str] | None = None,
    original_uuid: str = "",
    timeout_seconds: int = 60,
) -> dict
```

查询器件的固有参数（重量、尺寸、封装、所属厂商、成本等）。UUID 来自选型列表（`replace_models_from_csv` 的 CSV）的 `alternative_model_id` 列。不需要打开工程，也不需要 `project_path`。gRPC 服务将请求转发到 `POST /api/v1/components/static-params/`，返回上游完整响应（code/message/data）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `original_uuids` | list | 否 | None | 器件 UUID 数组（批量），与 `original_uuid` 二选一 |
| `original_uuid` | str | 否 | "" | 单个器件 UUID，与 `original_uuids` 二选一 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

返回（gRPC 统一结构，业务字段在 `details` 中）：

```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "code": 200,
        "message": "器件固有参数获取成功",
        "data": [{"模型id": "...", "重量": "", "尺寸": "...", "封装": "", "所属厂商": "...", "成本": ""}, ...]
    }
}
```

`data` 与请求 UUID 顺序一一对应，未命中的 UUID 保留为 `null`。

---

### `get_project_summary`

```python
from servers.eda.project_manage import get_project_summary

get_project_summary(
    project_path: str,
    include_component_types: bool = True,
    include_latest_result: bool = True,
) -> dict
```

获取 `.epp` 工程完整概览（元数据、原理图、仿真配置、最近结果）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `include_component_types` | bool | 否 | True | 是否统计元件类型分布 |
| `include_latest_result` | bool | 否 | True | 是否包含最近仿真结果 |

返回：
```python
{
    "success": True,
    "project": {"project_id": "...", "name": "demo", "author": ""},
    "schematics": {"count": 1, "names": ["main"]},
    "components": {"total": 15, "by_type": {"ResG": 5, "CapG": 3}},
    "simulation": {"type": "S_Param", "start": "0 GHz", "stop": "10 GHz"},
    "latest_result": {"path": ".../result.raw", "exists": True, "size": 2048}
}
```

---

### `analyze_variables`

```python
from servers.eda.project_manage import analyze_variables

analyze_variables(project_path: str) -> dict
```

分析工程中的 Var 变量定义、其他元件对变量的引用、Sweep 扫描配置。

返回：
```python
{
    "variables": [{"name": "freqin", "parameter": "freqin", "initial": "29", ...}],
    "references": [{"variable": "freqin", "component": "PORT1", "parameter": "Freq[1]"}, ...],
    "sweeps": [{"sweep": "Sweep2", "variable": "freqin", "start": "29", "stop": "31", ...}]
}
```

---

## 仿真（8 个）

### `simulate_project`

```python
from servers.eda.simulation import simulate_project

simulate_project(project_path: str, log_source: str = "mcp_client", timeout_seconds: int = 600) -> dict
```

对 `.epp` 工程执行仿真，**同步等待**完成。FetchEvent 长连接期间实时收集 `ads_output` 增量日志。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `log_source` | str | 否 | "mcp_client" | 日志来源标识 |
| `timeout_seconds` | int | 否 | 600 | 最长等待秒数（上限 3600） |

返回：
```python
{
    "success": True,
    "completed": True,
    "status": "SUCCEEDED",
    "project_path": "C:/Projects/test/test.epp",
    "result_path": "C:/Projects/test/history/result.raw",
    "ads_output": "Parsing netlist...\nTask completed.\n",
    "log_complete": True
}
```

> **注意**：此函数为同步阻塞，一次 HTTP 请求可能等待数分钟。交互场景建议使用 `start_simulation_async`。

---

### `start_simulation_async`

```python
from servers.eda.simulation import start_simulation_async

start_simulation_async(project_path: str, log_source: str = "mcp_client", timeout_seconds: int = 600) -> dict
```

**异步启动**仿真，立即返回 `task_id`。通过以下工具查询进度和结果。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `log_source` | str | 否 | "mcp_client" | 日志来源标识 |
| `timeout_seconds` | int | 否 | 600 | 后台任务最长等待秒数 |

返回：
```python
{
    "success": True,
    "task_id": "a1b2c3d4...",
    "client_uuid": "e5f6...",
    "status": "QUEUED",
    "message": "仿真任务已创建"
}
```

---

### `get_simulation_async_status`

```python
from servers.eda.simulation import get_simulation_async_status

get_simulation_async_status(task_id: str) -> dict
```

查询异步仿真任务状态和已实时接收的 `ads_output` 日志。运行中即可查询。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | str | 是 | `start_simulation_async` 返回的 task_id |

返回：
```python
{
    "success": True,
    "completed": False,
    "task_id": "a1b2...",
    "status": "RUNNING",
    "ads_output": "Parsing netlist...\n",
    "log_complete": False,
    "project_path": "C:/Projects/test/test.epp",
    "result_path": "",
    "started_at": 1750000000.0
}
```

---

### `get_simulation_async_result`

```python
from servers.eda.simulation import get_simulation_async_result

get_simulation_async_result(task_id: str) -> dict
```

获取仿真最终结果。运行中返回当前部分日志，完成后返回完整 `ads_output`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | str | 是 | `start_simulation_async` 返回的 task_id |

运行中返回：
```python
{"success": True, "completed": False, "status": "RUNNING", "ads_output": "..."}
```

完成后返回：
```python
{
    "success": True, "completed": True,
    "status": "SUCCEEDED",
    "project_path": "...",
    "result_path": ".../history/result.raw",
    "ads_output": "完整日志...",
    "log_complete": True
}
```

---

### `simulate_netlist`

```python
from servers.eda.simulation import simulate_netlist

simulate_netlist(netlist_path: str, timeout_seconds: int = 600) -> dict
```

仿真指定网表文件，返回 RAW 结果和仿真器输出日志。服务端自动复制网表→仿真→归档→清理。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `netlist_path` | str | 是 | — | 网表文件路径（必须已存在） |
| `timeout_seconds` | int | 否 | 600 | 最长等待秒数 |

返回：
```python
{
    "success": True,
    "status": "SUCCEEDED",
    "result_path": "C:/test/history/result.raw",
    "ads_output": "ADS simulator output...",
    "details": {"netlist_path": "C:/test/netlist.log"}
}
```

---

### `simulate_netlist_with_ads`

```python
from servers.eda.simulation import simulate_netlist_with_ads

simulate_netlist_with_ads(netlist_path: str, ads_path: str = "", timeout_seconds: int = 120) -> dict
```

直接调用 ADS 仿真控制器处理网表。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `netlist_path` | str | 是 | — | 网表文件路径 |
| `ads_path` | str | 否 | "" | ADS 安装路径（空则自动判断） |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `simulate_anti_burnout`

```python
from servers.eda.simulation import simulate_anti_burnout

simulate_anti_burnout(project_path: str, timeout_seconds: int = 600) -> dict
```

对工程原理图中具备抗烧毁数据的器件执行输入功率仿真和抗烧毁风险评估。服务端只计算并返回结果，不主动显示仿真窗口。`results` 只包含 `isAntiBurnout==true` 的器件；部分器件评估失败时整体仍成功，每个器件的结论/原因在各自的 `result` 字段。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 工程文件绝对路径 |
| `timeout_seconds` | int | 否 | 600 | 最长等待秒数 |

返回（gRPC 统一结构，业务字段在 `details` 中）：

```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "results": [
            {"component_type": "Attenuator", "instance_name": "Attenuator1",
             "simulated_input_power": "12.4 dBm", "max_input_power": "1 W",
             "result": "无抗烧毁风险"},
        ]
    }
}
```

每个结果项的 `result`：评估成功为 `无抗烧毁风险` / `有抗烧毁风险`，无法评估时为具体失败原因。

---

### `list_eda_tasks`

```python
from servers.eda.simulation import list_eda_tasks

list_eda_tasks(status: str = "") -> dict
```

列出当前 MCP 进程中已提交的异步仿真任务。可按状态过滤。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `status` | str | 否 | "" | 按状态过滤：QUEUED / RUNNING / SUCCEEDED / FAILED / TIMEOUT 等 |

返回：
```python
{"success": True, "total": 2, "tasks": [
    {"task_id": "abc123", "status": "RUNNING", "project_path": "...", ...}
]}
```

---

## 导出与分析（4 个）

### `export_project_netlist`

```python
from servers.eda.design_export import export_project_netlist

export_project_netlist(project_path: str, timeout_seconds: int = 60) -> dict
```

查看/导出 `.epp` 工程的网表，返回网表文件路径。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `capture_schematic`

```python
from servers.eda.design_export import capture_schematic

capture_schematic(project_path: str, img_path: str, timeout_seconds: int = 60) -> dict
```

截取原理图为图片（PNG/JPG 等）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `img_path` | str | 是 | — | 输出图片路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `export_schematic_components_to_csv`

```python
from servers.eda.design_export import export_schematic_components_to_csv

export_schematic_components_to_csv(project_path: str, save_path: str, timeout_seconds: int = 60) -> dict
```

将工程原理图中的有效器件信息导出为 CSV。导出列为 original_model_type/name/id 与 alternative_model_type/name/id（后三列留空），供 replace_models_from_csv 后续模型替换填写。仿真控制器、端口、变量等 EXCLUDED_TYPES 不导出。save_path 未以 .csv 结尾时服务端自动追加后缀；父目录必须已存在。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `save_path` | str | 是 | — | CSV 输出文件路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `get_signal_chain`

```python
from servers.eda.signal_chain import get_signal_chain

get_signal_chain(project_path: str, start_component: str = "",
                 direction: str = "forward", max_depth: int = 40,
                 timeout_seconds: int = 60) -> dict
```

追踪工程原理图的信号链路（节点接力算法，从源到负载）。读取网表，按「节点↔器件交替接力」追踪信号流方向。v1 只返回链路结构（器件序列/角色/分支数），不含增益/插损等规格。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `start_component` | str | 否 | "" | 起始器件实例名，留空自动找激励源（无激励源则取第一个端口） |
| `direction` | str | 否 | "forward" | 追踪方向（forward/backward） |
| `max_depth` | int | 否 | 40 | 最大追踪深度（防环路死循环） |
| `timeout_seconds` | int | 否 | 60 | gRPC 网表导出超时 |

返回：
```python
{
    "success": True,
    "start": "PORT1",
    "chain": [
        {"instance": "PORT1", "type": "Port", "role": "source", "model": ""},
        {"instance": "NC10355C_29311", "type": "AmplifierDevice", "role": "device", "model": "NC10355C_2931"},
        {"instance": "TermG2", "type": "Port", "role": "load", "model": ""},
    ],
    "branch_count": 0,
    "truncated": false,
    "warning": "",
    "skipped_lines": 8,
}
```

---

## 模型库 / 原理图库（10 个）

### `replace_models_from_csv`

```python
from servers.eda.model_replace import replace_models_from_csv

replace_models_from_csv(project_path: str, csv_path: str, timeout_seconds: int = 60) -> dict
```

按 CSV 批量替换元件模型。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `csv_path` | str | 是 | — | CSV 文件绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `get_model_category_params`

```python
from servers.eda.model_library import get_model_category_params

get_model_category_params(timeout_seconds: int = 60) -> dict
```

获取模型管理模块的全部子类及对应参数列表。不需要打开工程，也不需要 `project_path`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `search_public_models`

```python
from servers.eda.model_library import search_public_models

search_public_models(sub_type: str, filters: list | None = None, timeout_seconds: int = 60) -> dict
```

按子类查询公共模型库。`sub_type` 是模型子类 ID（非空字符串），`filters` 数组原样转发给模型服务。返回裁剪响应（`code`/`message`/`data`）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sub_type` | str | 是 | — | 模型子类 ID（如 "61"） |
| `filters` | list | 否 | [] | 过滤条件数组（原样转发） |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `search_personal_models`

```python
from servers.eda.model_library import search_personal_models

search_personal_models(sub_type: str, filters: list | None = None, timeout_seconds: int = 60) -> dict
```

按子类查询个人模型库。参数与返回结构同 `search_public_models`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sub_type` | str | 是 | — | 模型子类 ID（如 "61"） |
| `filters` | list | 否 | [] | 过滤条件数组（原样转发） |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `load_performance_component_from_mms`

```python
from servers.eda.model_library import load_performance_component_from_mms

load_performance_component_from_mms(original_uuid: str, timeout_seconds: int = 90) -> dict
```

从 MMS（模型管理系统）下载性能模型并导入当前工作区本地模型库。无需 `.epp`、`project_path` 或工作区路径，不要求打开工程。下载、解压并校验 `library.ep` 后合并至本地模型库，随后复制仿真文件并发起模型库重新扫描（下载超时 60 秒）。成功只表示已发起扫描，不代表异步扫描完成；导入后需等待扫描完成再调用 `add_performance_component`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `original_uuid` | str | 是 | — | MMS 原始模型 UUID |
| `timeout_seconds` | int | 否 | 90 | 最长等待秒数 |

---

### `add_performance_component`

```python
from servers.eda.model_library import add_performance_component

add_performance_component(project_path: str, component_uuid: str, position: dict, timeout_seconds: int = 120) -> dict
```

将工作区模型库中的 Component 放置到指定工程原理图并保存。`component_uuid` 是模型库 Component UUID（非 MMS 的 original_uuid），坐标会吸附到网格。不自动下载模型、不设置参数、不避让重叠、不返回实例名。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `component_uuid` | str | 是 | — | 模型库 Component UUID |
| `position` | dict | 是 | — | 场景坐标 `{"x": .., "y": ..}` |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `search_schematic_from_public_library`

```python
from servers.eda.model_library import search_schematic_from_public_library

search_schematic_from_public_library(search_name: str, timeout_seconds: int = 30) -> dict
```

按拓扑描述查询公共原理图库。search_name 是必需的拓扑描述关键词，服务端固定转换为 topology_description 过滤条件。不需要 project_path、工作区或已打开工程。使用前应提示用户提供对应拓扑描述。成功 payload 含 count 和 results。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `search_name` | str | 是 | — | 拓扑描述关键词（必需） |
| `timeout_seconds` | int | 否 | 30 | 最长等待秒数 |

---

### `search_schematic_from_personal_library`

```python
from servers.eda.model_library import search_schematic_from_personal_library

search_schematic_from_personal_library(search_name: str, timeout_seconds: int = 30) -> dict
```

按拓扑描述查询个人原理图库，参数与返回结构同 `search_schematic_from_public_library`。

---

### `use_schematic_from_library_create_project`

```python
from servers.eda.model_library import use_schematic_from_library_create_project

use_schematic_from_library_create_project(file_uuid: str, timeout_seconds: int = 180) -> dict
```

从在线原理图库下载内容，在当前工作区创建并打开新工程。file_uuid 是在线原理图文件的合法 UUID（取自搜索结果的 id）。下载超时 120 秒，模型库扫描超时 60 秒。同名工程目录存在则失败、不覆盖。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_uuid` | str | 是 | — | 在线原理图文件的 UUID |
| `timeout_seconds` | int | 否 | 180 | 最长等待秒数 |

---

### `use_schematic_from_library_import`

```python
from servers.eda.model_library import use_schematic_from_library_import

use_schematic_from_library_import(file_uuid: str, project_path: str, timeout_seconds: int = 180) -> dict
```

从在线原理图库下载内容，替换指定工程的当前原理图并保存。⚠️ 会替换工程原理图，请确认 project_path。下载/扫描规则同 create 方式。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_uuid` | str | 是 | — | 在线原理图文件的 UUID |
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `timeout_seconds` | int | 否 | 180 | 最长等待秒数 |

---

## 工作区（3 个）

### `create_workspace`

```python
from servers.eda.workspace_ops import create_workspace

create_workspace(path: str, timeout_seconds: int = 60) -> dict
```

创建一个新的工作区，不自动切换。路径无效、已有合法工作区或目标目录非空时失败。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | str | 是 | — | 工作区目录绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `switch_workspace`

```python
from servers.eda.workspace_ops import switch_workspace

switch_workspace(path: str, timeout_seconds: int = 60) -> dict
```

设置下次启动程序时使用的工作区，当前工作区保持不变。仅保存最近使用的工作区路径，下次启动生效。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | str | 是 | — | 工作区目录绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `get_current_workspace`

```python
from servers.eda.workspace_ops import get_current_workspace

get_current_workspace(timeout_seconds: int = 60) -> dict
```

查询程序当前实际加载的工作区目录。只读查询，不修改状态。若调用了 `switch_workspace` 但尚未重启，仍返回当前工作区（不是下次启动的路径）。成功时 `details` 含 `workspace_path`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

## 原理图扩展（4 个）

### `list_ideal_components`

```python
from servers.eda.schematic_ops import list_ideal_components

list_ideal_components(timeout_seconds: int = 60) -> dict
```

列出内置器件类型及其简要说明。数据读取 ComponentToolBar 的 `symbolDescriptionMap`，不需要打开工程。每项含 `component_type` 和 `description`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `add_ideal_component`

```python
from servers.eda.schematic_ops import add_ideal_component

add_ideal_component(project_path: str, component_type: str, position: dict, timeout_seconds: int = 120) -> dict
```

按指定坐标新增内置器件，使用工厂默认参数，不自动排布。严格按场景坐标放置，不做网格吸附、自动排布或重叠避让。`component_type` 区分大小写，支持范围同 `create_simulation_component` 的工厂类型。不接收 `parameters`，后续用 `update_simulation_component` 设参。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `component_type` | str | 是 | — | 器件工厂注册类型名（如 "R"） |
| `position` | dict | 是 | — | 场景坐标 `{"x": .., "y": ..}` |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `clear_schematic`

```python
from servers.eda.schematic_ops import clear_schematic

clear_schematic(project_path: str, confirm_clear: bool = False, timeout_seconds: int = 300) -> dict
```

清空工程原理图中的全部器件和网段并保存（破坏性操作）。会删除全部器件（含控制器、Var、Out）、独立文本以及全部网段、连线和连接点。需显式传 `confirm_clear=True` 才会执行（双重确认）；调用即表示确认，不额外弹确认框。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `confirm_clear` | bool | 否 | False | 必须显式 True 才执行 |
| `timeout_seconds` | int | 否 | 300 | 最长等待秒数 |

---

### `add_wire`

```python
from servers.eda.schematic_ops import add_wire

add_wire(project_path: str, first_instance_name: str, first_pin_index: int, second_instance_name: str, second_pin_index: int, timeout_seconds: int = 120) -> dict
```

连接两个器件的指定引脚，按需创建或合并网段并保存。`pin_index` 是 0 开始的内部引脚编号（非界面端口名称），范围 0~2147483647。拒绝同一引脚自连接。新增连线及网段合并作为可撤销操作，提交后保存工程。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `first_instance_name` | str | 是 | — | 第一个器件实例名 |
| `first_pin_index` | int | 是 | — | 第一个器件引脚编号（0 开始） |
| `second_instance_name` | str | 是 | — | 第二个器件实例名 |
| `second_pin_index` | int | 是 | — | 第二个器件引脚编号（0 开始） |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

## 启动 / 诊断（3 个）

### `launch_edi`

```python
from servers.eda.edi_launcher import launch_edi

launch_edi(edi_path: str = "", wait_for_grpc: bool = True, wait_timeout: int = 30) -> dict
```

启动 EDI 客户端并等待 gRPC 就绪。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `edi_path` | str | 否 | .env 配置 | EDI.exe 路径 |
| `wait_for_grpc` | bool | 否 | True | 是否等待 gRPC 端口就绪 |
| `wait_timeout` | int | 否 | 30 | 等待超时秒数 |

---

### `get_service_status`

```python
from servers.eda.edi_launcher import get_service_status

get_service_status() -> dict
```

返回 EDI gRPC 通道状态和队列占用信息（只读，不占执行槽位），用于诊断通道是否健康、是否有任务在排队。

返回：
```python
{"grpc_target": "127.0.0.1:50055", "channel_state": "ready/unhealthy/unknown",
 "channel_cached": True, "queue_locked": False, "max_receive_mb": 256}
```

---

### `get_service_logs`

```python
from servers.eda.edi_launcher import get_service_logs

get_service_logs(lines: int = 50, keyword: str = "", level: str = "") -> dict
```

读取 EDI 服务端日志（`logs/eda_YYYY-MM-DD.log`，当天），检查运行异常。支持按关键词/级别过滤，并统计 ERROR / WARN / 异常堆栈。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `lines` | int | 否 | 50 | 返回最后 N 行（1-500） |
| `keyword` | str | 否 | "" | 关键词过滤（大小写不敏感） |
| `level` | str | 否 | "" | 级别过滤（DEBUG/INFO/WARN/ERROR） |

返回：
```python
{
    "success": True,
    "log_file": "C:/Program Files (x86)/EDI/logs/eda_2026-09-03.log",
    "total_lines": 1280,
    "matched_lines": 42,
    "lines": ["...", "..."],
    "error_count": 5,
    "warning_count": 8,
    "exception_lines": ["Traceback ...", "..."],
    "message": "日志共 1280 行，发现 5 个错误、8 个警告。",
}
```

日志路径由 `.env` 的 `EDI_LOG_DIR` 配置，默认 `C:\Program Files (x86)\EDI\logs`。

---

## ANSYS HFSS（6 个）

### `open_hfss_project`

```python
from servers.ansys.project_manage import open_hfss_project

open_hfss_project(project_path: str, aedt_path: str = "", wait_timeout: int = 30) -> dict
```

启动 AEDT 并打开 .aedt 项目（COM 附着优先，subprocess 单次启动兜底）。流程：检查锁文件 → 清理失效锁 → COM 附着打开或 subprocess 启动 → 轮询确认工程打开。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .aedt/.aedtz 文件绝对路径 |
| `aedt_path` | str | 否 | 自动检测 | ansysedt.exe 路径 |
| `wait_timeout` | int | 否 | 30 | 等待超时秒数（1-120） |

返回：`{"success": true, "status": "opened/already_open", "project_opened": true, "method": "com/subprocess", "duration_s": 1.2}`；失败 `{"success": false, "status": "invalid_path"/"aedt_not_found"/"project_locked"/"com_open_failed", "message": "..."}`

### `close_hfss_project`

```python
from servers.ansys.project_manage import close_hfss_project

close_hfss_project(project_name: str = "", project_path: str = "", save_before_close: bool = False, force: bool = False) -> dict
```

关闭 AEDT 项目（COM 优先，force 仅结束 MCP 最后启动的 PID）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_name` | str | 否 | "" | 项目名，为空关闭活动项目 |
| `project_path` | str | 否 | "" | 项目路径，用于清理锁文件 |
| `save_before_close` | bool | 否 | False | 关闭前保存 |
| `force` | bool | 否 | False | 仅结束 MCP 最后启动的 PID |

返回：`{"success": true, "method": "com", "project_closed": true, "message": "已关闭: demo"}`；失败 `{"success": false, "message": "..."}`。

### `launch_aedt`

```python
from servers.ansys.project_manage import launch_aedt

launch_aedt(aedt_path: str = "", wait_timeout: int = 30) -> dict
```

启动 AEDT（不打开项目）。已运行时仅返回状态。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `aedt_path` | str | 否 | 自动检测 | ansysedt.exe 路径 |
| `wait_timeout` | int | 否 | 30 | 等待超时秒数 |

返回：`{"success": true, "status": "started"/"already_running", "com_ready": true, "pid": 12345, "message": "..."}`

### `get_hfss_project_info`

```python
from servers.ansys.project_manage import get_hfss_project_info

get_hfss_project_info() -> dict
```

查询当前 AEDT 项目信息（纯查询，不启动 AEDT）。

返回：`{"success": True, "aedt_running": True, "pids": [...], "open_projects": [...], "active_project": "...", "active_design": "..."}`

### `start_hfss_analysis_async`

```python
from servers.ansys.run_analysis import start_hfss_analysis_async

start_hfss_analysis_async(project_path: str, design_name: str, setup_name: str, save_before_run: bool = True) -> dict
```

异步启动 HFSS Setup 仿真，立即返回 task_id。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .aedt 文件绝对路径 |
| `design_name` | str | 是 | — | 设计名称 |
| `setup_name` | str | 是 | — | Setup 名称 |
| `save_before_run` | bool | 否 | True | 仿真前保存 |

返回：`{"success": true, "task_id": "hfss-xxx", "status": "QUEUED", ...}`；失败 `{"success": false, "status": "aedt_not_running"/"project_not_open"/"design_not_found"/"setup_not_found"/"analysis_busy"}`

### `get_hfss_analysis_status`

```python
from servers.ansys.run_analysis import get_hfss_analysis_status

get_hfss_analysis_status(task_id: str, refresh_from_aedt: bool = False) -> dict
```

查询 HFSS 异步仿真状态（默认只读本地，不访问 AEDT）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `task_id` | str | 是 | — | `start_hfss_analysis_async` 返回的 task_id |
| `refresh_from_aedt` | bool | 否 | False | 是否实时查询 AEDT 仿真状态 |

返回：`{"success": true, "task_id": "...", "status": "RUNNING"/"SUCCEEDED"/"FAILED", "completed": false, "task_success": null, "outcome_known": false, "result_directory": "..."}`；不存在 `{"success": false, "error_code": "TASK_NOT_FOUND", "status": "UNKNOWN"}`。

---

## CST 电磁仿真（5 个）

### `cst_solve_async`

```python
from servers.cst import cst_solve_async

cst_solve_async(model_path: str) -> dict
```

异步求解 CST 模型（.cst），立即返回 task_id。求解在后台单 worker 串行执行（一次性会话：连接 → run_solver → 保存 → 关闭）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `model_path` | str | 是 | — | .cst 模型文件绝对路径 |

返回：`{"success": true, "task_id": "cst-a1b2...", "status": "QUEUED"}`；队列满 `{"success": false, "error_code": "CST_QUEUE_FULL"}`。

### `cst_solve_query`

```python
from servers.cst import cst_solve_query

cst_solve_query(task_id: str) -> dict
```

查询 CST 求解任务：返回进度，完成时附带 model_path。一次调用同时拿到进度和结果。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | str | 是 | `cst_solve_async` 返回的 task_id |

返回（完成）：`{"success": true, "completed": true, "status": "SUCCEEDED", "model_path": "C:/.../xxx.cst"}`；运行中 `{"success": true, "completed": false, "status": "RUNNING"}`；失败 `{"success": false, "error_code": "CST_SOLVE_FAILED"}`。

### `cst_export_snp`

```python
from servers.cst import cst_export_snp

cst_export_snp(model_path: str, output_dir: str = "", port_count: int | None = None) -> dict
```

导出 CST 模型的 S 参数为 Touchstone .sNp 文件（需先求解）。无会话直接读结果。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `model_path` | str | 是 | — | .cst 模型文件绝对路径（需已求解） |
| `output_dir` | str | 否 | "" | 导出目录（空则模型同级目录） |
| `port_count` | int | 否 | None | 端口数（None 自动推断） |

返回：`{"success": true, "snp_path": "C:/.../xxx.s2p"}`；失败 `{"success": false, "error_code": "CST_EXPORT_FAILED"}`。

### `cst_export_farfield`

```python
from servers.cst import cst_export_farfield

cst_export_farfield(model_path: str, output_dir: str = "") -> dict
```

导出 CST 模型的远场方向图为 ASCII .txt（自动判断是否已求解）。无会话检测结果树，已有远场结果则直接导出；否则先 run_solver 求解再导出。任务异步串行执行，通过 cst_export_farfield_query 查询。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `model_path` | str | 是 | — | .cst 模型文件绝对路径（模型需配置 farfield 监视器） |
| `output_dir` | str | 否 | "" | 导出目录（空则模型同级目录） |

返回：`{"success": true, "task_id": "cst-a1b2...", "status": "QUEUED"}`；队列满 `{"success": false, "error_code": "CST_QUEUE_FULL"}`。

### `cst_export_farfield_query`

```python
from servers.cst import cst_export_farfield_query

cst_export_farfield_query(task_id: str) -> dict
```

查询远场导出任务：返回进度，完成时附带 farfield_txts。一次调用同时拿到进度和结果。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | str | 是 | `cst_export_farfield` 返回的 task_id |

返回（完成）：`{"success": true, "completed": true, "status": "SUCCEEDED", "farfield_txts": ["C:/.../farfield (...).txt"]}`；运行中 `{"success": true, "completed": false, "status": "RUNNING"}`；失败 `{"success": false, "error_code": "CST_EXPORT_FARFIELD_FAILED"}`。

---

## 图表（3 个）

### `list_result_curves`

```python
from servers.turbocharts.convert_raw import list_result_curves

list_result_curves(result_path: str) -> dict
```

解析 ADS RAW 仿真结果文件，返回可用曲线名和依赖轴。画图前调用避免猜测曲线名。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `result_path` | str | 是 | RAW 文件路径 |

返回：
```python
{"success": True, "format": "MDS", "datasets": [
    {"plot_name": "SP SP1[1]", "dependencies": ["freq"],
     "variables": [{"name": "S[2,1]", "type": "complex"}],
     "suggested_curves": ["DB_S[2,1]", "real_S[2,1]", ...]}
]}
```

---

### `turbocharts_convert`

```python
from servers.turbocharts.convert_raw import turbocharts_convert

turbocharts_convert(
    raw_path: str,
    img_path: str,
    chart_type: str,
    csv_path: str = "",
    linename: str = "",
    dependency: str = "",
    ac_config: str = "",
) -> dict
```

ADS RAW 结果文件转换为曲线图 + CSV。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `raw_path` | str | 是 | RAW 文件路径 |
| `img_path` | str | 是 | 输出图片路径（PNG/JPG/BMP/SVG） |
| `chart_type` | str | 是 | `"SP"` / `"HB"` / `"XDB"` |
| `csv_path` | str | 否 | 同步导出 CSV 路径 |
| `linename` | str | 否 | 曲线名，如 `"DB_S[2,1]"` |
| `dependency` | str | 否 | 依赖轴，通常 `"freq"` |
| `ac_config` | str | 否 | 精度配置 |

常用曲线名：
- `DB_S[2,1]` — 增益
- `VSWR_S[1,1]` — 驻波
- `real_nf(1)` — 噪声系数
- `real_delayS[2,1]` — 群时延

返回：
```python
{
    "success": True,
    "return_code": 0,
    "img_generated": True,
    "csv_generated": True,
    "output_paths": {"img": "C:/result/gain.png", "csv": "C:/result/gain.csv"}
}
```

---

### `compare_simulation_results`

```python
from servers.turbocharts.compare_results import compare_simulation_results

compare_simulation_results(
    result_paths: list[str],
    curve: str,
    img_path: str,
    chart_type: str = "SP",
    labels: list[str] | None = None,
    dependency: str = "freq",
    csv_path: str = "",
    alignment: str = "intersection",
    reference_index: int = 0,
) -> dict
```

多个 RAW 结果同一条曲线对比叠图（Matplotlib）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `result_paths` | list | 是 | RAW 文件路径（2-8 个） |
| `curve` | str | 是 | 曲线名 |
| `img_path` | str | 是 | 输出图片路径 |
| `labels` | list | 否 | 每条曲线标签 |
| `dependency` | str | 否 | 依赖轴（默认 "freq"） |
| `alignment` | str | 否 | 对齐方式："intersection" 或 "interpolation" |

返回：
```python
{
    "success": True,
    "image_path": "C:/compare.png",
    "curve": "DB_S[2,1]",
    "metrics": [
        {"label": "label1", "max_absolute_difference": 0.5, "rms_difference": 0.12}
    ]
}
```

---

## 图片（2 个）

### `show_image`

```python
from servers.multimodal_vision import show_image

show_image(image_path: str) -> list
```

读取本地图片，返回标准 MCP ImageContent。不复制文件，不依赖 OpenClaw。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image_path` | str | 是 | 图片文件绝对路径 |

返回 `[TextContent, ImageContent]`，其中 ImageContent 包含 Base64 编码的图片数据。
≤10MB 时内嵌图片；>10MB 时只返回本地路径供本机查看。

---

### `analyze_image`

```python
from servers.multimodal_vision import analyze_image

analyze_image(image_path: str, prompt: str = "请描述图片中的主要内容。", detail: str = "auto", max_tokens: int = 2048) -> dict
```

调用视觉模型分析本地图片内容，返回结构化文字结果。**本工具会把图片上传到第三方视觉模型**。

与 `show_image` 的区别：`show_image` 返回图片给客户端渲染；`analyze_image` 调用视觉模型识别、理解图片内容。

> **注意**：仅用户明确要求分析图片时才调用，AI 不应主动触发。配置 `VISION_API_KEY` + `VISION_BASE_URL` + `VISION_MODEL` 三项后自动开启。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `image_path` | str | 是 | — | 本地图片绝对路径（PNG/JPEG/WebP） |
| `prompt` | str | 否 | "请描述图片中的主要内容。" | 分析需求 |
| `detail` | str | 否 | "auto" | auto / low / high |
| `max_tokens` | int | 否 | 2048 | 分析结果最大长度（128-4096） |

返回：
```python
{"success": True, "model": "gpt-4o", "analysis": "图片显示...",
 "image": {"name": "result.png", "mime_type": "image/png", "size_bytes": 15231},
 "usage": {"prompt_tokens": 1200, "completion_tokens": 320, "total_tokens": 1520},
 "content_is_untrusted": True}
```

常见错误码：`VISION_NOT_CONFIGURED`、`IMAGE_NOT_FOUND`、`UNSUPPORTED_IMAGE_FORMAT`、`IMAGE_TOO_LARGE`、`VISION_TIMEOUT`、`VISION_AUTH_FAILED`、`VISION_RATE_LIMITED`、`VISION_BUSY`。

---

### `copy_image_to_workspace`（已隐藏）

当前不使用 OpenClaw，该工具已暂时隐藏（不注册为 MCP 工具、不检测工作区）。原逻辑：仅在 `OPENCLAW_WORKSPACE` 有效时注册（支持 `.env` 配置或自动检测），复制到 `media/edi/mcp-cache/`。

```python
from servers.multimodal_vision import copy_image_to_workspace

copy_image_to_workspace(image_path: str) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image_path` | str | 是 | 图片文件绝对路径 |

返回：
```python
{
    "success": True, "copied": True,
    "workspace_path": "C:/Users/JGL/.openclaw/workspace",
    "image_path": "C:/Users/.../mcp-cache/S11_a1b2c3d4.png",
    "media_path": "media/edi/mcp-cache/S11_a1b2c3d4.png",  # 相对工作区路径
    "media_type": "image/png",
    "openclaw_attachment": {"filePath": "..."}
}
```

---

## 仿真器件管理（10 个）— 协议 v3

### `get_simulation_component_schema`

```python
from servers.eda.simulation_components import get_simulation_component_schema

get_simulation_component_schema(component_type: str, parameter_name: str = "") -> dict
```

查询仿真控件支持的参数名、值类型、单位、创建/更新权限和动态参数模式。返回 `schema_version`、`protocol_version`、`parameter_patterns`。创建或修改器件前优先调用。

### `list_simulation_components`

```python
from servers.eda.simulation_components import list_simulation_components

list_simulation_components(
    project_path: str,
    component_type: str = "",
    name_contains: str = "",
    schematic_name: str = "",
    offset: int = 0,
    limit: int = 100,
) -> dict
```

本地读取原理图，列出全部器件（SP/HB/XDB/Var/Sweep/P_nToneG/TermG 等）及其当前参数。
已知类型做 wire→public 映射；其他类型返回原始 paramsinfo。支持按类型过滤、名称模糊匹配、原理图过滤和分页。

### `create_simulation_component`

```python
from servers.eda.simulation_components import create_simulation_component

create_simulation_component(project_path: str, component_type: str, timeout_seconds: int = 120) -> dict
```

新增器件（使用 EDI 器件工厂默认参数）。创建后如需设置参数，根据返回的 `instance_name` 调用 `update_simulation_component`。`component_type` 支持任意 EDI 工厂类型（SParameter / HarmonicBalance / XDB / Sweep / Var 等）。

参数格式：`{"Start": {"value": "1", "unit": "GHz"}, "Pts": {"value": "101"}}`

### `update_simulation_component`

```python
from servers.eda.simulation_components import update_simulation_component

update_simulation_component(project_path: str, instance_name: str, parameters: dict, component_type: str = "", timeout_seconds: int = 120) -> dict
```

按实例名更新仿真器件参数。优先从已保存工程自动识别器件类型；实例未保存时可显式提供 `component_type`。显式类型与实际类型不一致会返回 `COMPONENT_TYPE_MISMATCH`。只更新传入的参数，其余保持原值。

### `delete_simulation_component`

```python
from servers.eda.simulation_components import delete_simulation_component

delete_simulation_component(project_path: str, instance_name: str, timeout_seconds: int = 120) -> dict
```

按实例名删除任意原理图器件及其连接线。MCP 只校验实例名非空，最终查找、删除和回滚由 EDI 服务完成。建议调用前先查询目标实例。

### `set_component_active_state`

```python
from servers.eda.simulation_components import set_component_active_state

set_component_active_state(project_path: str, instance_name: str, state: str, timeout_seconds: int = 120) -> dict
```

确定性设置器件状态。state 接受 NORMAL / DISABLED / SHORTED（大小写不敏感）。不是状态切换，重复调用具有幂等性。

### `replace_port_component`

```python
from servers.eda.simulation_components import replace_port_component

replace_port_component(project_path: str, target_instance_name: str, replacement_component_type: str, parameters: dict | None = None, timeout_seconds: int = 300) -> dict
```

替换端口器件类型（TermG ↔ P_nToneG）。服务端保留位置、状态和外部连线。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 绝对路径 |
| `target_instance_name` | str | 是 | — | 要替换的端口实例名 |
| `replacement_component_type` | str | 是 | — | TermG / P_nToneG |
| `parameters` | dict | 否 | None | 可选参数字典 |
| `timeout_seconds` | int | 否 | 300 | 最长等待秒数 |

### `generate_schematic_from_netlist`

```python
from servers.eda.simulation_components import generate_schematic_from_netlist

generate_schematic_from_netlist(project_path: str, netlist_path: str, clear_before_import: bool = False, confirm_clear: bool = False, timeout_seconds: int = 300) -> dict
```

从网表文件导入生成 main 原理图。默认追加模式。`clear_before_import=true` 会清空原理图，必须同时传 `confirm_clear=true` 确认。

---

### `attach_out_component`

```python
from servers.eda.simulation_components import attach_out_component

attach_out_component(project_path: str, target_instance_name: str, pin_index: int | None = None, timeout_seconds: int = 120) -> dict
```

为目标器件引脚挂载 Out 器件并自动连线。单引脚器件可省略 `pin_index`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .epp 绝对路径 |
| `target_instance_name` | str | 是 | — | 目标器件实例名 |
| `pin_index` | int | 否 | None | 0 开始的目标引脚编号 |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `replace_schematic_from_file`

```python
from servers.eda.simulation_components import replace_schematic_from_file

replace_schematic_from_file(project_path: str, schematic_path: str, timeout_seconds: int = 300) -> dict
```

从 .ep 文件整体替换原理图（不追加不合并）。成功后立即保存，失败恢复原原理图状态。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .epp 绝对路径 |
| `schematic_path` | str | 是 | — | .ep 原理图文件绝对路径（完整 S-Expression） |
| `timeout_seconds` | int | 否 | 300 | 最长等待秒数 |

> ⚠️ 整体替换当前原理图，原内容丢失；自定义器件须已在工作区模型库。

---

## Resources & Prompts

除了 Tool（启动时动态统计，当前 69 个），服务还注册了只读 Resource 和可复用 Prompt 工作流模板。

### Resources（6 个）

| URI | MIME | 说明 |
|---|---|---|
| `edi://service/overview` | `application/json` | 服务版本、协议版本、gRPC 目标、安全规则 |
| `edi://reference/simulation-components` | `application/json` | 仿真器件参数目录（与 `get_simulation_component_schema` 同源） |
| `edi://reference/operation-guide` | `text/markdown` | Markdown 操作规则：创建/删除/网表导入的安全约束 |
| `edi://service/status` | `application/json` | 实时运行时状态（gRPC 通道、队列占用） |
| `edi://reference/error-codes` | `text/markdown` | gRPC 状态码词典及建议动作 |

> Resource 是只读上下文，由客户端主动拉取。标准 MCP 客户端可通过 `resources/list` 和 `resources/read` 访问。

### Prompts（8 个）

| Prompt | 参数 | 用途 |
|---|---|---|
| `inspect_edi_project` | `project_path`, `detail_level` | 只读检查工程（概览→变量→器件→仿真配置） |
| `run_and_review_simulation` | `project_path`, `execution_mode`, `analyze_log` | 统一异步仿真流程 + 日志分析 |
| `configure_simulation_component` | `project_path`, `action`, `component_type`, `instance_name`, `requirements` | Schema→参数→确认→创建/更新 |
| `create_simulation_report` | `project_path`, `output_path`, `overwrite` | 查询工程 → 生成曲线 → 渲染 PDF/DOCX |
| `troubleshoot_edi_error` | `status`, `error_code` | 按状态码查错误词典、检查服务状态、给排查建议 |

> Prompt 是用户主动选择的工作流模板。标准 MCP 客户端可通过 `prompts/list` 和 `prompts/get` 访问。

> 注意：内置 EDI Chat (`/ui`) 当前主要消费 Tool，不自动拉取 Resources 或 Prompts。能否在客户端中显示取决于具体 MCP 客户端的实现。

---

## 文档（1 个）

### `open_document`

```python
from servers.multimodal_vision import open_document

open_document(file_path: str, mode: str = "link", disposition: str = "inline") -> dict
```

打开本地文档：link 模式生成 10 分钟临时 HTTP 链接，local 模式用系统默认程序打开（os.startfile）。支持 10 种格式。仅用户明确要求时调用。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_path` | str | 是 | — | 本地绝对路径（.pdf/.doc/.docx/.xls/.xlsx/.ppt/.pptx/.txt/.csv/.rtf） |
| `mode` | str | 否 | "link" | "link"（生成 HTTP 链接）或 "local"（系统默认程序打开） |
| `disposition` | str | 否 | "inline" | link 模式下：inline（预览）或 attachment（下载） |

返回（link 模式）：`{"success": True, "url": "http://...", "file_name": "...", "expires_in": 600, "markdown_link": "[...](...)"}`

返回（local 模式）：`{"success": True, "status": "OPEN_REQUESTED", "file_path": "...", "file_type": ".pdf"}`

---

## 报告（1 个）

### `generate_simulation_report`

```python
from servers.report import generate_simulation_report

generate_simulation_report(
    output_path: str,
    model_name: str,
    description: str = "",
    conclusion: str = "",
    spec_table: list | None = None,
    charts: list | None = None,
    components: list | None = None,
    schematic: str = "",
    overwrite: bool = False,
    timeout_seconds: int = 45,
) -> dict
```

生成本地仿真报告（PDF/DOCX），调用本地报告渲染服务 `POST /api/v1/reports/render`。只负责数据校验和渲染，不会自动执行仿真或编造数据。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `output_path` | str | 是 | — | 输出文件绝对路径，后缀决定格式（.pdf/.docx） |
| `model_name` | str | 是 | — | 封面标题/型号名（最长 200 字符） |
| `description` | str | 否 | "" | 产品简介（多行文本） |
| `conclusion` | str | 否 | "" | 结论文字 |
| `spec_table` | list | 否 | None | 电参数表二维数组（7 列，第一行表头） |
| `charts` | list | 否 | None | 曲线图片 `[{"path":..., "title":...}]`，最多 50 张 |
| `components` | list | 否 | None | 器件选型 `[{"type","model","manufacturer","specs"}]`，最多 500 条 |
| `schematic` | str | 否 | "" | 原理图图片绝对路径 |
| `overwrite` | bool | 否 | False | 输出文件已存在时是否覆盖 |
| `timeout_seconds` | int | 否 | 45 | 请求超时秒数（5-120） |

**重要规则**：
- 默认禁止覆盖已存在文件（`overwrite=false`）
- 器件厂家和规格不得根据型号名称猜测
- 没有要求值时结果填"未判定"
- 图片缺失不阻塞生成，返回中记录 warning
- 报告服务不可用时返回 `REPORT_SERVICE_UNAVAILABLE`

配置：`REPORT_RENDER_URL`（默认 `http://127.0.0.1:17867/api/v1/reports/render`）、`REPORT_RENDER_TIMEOUT_SECONDS`（默认 45）。

---

## 辅助

### Chat 上下文

`servers/chat/service.py` 维护会话级状态，支持多轮对话：

```python
from servers.chat.service import ChatService

svc = ChatService.instance()
response = await svc.chat(session_id="abc123", message="打开第一个工程")
# response: ChatResponse(success=True, reply="已打开 demo1.epp", activities=[...], context={...}, media=[...])
```

会话自动记住当前工程路径和最近仿真 task_id，消除重复输入。

---

## gRPC 工具统一返回格式

所有 gRPC 工具（19 个）的返回结构统一如下（完整字段）：

```json
{
    "success": true,
    "completed": true,
    "outcome_known": true,
    "task_success": true,
    "client_uuid": "a1b2c3d4",
    "task_id": "e5f6g7h8",
    "task_type": "OPEN_PROJECT",
    "status": "SUCCEEDED",
    "message": "task completed",
    "project_path": "C:/test.epp",
    "result_path": "",
    "ads_output": "",
    "log_complete": true,
    "details": {}
}
```

各 `status`（成功 / 失败情况）语义：

| status | 含义 | success | outcome_known | task_success |
|---|---|---|---|---|
| `SUCCEEDED` | 任务成功 | true | true | true |
| `FAILED` | EDI 明确返回失败 | false | true | false |
| `REJECTED` | EDI 未受理（参数/权限问题） | false | true | false |
| `TIMEOUT` | 超时，EDI 结果未知 | false | false | null |
| `STREAM_DISCONNECTED` | 长连接中断，结果未知 | false | false | null |
| `GRPC_UNAVAILABLE` | 无法连接 EDI | false | false | null |
| `PROTOCOL_MISMATCH` | 协议字段不一致 | false | false | null |
| `PAYLOAD_TOO_LARGE` | 返回消息过大（>256MB） | false | false | null |

字段语义：
- `completed` — MCP 侧本次调用结束；TIMEOUT/STREAM_DISCONNECTED 也是 `completed=True`
- `outcome_known` — 收到 EDI 最终事件（SUCCEEDED/FAILED）时为 True；超时/断连时为 False
- `task_success` — 只有 `outcome_known=True` 时才有确定值；`None` 表示 EDI 实际状态未知
- `failure_source: "mcp"` — MCP 进程自身异常（如无法连接 EDI），不属于 EDI 业务失败

纯本地工具（如 `list_epp_projects`）使用各自的简化结构。
