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

- **[工程管理（10 个）](#工程管理10个)**：创建 / 扫描 / 打开 / 关闭工程、查询器件、分析变量
- **[仿真（6 个）](#仿真6个)**：同步 / 异步仿真、网表仿真、抗烧毁评估、任务查询
- **[导出与分析（4 个）](#导出与分析4个)**：导出网表、截图原理图、器件 CSV、信号链路追踪
- **[模型库 / 原理图库（6 个）](#模型库--原理图库6个)**：模型替换、模型分类/查询/搜索、性能器件放置、拓扑设计
- **[软 IP（1 个）](#软ip1个)**：按设计描述下载芯片模型
- **[微波计算（7 个）](#微波计算7个)**：传输线特性阻抗 / 线宽计算、多级威尔金森功分器设计、滤波器阶数评估（高通/低通/带通/带阻）、功分器/巴伦节数（本地纯计算，无任务信封）
- **[射频链路计算（2 个）](#射频链路计算2个)**：混频杂散枚举、噪声底 / 灵敏度 / 压缩受限动态范围（本地纯计算，无任务信封）
- **[工作区（3 个）](#工作区3个)**：创建 / 切换 / 查询当前工作区
- **[原理图扩展（5 个）](#原理图扩展5个)**：内置器件、清空原理图、连线、批量器件变换
- **[启动 / 诊断（3 个）](#启动--诊断3个)**：启动 EDI、服务诊断、日志读取
- **[ANSYS HFSS（7 个）](#ansys-hfss7个)**：AEDT 工程开关、退出、HFSS 异步仿真
- **[CST 电磁仿真（13 个）](#cst电磁仿真13个)**：异步求解 .cst、导出 S 参数 / 远场方向图
- **[LASPCEM 电磁仿真（11 个）](#laspcem电磁仿真11个)**：启动 LASPCEM、打开 .sem 工程、aedt 转 sem、异步求解（可带导出/建报告后处理）、任务查询与取消、同步导出 S 参数 / 建报告、读取求解设置
- **[图表（3 个）](#图表3个)**：RAW 曲线解析、转图、结果对比
- **[图片（2 个）](#图片2个)**：显示图片、视觉分析
- **[仿真器件管理（10 个）](#仿真器件管理10个协议-v3)**：器件 Schema、增删改、状态、网表导入、原理图加载
- **[Resources & Prompts](#resources--prompts)**：只读资源 + 可复用工作流
- **[文档（2 个）](#文档2个)**：打开本地文档、下载任意产物
- **[独立仿真工具（4 个）](#独立仿真工具4个)**：报告导出 exe、TR 链路仿真 exe、混频链路仿真 exe、XY 绘图 exe（固定目录直接调 exe）
- **[辅助](#辅助)**：Chat 上下文
- **[gRPC 工具统一返回格式](#gRPC工具统一返回格式)**：gRPC 工具的返回结构 + status 语义

---

## 工程管理（10 个）

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

list_schematic_components(project_path: str, timeout_seconds: int = 60, summary_only: bool = False, limit: int = 50, offset: int = 0) -> dict
```

列出当前原理图上存在的所有器件及器件对应参数，参数包括器件属性和位置几何信息（电阻值、位置坐标、引脚索引和引脚相对坐标、旋转角度、水平/垂直镜像等），比本地文件读取更实时，能看到 EDI 未保存的修改和运行态（active_state/state）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |
| `summary_only` | bool | 否 | False | 每项只留标量字段（instance_name/component_type/active_state/state 及 rotation_deg/mirrored_*），裁掉 parameters 与 position/pins；需要坐标或引脚时改用 limit 分页 |
| `limit` | int | 否 | 50 | 每页条数；0 = 全部 |
| `offset` | int | 否 | 0 | 从第几个器件开始返回，配合 limit 翻页 |

返回（gRPC 统一结构，业务字段在 `details` 中）：
```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "component_count": 5, "total": 5, "offset": 0, "limit": 50,
        "returned": 5, "truncated": False,
        "components": [
            {"instance_name": "R1", "component_type": "R",
             "general_type": "", "sub_type": "",
             "active_state": 0, "state": "NORMAL",
             "uuid": "...", "component_uuid": "",
             "position": {"x": 1125, "y": 412.5},
             "rotation_deg": 90,
             "mirrored_horizontal": False, "mirrored_vertical": False,
             "pins": [{"index": 0, "local_position": {"x": 0, "y": 0},
                       "position": {"x": 1125, "y": 412.5}, "uuid": "..."}],
             "parameters": {...}}
        ]
    }
}
```

属性与几何来自同一次 `LIST_SCHEMATIC_COMPONENTS`（不再逐器件补查）。`Var` / `Sweep` / `SParameter` / `HB(XDB)` / 形状类（`EBOND_ShapeJedec`）等非图形器件本来就没有引脚，`pins` 返回 `[]` 属正常，不代表数据缺失；判断几何是否完整看 `position`。

---

### `get_schematic_component_info`

```python
from servers.eda.project_manage import get_schematic_component_info

get_schematic_component_info(project_path: str, instance_name: str | list[str], timeout_seconds: int = 60) -> dict
```

按实例名查询器件参数与几何（器件属性 + 位置/旋转/镜像/引脚），实时，含未保存修改和 active_state/state。支持单个名称，也支持名称列表批量查询。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `instance_name` | str \| list[str] | 是 | — | 器件实例名（如 "R1"）或实例名列表，取自 list_schematic_components 返回 |
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
            "active_state": 0, "state": "NORMAL", "parameters": {...},
            "position": {"x": 100, "y": 200}, "rotation_deg": 0,
            "mirrored_horizontal": false, "mirrored_vertical": false, "pins": [...]
        }
    }
}
```

传 `instance_name` 为列表时，`details` 含 `component_count` + `components`（元素结构与 `component` 一致，结果与请求顺序一致）；**任一实例不存在则整批失败**（`message` 会指出是哪个实例）。批量没有分页，每器件约 1.2 KB，一次建议不超过 10–20 个，全量请用 `list_schematic_components` 分页。

---

### `get_components_static_params`

```python
from servers.eda.project_manage import get_components_static_params

get_components_static_params(
    component_uuids: list[str],
    timeout_seconds: int = 60,
) -> dict
```

查询器件的重量、尺寸、封装、所属厂商、成本等。传入参数 `component_uuids` 从 `get_schematic_component_info` 接口返回的 `component_uuid` 获得，内部转成 `original_uuids` 后转发到 `POST /api/v1/components/static-params/`，返回上游完整响应（code/message/data）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `component_uuids` | list | 是 | — | 器件 UUID 数组（从 get_schematic_component_info 返回的 component_uuid 获得） |
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

### `batch_query_component_rf_params`

```python
from servers.eda.project_manage import batch_query_component_rf_params

batch_query_component_rf_params(
    model_name_list: list[str],
    timeout_seconds: int = 60,
) -> dict
```

按器件型号字符串列表批量查询模型的频率、增益、回波损耗、抑制、P-1、Psat、隔离、插损、衰减等信息，返回对齐到输入顺序的结果。器件型号串数组（如 HGC259、NC10424C_812），来源二选一：① 选型阶段 `search_public_models` / `search_personal_models` 返回的 `model_name`；② 工程内已有的性能器件 `list_schematic_components` 返回的 `component_type`，仅当该器件 `component_uuid` 非空。内置/仿真器件（TermG、P_nToneG、SParameter、Var、Sweep、XDB、Out）的 `component_type` 是类型名不是型号，传入后只会进 missing，不要用它们。只接受型号串，不接受 UUID（按 UUID 查固有参数用 `get_components_static_params`）。不需要打开工程，也不需要 `project_path`。gRPC 服务将请求转发到 `POST /api/v1/models/manage/batch_query_component/`。

下游模型服务只返回命中的型号且不保序。本工具会将返回的 `data` 按输入 `model_name_list` 顺序对齐：未命中的型号补 `null` 占位，并在 `missing` 中列出未命中的型号。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `model_name_list` | list | 是 | — | 器件型号字符串数组（如 `"MAAD_008866"`），顺序和重复项保留 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

返回（gRPC 统一结构，业务字段在 `details` 中）：

```python
{
    "success": True,
    "status": "SUCCEEDED",
    "details": {
        "code": 200,
        "message": "器件批量查询成功",
        "data": [
            {"model": "MAAD_008866", "manufacturer": "MACOM", "type": "...", "specs": "...", "description": "..."},
            null
        ],
        "missing": ["未命中的型号"]
    }
}
```

`data` 与输入 `model_name_list` 顺序一一对应，未命中项为 `null`；`missing` 列出未命中的型号。

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

## 仿真（6 个）


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
`success` 反映仿真业务结果（与 `get_simulation_async_result` 一致）：运行中为 true；终态时等于 `task_success`（结果未知如 TIMEOUT 时为 false）。

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
`success` 反映仿真业务结果：SUCCEEDED 为 true；FAILED/TIMEOUT/断连 为 false（权威细粒度信号看 `task_success` / `outcome_known`）。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | str | 是 | `start_simulation_async` 返回的 task_id |

运行中返回：
```python
{"success": True, "completed": False, "status": "RUNNING", "ads_output": "..."}
```

完成后返回（成功）：
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

完成但失败 / 超时 / 断连时 `success` 为 `false`：`FAILED` → `task_success=false, outcome_known=true`；`TIMEOUT`/`STREAM_DISCONNECTED` → `task_success=null, outcome_known=false`。

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

capture_schematic(project_path: str, output_path: str, timeout_seconds: int = 60) -> dict
```

截取原理图为图片（PNG/JPG 等）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `output_path` | str | 否 | 默认产物目录 | 输出图片路径；留空落到 `<result_dir>/capture_schematic_<时间戳>.png` |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `export_schematic_components_to_csv`

```python
from servers.eda.design_export import export_schematic_components_to_csv

export_schematic_components_to_csv(project_path: str, csv_path: str, timeout_seconds: int = 60) -> dict
```

将工程原理图中的有效器件信息导出为 CSV，用于器件替换和 replace_schematic_components_from_csv 接口配对使用，不单独使用。导出列为 original_model_type/name/id 与 alternative_model_type/name/id（后三列留空），供 replace_schematic_components_from_csv 后续模型替换填写。仿真控制器、端口、变量等 EXCLUDED_TYPES 不导出。csv_path 未以 .csv 结尾时服务端自动追加后缀；父目录必须已存在。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `csv_path` | str | 否 | 默认产物目录 | CSV 输出文件路径；留空落到 `<result_dir>/export_schematic_components_to_csv_<时间戳>.csv` |
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

## 模型库 / 原理图库（6 个）

### `replace_schematic_components_from_csv`

```python
from servers.eda.model_replace import replace_schematic_components_from_csv

replace_schematic_components_from_csv(project_path: str, csv_path: str, timeout_seconds: int = 60) -> dict
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

### `add_performance_component`

```python
from servers.eda.model_library import add_performance_component

add_performance_component(project_path: str, component_uuid: str, position: dict, timeout_seconds: int = 120) -> dict
```

按指定坐标新增性能模型器件，只放置器件，不修改器件属性。`component_uuid` 必须是 `get_current_workspace_summary` 接口返回的性能模型的 uuid，坐标会吸附到网格。只放置器件、不修改器件属性，不自动下载模型、不设置参数、不避让重叠、不返回实例名。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `component_uuid` | str | 是 | — | 性能模型 UUID（从 get_current_workspace_summary 返回获取） |
| `position` | dict | 是 | — | 场景坐标 `{"x": .., "y": ..}` |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `design_schematic_topology`

```python
from servers.eda.model_library import design_schematic_topology

design_schematic_topology(requirement: str, timeout_seconds: int = 180) -> dict
```

按自然语言需求设计电路拓扑（收发组件、滤波器、天线等），在当前工作区新建并打开工程。
生成后建议核对工程是否与需求一致：比对 `details.component_count` / `schematic_id`，或读工程目录里的 `拓扑描述.txt`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `requirement` | str | 是 | — | 自然语言需求（非空） |
| `timeout_seconds` | int | 否 | 180 | 最长等待秒数（含取拓扑 + 下载 + 模型库扫描），上限 300 |

返回（gRPC 统一结构，业务字段在 `details` 中）：

```python
{"success": True, "status": "SUCCEEDED", "task_success": True, "message": "在线链路工程已打开",
 "details": {"component_count": 198, "net_segment_count": 228,
             "project_name": "KuKa波段双频TR组件_理想",
             "schematic_id": "f5289ae1-e330-4a70-be09-5f97e3b8c50a", "status": "SUCCESS"}}
```

⚠️ 已知口径：① `details.project_name` 回**基名**——同名工程第二次调用时真实目录会变成 `..._2`（只有 `project_path` 是准的）；② 每次调用都**新建工程目录**，无「复用已有工程」选项。

---

## 软 IP（1 个）

### `design_chip`

```python
from servers.eda.soft_ip import design_chip

design_chip(description: str, save_path: str, freq: float | None = None, bandwidth: float | None = None, timeout_seconds: int = 120) -> dict
```

按用户输入的指标设计芯片电路（天线、滤波器、负载等），生成对应的 AEDT 模型文件，落到 `save_path` 指定路径。阶数/拓扑/指标由所选模型自身决定，本工具不校验指标、也不回模型名。描述写不中 → `FAILED`「指定分类下未找到该描述的模型」。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `description` | str | 是 | — | 模型设计需求（非空）：电路类型（带通/低通滤波器、天线、负载…）+ 指标 |
| `save_path` | str | 否 | 默认产物目录 | 目标文件完整路径（如 `D:/soft-ip/PCB.aedt`）；留空落到 `<result_dir>/design_chip_<时间戳>.aedt` |
| `freq` | float | 否 | None | 可选中心频率（>0，GHz）；未提供时服务端按该软 IP 插值范围中点补齐 |
| `bandwidth` | float | 否 | None | 可选**相对带宽，单位 %**（5 = 5%）；未提供时同上。⚠️ 部分软 IP **必填**（不给 → `FAILED`「缺少相对带宽（bandwidth,单位:%）」）；超出有效范围（实测带通模板 **[1.2267, 6.27922]%，与 freq 无关**：20/25/30 GHz 同区间）也会 `FAILED` |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

返回（gRPC 统一结构）：`{"success": true, "status": "SUCCEEDED", "details": {"description": "...", "freq": 20, "bandwidth": 6, "save_path": "D:/soft-ip/BPF_20G.aedt", "status": "MODEL_DESIGN_COMPLETED"}}`。

⚠️ **产物路径读 `details.save_path`**（`result_path` 恒为空串）；`details` 只回 `description/freq/bandwidth/save_path/status`。

---

## 工作区（4 个）

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

立即切换当前工作区，当前正在进行的任务要及时关闭保存，避免丢失。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `path` | str | 是 | — | 工作区目录绝对路径 |
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `get_current_workspace_path`

```python
from servers.eda.workspace_ops import get_current_workspace_path

get_current_workspace_path(timeout_seconds: int = 60) -> dict
```

查询程序当前实际加载的工作区目录。只读查询，不修改状态。`switch_workspace` 立即生效，本工具返回的就是切换后的当前工作区。成功时 `details` 含 `workspace_path`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

### `get_current_workspace_summary`

```python
from servers.eda.workspace_ops import get_current_workspace_summary

get_current_workspace_summary(timeout_seconds: int = 60) -> dict
```

获取当前工作区概要：工作区内全部 `.epp` 工程路径 + 本地性能模型（uuid / 子类型 / 型号名称）。只读查询，不需要 `project_path`，也不需要打开工程，但需要 EDI 在线。

`details` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `workspace_path` | str | 当前工作区目录 |
| `epp_files` | list[str] | 工作区内全部 `.epp` 工程文件路径 |
| `models` | dict | 服务端原样结构：`{子类型名: [{uuid, name}]}`，仅含 `config.ini` 的 `sub_types` 能匹配到名称的性能模型 |
| `models_flat` | list[dict] | 由 `models` 摊平：`[{sub_type, uuid, name}]`，取单个模型信息时用这个 |
| `epp_count` / `model_count` | int | 工程数 / 模型数 |

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `timeout_seconds` | int | 否 | 60 | 最长等待秒数 |

---

## 原理图扩展（5 个）

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

按指定坐标新增内置器件，只放置器件，不修改器件属性。严格按场景坐标放置，不做网格吸附、自动排布或重叠避让。`component_type` 必须是 `list_ideal_components` 接口返回数据中存在的 component_type（区分大小写）。只放置器件、不修改器件属性，后续参数设置用 `update_components_from_schematic`。

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

连接两个器件的指定引脚，按需创建或合并网段并保存。器件引脚可以在 list_schematic_components 和 get_schematic_component_info 获取。`pin_index` 是 0 开始的内部引脚编号（非界面端口名称），范围 0~2147483647。拒绝同一引脚自连接。新增连线及网段合并作为可撤销操作，提交后保存工程。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `first_instance_name` | str | 是 | — | 第一个器件实例名 |
| `first_pin_index` | int | 是 | — | 第一个器件引脚编号（0 开始） |
| `second_instance_name` | str | 是 | — | 第二个器件实例名 |
| `second_pin_index` | int | 是 | — | 第二个器件引脚编号（0 开始） |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

### `update_schematic_components_transform`

```python
from servers.eda.schematic_ops import update_schematic_components_transform

update_schematic_components_transform(project_path: str, components: list[dict], timeout_seconds: int = 120) -> dict
```

批量设置原理图器件的位置、旋转角度和镜像状态（gRPC 枚举 `64`）。按实例 `uuid` 或 `instance_name` 定位，`position` / `rotation_deg` / `mirrored_horizontal` / `mirrored_vertical` 均为目标绝对状态，省略字段保持原值；整批校验，任一项非法整批不修改，有改动时形成一个撤销操作并保存。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 文件绝对路径 |
| `components` | list | 是 | — | 非空数组，每项 `{uuid?, instance_name?, position?, rotation_deg?, mirrored_horizontal?, mirrored_vertical?}`；每项至少提供 `uuid` 或 `instance_name`，且至少一个变换字段 |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

返回：gRPC 统一返回结构，成功时 `details` 含 `project_path` / `component_count` / `components`（本批器件更新后完整信息，含引脚坐标）。

---

## 启动 / 诊断（3 个）

### `launch_edi`

```python
from servers.eda.edi_launcher import launch_edi

launch_edi(edi_path: str = "", wait_for_grpc: bool = True, timeout_seconds: int = 30) -> dict
```

启动 EDI 客户端并等待 gRPC 就绪。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `edi_path` | str | 否 | .env 配置 | EDI.exe 路径 |
| `wait_for_grpc` | bool | 否 | True | 是否等待 gRPC 端口就绪 |
| `timeout_seconds` | int | 否 | 30 | 等待超时秒数 |

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
    "log_file": "<EDI 根>/logs/eda_YYYY-MM-DD.log",
    "total_lines": 1280,
    "matched_lines": 42,
    "lines": ["...", "..."],
    "error_count": 5,
    "warning_count": 8,
    "exception_lines": ["Traceback ...", "..."],
    "message": "日志共 1280 行，发现 5 个错误、8 个警告。",
}
```

日志路径由 `.env` 的 `EDI_LOG_DIR` 配置，默认空 = 自动检测（EDI 根下 `logs`，往上找 EDI 根）。

---

## ANSYS HFSS（7 个）

### `open_hfss_project`

```python
from servers.ansys.project_manage import open_hfss_project

open_hfss_project(project_path: str, aedt_path: str = "", timeout_seconds: int = 30) -> dict
```

启动 AEDT 并打开 .aedt 项目（COM 附着优先，subprocess 单次启动兜底）。流程：检查锁文件 → 清理失效锁 → COM 附着打开或 subprocess 启动 → 轮询确认工程打开。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .aedt/.aedtz 文件绝对路径 |
| `aedt_path` | str | 否 | 自动检测 | ansysedt.exe 路径 |
| `timeout_seconds` | int | 否 | 30 | 等待超时秒数（1-120） |

返回：`{"success": true, "status": "opened/already_open", "project_opened": true, "method": "com/subprocess", "duration_s": 1.2}`；失败 `{"success": false, "status": "invalid_path"/"aedt_not_found"/"project_locked"/"com_open_failed", "message": "..."}`

### `close_hfss_project`

```python
from servers.ansys.project_manage import close_hfss_project

close_hfss_project(project_name: str = "", project_path: str = "", save_before_close: bool = False) -> dict
```

关闭 AEDT **工程**（File → Close Project）：不退出 AEDT、绝不杀进程。
用于「关掉这个工程、AEDT 还要继续用别的工程」/「只释放某个工程的占用或锁」/「关闭前按 COM 语义保存」。
要退出 AEDT 程序用 `quit_aedt`；要硬杀进程用 `quit_aedt(force=True, confirm=True)`；要打开工程用 `open_hfss_project`。
不传 `project_name` 时关**当前活动工程**。

关键语义：
- **成败判据是事后核对**（`CloseProject` 后再看 `GetProjectList()` / 锁文件），不是 COM 有没有抛异常：AEDT 收尾阶段抛 `DISP_E_EXCEPTION` 时工程往往已关掉，此时返回 `success=true` + `closed_despite_exception=true`。
- `project_name` 不在已打开列表 → `NO_SUCH_PROJECT`；`COM 附着失败` → `COM_UNAVAILABLE`；AEDT 没跑 → `AEDT_NOT_RUNNING`；≥2 个 AEDT 实例 → `MULTI_INSTANCE_UNSUPPORTED`；COM 30s 不返回 → `AEDT_NOT_RESPONDING`。
- `project_path` 为空时用 COM 的 `GetPath()` 回填，用于只读检测与锁清理。
- 工程文件只读时 `readonly_project_file=true` + `warnings` 要求转告用户（AEDT 保存会弹「写入文件失败」框）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_name` | str | 否 | "" | 项目名，为空关闭活动项目 |
| `project_path` | str | 否 | "" | 项目路径，用于只读检测与锁清理 |
| `save_before_close` | bool | 否 | False | 关闭前保存（只读工程跳过并告警） |

返回：`{"success": true, "method": "com", "project_closed": true, "message": "已关闭: demo", "open_projects_after": [], "readonly_project_file": false, "lock_path": "...", "lock_exists_after": false, "warnings": []}`

### `quit_aedt`

```python
from servers.ansys.project_manage import quit_aedt

quit_aedt(force: bool = False, confirm: bool = False, timeout_seconds: int = 30) -> dict
```

退出 AEDT **程序**（关掉整个 AEDT 窗口、收掉进程）：不是关工程。
用于「工程已关、AEDT 停在空窗口要收掉」/「释放 AEDT 的内存或许可证」/「实例卡死（未响应）且内容可丢弃」。
只关某个工程用 `close_hfss_project`（进程保留）；硬杀必须 `force=True`+`confirm=True`（会终止机器上**所有** ansysedt.exe）。

**`confirm` 有两个用处**：① `force=True` 必须配 `confirm=True`；② 目标实例**不是本服务启动的**（用户手开/别的工具开）时默认回 `CONFIRM_REQUIRED`（附 PID、`open_projects`），要 `confirm=True` 才退 —— 避免替别人关掉他正在用的 AEDT。本服务自己启动的实例无需确认。

顺序：COM `QuitApplication` → 给主窗发 `WM_CLOSE`（等同点 X）→ 只剩 `-Embedding` 辅助进程时**自动清理**（无窗口、无工程，`method` 会带 `+helper_cleanup`）→ 仍不退出回 `STILL_RUNNING`（**不自己硬杀**）。

`force=True`（需 `confirm`）走 `taskkill /F`，会**多轮重试**（`-Embedding` 会以新 PID 再生）；若服务的令牌权限不足会明确回 `PERMISSION_DENIED` + `pids_left`（提示以管理员运行服务或手工结束），而不是笼统的 `KILL_FAILED`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `force` | bool | 否 | False | True = 直接 `taskkill /F`；**必须同时传 `confirm=True`**，否则回 `CONFIRM_REQUIRED` |
| `confirm` | bool | 否 | False | 二次确认闸门：`force=True` 或「目标 AEDT 不是本服务启动的」时必须为 True |
| `timeout_seconds` | int | 否 | 30 | 等 AEDT 退出的秒数（5–120） |

返回：`{"success": true, "status": "quit", "method": "com", "pids_before": [12345], "pids_after": [], "open_projects_before": [], "data_loss_risk": false, "warnings": []}`

### `launch_aedt`

```python
from servers.ansys.project_manage import launch_aedt

launch_aedt(aedt_path: str = "", timeout_seconds: int = 30) -> dict
```

启动 AEDT（不打开项目）。已运行时仅返回状态。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `aedt_path` | str | 否 | 自动检测 | ansysedt.exe 路径 |
| `timeout_seconds` | int | 否 | 30 | 等待超时秒数 |

返回：`{"success": true, "status": "started"/"already_running", "com_ready": true, "pid": 12345, "pids": [12345, 12346], "message": "..."}`

- `started` 与 `already_running` **两个分支都会按 `timeout_seconds` 等 COM 就绪**，并都返回 `pid` / `pids`。
- `pids` 是列表：AEDT 启动后会 re-exec 出子进程，单 `pid` 会漏掉另一个。
- `success=true` 只代表**进程层面**完成（ansysedt.exe 在跑）；能不能用 COM 要看 `com_ready`。

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

## CST 电磁仿真（13 个）

> 本节覆盖全部 13 个 CST 工具。

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
| `output_dir` | str | 否 | 默认产物目录 | 导出目录；留空落到 `<result_dir>/cst_export_snp_<时间戳>/` |
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
| `output_dir` | str | 否 | 默认产物目录 | 导出目录；留空落到 `<result_dir>/cst_export_farfield_<时间戳>/` |

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

### `cst_open_project`

打开并保存 CST 模型（= 解包归档文件），不求解。

```python
cst_open_project(model_path: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | string | 是 | .cst 模型文件绝对路径（归档文件也行）。 |

example: `{"model_path": "<string>"}`

### `cst_get_model_info`

只读查看 CST 模型状态：是否已解、是否解包、端口数、远场监视器有无。

```python
cst_get_model_info(model_path: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | string | 是 | .cst 模型文件绝对路径。 |

example: `{"model_path": "<string>"}`

### `cst_list_results`

列出 CST 模型结果树里的全部条目（S 参数 / 远场等）与监视器。

```python
cst_list_results(model_path: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | string | 是 | .cst 模型文件绝对路径。 |

example: `{"model_path": "<string>"}`

### `cst_plot_farfield`

把 CST 远场 ASCII .txt 画成方向图（极坐标切面 + 直角曲线各一张 PNG）。

```python
cst_plot_farfield(txt_path: string, cut: string = "phi=0", output_dir: string = "") -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `txt_path` | string | 是 | 远场 txt 绝对路径（cst_export_farfield 返回的 farfield_txts 之一）。 |
| `cut` | string | 否 | 切面，格式 phi=0 或 theta=0（默认 phi=0）。 |
| `output_dir` | string | 否 | 图片输出目录；空则落到默认产物目录（<result_dir>/cst_plot_farfield_<时间戳>/）。 |

example: `{"txt_path": "<string>"}`

### `cst_export_snp_async`

异步提交 CST 的 S 参数导出，立即返回 task_id，用 cst_export_snp_query 取结果。想一步到位用同步版 cst_export_snp。

```python
cst_export_snp_async(model_path: string, output_dir: string = "", port_count: any = null) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `model_path` | string | 是 | .cst 模型文件绝对路径（需已求解，存在 S 参数结果）。 |
| `output_dir` | string | 否 | 导出目录；空则落到默认产物目录（<result_dir>/cst_export_snp_<时间戳>/）。 |
| `port_count` | any | 否 | 端口数（None 则自动从结果推断）。 |

example: `{"model_path": "<string>"}`

### `cst_export_snp_query`

查询 CST 的 S 参数导出任务进度（配合 cst_export_snp_async 的 task_id）。求解任务用 cst_solve_query。

```python
cst_export_snp_query(task_id: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | cst_export_snp_async 返回的 task_id。 |

example: `{"task_id": "<string>"}`

### `cst_task_list`

列出当前所有 CST 异步任务（求解 / 导出）及状态。

```python
cst_task_list() -> dict
```

### `cst_task_cancel`

⚠️ 取消一个 CST 异步任务（会终止运行中的求解）。

```python
cst_task_cancel(task_id: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | cst_task_list 或各 cst_*_async 返回的 task_id。 |

example: `{"task_id": "<string>"}`

## LASPCEM 电磁仿真（11 个）

> LASPCEM（西安电子科技大学国产电磁仿真平台）无 COM/gRPC 接口，程序化入口是 GUI 内嵌 Python 脚本（`app`/`cem`/`report`/`viewer` 全局对象），用 `laspcem /session <脚本>` 执行（会弹 GUI，需要可见桌面会话）。
>
> **实测要点（2026-11-12，BuildNo 28167，2 端口 CPW 模型）**
> - `cem.FEMAnalyze` 是**异步**的：调用立即返回，真正求解在 GUI 里跑 `mpiexec + phofem`，结果由 GUI 自己写回 `.sem`。脚本里**不能**跟 `SaveAs`/`app.Exit()`（会把求解器掐死）。
> - 脚本首行必须是 `#Version Information:` + `app.SetScriptVersion(...)`，否则宿主拒绝执行；给脚本套 `try/except` 日志包装会导致求解不启动。
> - setup 名传**显示名**（`.sem` 里存的是 `Setup1(FEM)`，API 要用 `Setup1`）。
> - 判定"求解完成"看**求解器进程**：见过 `mpiexec/phofem` → 等它们退出且 `.sem` 安静 15 s。只看解序号不行（同一 setup 重解是**覆盖**，不新增序号）。
> - 工程已有解且模型没变化时，LASPCEM 会跳过重算（Message: `already calculated`）——正常行为，本组工具**不提供强制重解**；需要重算就让模型真的变化。
> - 实测耗时：一次完整求解 ≈ **220 s**；未求解工程"求解→导出" ≈ **238 s**；已求解工程导出 **2–5 s**。

### `launch_laspcem`

```python
from servers.laspcem.launcher import launch_laspcem

launch_laspcem(laspcem_path: str = "") -> dict
```

启动 LASPCEM 软件。已运行时返回状态，不重复启动。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `laspcem_path` | str | 否 | 自动检测 | laspcem.exe 路径（LASPCEM_PATH → 注册表卸载项 → 注册表 .sem 关联） |

返回：`{"success": true, "started": bool, "already_running": bool, "exe": "...", "message": "..."}`。

### `open_laspcem_project`

```python
from servers.laspcem.launcher import open_laspcem_project

open_laspcem_project(project_path: str, laspcem_path: str = "", force_new_instance: bool = False) -> dict
```

在 LASPCEM 中打开 .sem 工程（等价于双击 .sem）。LASPCEM 无进程间投递，带文件启动一律新起 GUI 实例，故本工具做同工程去重：目标 .sem 已在某实例命令行里则复用；`force_new_instance=True` 强制新开。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .sem 文件绝对路径 |
| `laspcem_path` | str | 否 | 自动检测 | laspcem.exe 路径 |
| `force_new_instance` | bool | 否 | False | 该工程已打开时也强制再起新实例 |

返回：`{"success": true, "exe": "...", "project": "...", "reused_existing": bool, "new_instance": bool, "pid": int|null, "running_instances": [pid,...], "message": "..."}`。

### `close_laspcem_project`

关闭在 LASPCEM 中打开某个 `.sem` 的 GUI 实例（等价于关窗口）。

```python
close_laspcem_project(project_path: str, force: bool = False) -> dict
# → {"success": true, "project": "...", "closed": [29792], "remaining": [], "message": "..."}
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.sem` 绝对路径。**不要求文件仍存在** —— 工程被删/改名后，实例 cmdline 仍可能指向原路径，这种残留更要能关掉 |
| `force` | bool | 否 | False | terminate 超时后再 kill；默认只 terminate 并等 5 s |

- 按 **cmdline 里的工程路径**匹配实例（LASPCEM 没有进程间 IPC，只能这样定位）。
- 工程被 GUI 打开期间，`export_laspcem_snp` / `build_laspcem_report` 一律回 `PROJECT_IN_USE`；
  调本工具关掉后立刻可用，不必等进程自然退出。
- `closed=[]` 表示当前没有实例打开该工程，**不算失败**（`success=true`、`remaining=[]`）。

### `run_laspcem_solve`（内部实现，非 MCP 工具）

> 求解对外**只提供异步工具** `run_laspcem_solve_async`（见下一节）。本函数是内部实现：
> 异步队列、`export_laspcem_snp`、`build_laspcem_report` 都复用它，下列判据/返回字段同样适用于
> 异步任务完成后的 `solve_result`。
>
> **`exit_code` 不是成败判据**：`export_laspcem_snp` / `build_laspcem_report` 返回的
> `exit_code` 是 `laspcem.exe /session <脚本>` 这个进程的退出码 —— 脚本宿主在末尾执行
> `app.Exit()` 后**恒为 1**（实测 CPWG-3 成功导出 91 行数据、报告 `verified=true` 时也都是
> `exit_code=1`）。判断成败看 `success`、`verified`（报告是否真让工程被 SaveAs 改写）、
> `output_path` 是否存在与 `data_rows`；`exit_code` 只在 `success=false` 时作为辅助线索。

```python
from servers.laspcem.launcher import run_laspcem_solve

run_laspcem_solve(sem_path: str, setup_name: str = "", timeout_seconds: int = 7200, close_gui: bool = True) -> dict
```

对 .sem 工程跑 FEM 求解：非阻塞启动脚本（`app.Open` → `SetSelectionMode` → `cem.FEMAnalyze`），**轮询求解器进程**直到跑完，再按需关闭本次拉起的 GUI；结果自动写回 `.sem`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sem_path` | str | 是 | — | .sem 文件绝对路径 |
| `setup_name` | str | 否 | 自动探测 | setup 名；探测到 `Setup1(FEM)` 会剥成显示名 `Setup1` 再传给 API |
| `timeout_seconds` | int | 否 | 7200 | 最长等待秒数（小件约 100–220 s，大件往上加；**无代码上限** —— LASPCEM 的 TaskRunner 没设 `max_run_seconds`，不像 HFSS/CST 有 7200 硬上限） |
| `close_gui` | bool | 否 | True | 求解结束后是否关掉本次拉起的 GUI 实例 |

返回：`{"success": true, "solved": true, "saw_solver": true, "solver_seconds": 216.9, "solver_processes": [...], "sem_size_before": 260979, "sem_size_after": 6169622, "orphan_solvers": [], "script_log": "...", "message": "..."}`

失败码：`SETUP_NOT_FOUND`（显式传的 setup_name 在 .sem 里不存在，调用前就拦下）、`SOLVE_NOT_TRIGGERED`（脚本跑完但没出现求解器；`skipped_likely=true` 表示工程已有解、大概率被 LASPCEM 跳过）、`SOLVE_INCOMPLETE`（求解器跑完但 `Design_*.log` 里没有 `successfully finished`，可能不收敛/中途失败）、`SOLVE_NOT_SAVED`（求解跑完、补了 SaveAs 后顶层 .sem 仍无解记录）、`SOLVE_TIMEOUT`、`PROJECT_IN_USE`（该 .sem 已被别的实例打开）、`INVALID_PATH`。

证据字段：`saw_solver`、`solver_seconds`、`solver_processes`、`sem_size_before/after`、`solve_log`（最新 `Design_*.log` 路径）、`solve_log_finished`（True/False/null）、`persist`（补保存的结果，`null` 表示 GUI 自己写回了）、`stale_lock_removed`、`orphan_solvers`。

落盘细节：`cem.FEMAnalyze` 的结果有时只写进 `<工程名>/1_Result/` 的工作副本，顶层 `.sem` 仍是求解前状态。工具在 `is_solved` 仍为 false 时会自动补一次 `app.Open + app.SaveAs` 把结果合并回去（约 5 s），合并后仍无解才报 `SOLVE_NOT_SAVED`。

### 异步求解（大模型用这组）

LASPCEM 求解在 GUI 里跑，大模型可能几十分钟到几小时。同步的 `run_laspcem_solve` 会一直占住
MCP 请求，容易客户端超时；异步组把它丢进后台**单 worker 串行队列**（`servers/task_runner.py`），
立即返回 `task_id`。

```python
run_laspcem_solve_async(sem_path: str, setup_name: str = "", timeout_seconds: int = 7200,
                        stall_seconds: int = 900,
                        then_export: bool = False, then_report: bool = False,
                        export_path: str = "", report_name: str = "Sparameter_1",
                        port_count: int = 0) -> dict
# → {"success": true, "task_id": "a1b2c3d4e5f6", "status": "QUEUED",
#    "steps": ["solve", "export"], "hint": "poll", "sem_path": "..."}
# then_export / then_report：**由服务端串**——求解完成后自动接着导出 / 建报告，
# 调用方只提交一次、只查一个 task_id，不需要自己按顺序调工具。

laspcem_solve_query(task_id: str) -> dict
# 运行中 → {"status": "RUNNING", "completed": false, "phase": "solving"|"exporting"|"reporting",
#           "steps": [{"name": "solve", "status": "RUNNING"}, {"name": "export", "status": "PENDING"}],
#           "elapsed_seconds": 88.0, "adapt_iterations": 10,
#           "last_log_line": "@0.0-Iter6: Solving 1 out of 1 frequencies @"}
# 完成   → {"status": "SUCCEEDED", "completed": true, "phase": "done",
#           "solve_result": {...}, "export_result": {...}, "report_result": {...}}
# 失败   → error_code = 失败那一步的错误码（SOLVE_STALLED / EXPORT_NO_OUTPUT …），
#           已完成步骤的结果仍然带回（例如 solve_result 成功 + export_result 失败）

laspcem_task_cancel(task_id: str) -> dict
# 排队中 → 直接标记 CANCELLED；运行中 → 置取消信号（等待循环立刻收手、跳过补保存）
# + 终止该工程的 LASPCEM GUI 和它的 mpiexec/phofem/hydra_pmi_proxy
# → 之后 laspcem_solve_query 返回 error_code = "TASK_CANCELLED"

laspcem_task_list() -> dict
# {"count": n, "tasks": [{"task_id","status","operation","sem_path","steps",
#                          "created_at","started_at","finished_at","error","phase"}]}
```

要点：
- **单 worker 串行**：同时提交多个只会排队，不会起多个 LASPCEM 抢同一工程/CPU。
- 求解与导出都走同一条队列（串行），互不抢占；`laspcem_task_list` 的 `operation` 字段区分 `solve`/`export`。
- 参数校验（路径、`setup_name` 是否存在）在**提交时同步做**，`INVALID_PATH` / `SETUP_NOT_FOUND` 立即返回，不进队列、不占 worker。
- **取消不会引发额外动作**：取消信号让 `_wait_for_solve` 立刻返回，`run_laspcem_solve` 跳过补保存（否则会再起一个 GUI 去 SaveAs），并连带杀掉 `mpiexec / phofem / hydra_pmi_proxy`（Intel MPI 代理 cmdline 里没有工程路径，会一直占住结果目录句柄，删目录报 "Device or resource busy"）。实测取消 4.2 s、清理 1.0 s、进程残留为空、结果目录可删。
- 任务状态机 `QUEUED → RUNNING → SUCCEEDED/FAILED/CANCELLED`，完成后保留 6 小时（TTL）；**服务重启后任务表丢失**（返回 `TASK_NOT_FOUND`）。
- **卡死检测**：求解器还在，但「日志不增长 + `.sem` 不变 + 求解器 CPU 时间不涨」持续 `stall_seconds`（默认 900 s）⇒ 报 `SOLVE_STALLED` 并结束求解器。CPU 时间是关键判据 —— 插值扫频阶段不写日志但 CPU 在跑（实测 80–140 s 无日志增长）。
- **进度查询**：`laspcem_solve_query` 运行中回传 `elapsed_seconds` / `adapt_iterations`（`HOFEM_adapt_itr*.log` 计数）/ `last_log_line`（`Prog.log` 末行）。
- 任务进入终态后，取消信号/进程句柄会从内存表里回收（长跑服务不会只增不减）。
- ⚠️ `TaskRunner` 的 worker 线程是非 daemon：**服务重启/停止会被仍在跑的任务拖住**，重启前先 `laspcem_solve_cancel` 或等任务结束。
- 后台任务里跑的仍是同步 `run_laspcem_solve`，所以它自带的判据（`saw_solver` / `solve_log_finished` / 补保存 / 锁清理）全部照旧。

### `laspcem_cleanup_orphans`

服务重启/强杀后内存任务表已丢失（`laspcem_task_cancel` 拿不到 task_id），残留的 GUI / `mpiexec` /
`phofem` / `hydra_pmi_proxy` 会一直占 CPU 和结果目录句柄。本工具按**工程归属**定位并终止它们。

```python
laspcem_cleanup_orphans(sem_path: str = "", dry_run: bool = False,
                        include_unattributed: bool = False) -> dict
# → {"success": true, "dry_run": false,
#    "killed":  [{"pid": 31932, "name": "phofem.exe", "sem_path": "...", "reason": "terminated"}],
#    "skipped": [{"pid": 12852, "name": "laspcem.exe", "sem_path": "",
#                 "reason": "cmdline 无工程路径（可能 /session 脚本已删），保守跳过"}],
#    "active_sems": []}
```

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sem_path` | str | 否 | `""` | 只清这个工程的残留；留空 = 清所有「能判定归属、且不属于在跑任务」的进程 |
| `dry_run` | bool | 否 | False | True 只报告不动手（建议先跑一次看清） |
| `include_unattributed` | bool | 否 | False | True 时连**判不出归属**的 `laspcem.exe` 也清。默认 False —— 自己 `File→Open` 打开的窗口 cmdline 同样是纯 `laspcem.exe`，默认保守跳过 |

**归属怎么判**（决定谁进 `killed`）：
1. cmdline 能直接读到 `.sem`（GUI 带工程打开）⇒ 按工程匹配；
2. `/session <临时脚本>` 形态 ⇒ 读脚本内容反查 `.sem`；
3. `mpiexec` / `phofem` / `hydra_pmi_proxy` 的 cmdline **通常不含工程路径**（实测 `phofem Design ADAPTIVE-MESH`）
   ⇒ 无在跑任务时当孤儿清掉；有在跑任务时跳过并**记录**到 `skipped`（不静默）；
4. 判不出归属的 `laspcem.exe` ⇒ 默认只记 `skipped`，`include_unattributed=True` 才清。

`killed[].reason` 口径：`dry_run=true` 时一律是 `"dry_run"`（只是预览，什么都没杀）；
真清时才写 `"terminated"` 或 `"显式要求清理无归属实例（include_unattributed）"`。

**不会误杀活任务**：属于 `active_sems`（有未结束后台任务）的工程一律跳过，要停走 `laspcem_task_cancel`。

### `export_laspcem_snp`

> **只导已求解的工程**：没求解就立刻返回 `SOLVE_REQUIRED`（0.1 s，不起 GUI、不阻塞）。
> 要"先求解再导出"，用 `run_laspcem_solve_async(…, then_export=True)`，服务端会自动串起来。


```python
from servers.laspcem.launcher import export_laspcem_snp

export_laspcem_snp(sem_path: str, output_path: str = "", setup_name: str = "", solution_name: str = "",
                   sweeping_parameters: dict | None = None,
                   timeout_seconds: int = 600) -> dict
```

导出 S 参数为 Touchstone 文件。**没有解记录就先自动求解**（等求解器真跑完）再导出；已经有解就直接导出。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sem_path` | str | 是 | — | .sem 文件绝对路径 |
| `output_path` | str | 否 | 默认产物目录 | 输出路径；留空落到 `<result_dir>/export_laspcem_snp_<时间戳>.sNp`；`N` 按端口数落地（2 端口 → `.s2p`） |
| `setup_name` | str | 否 | 自动探测 | setup 名（显示名） |
| `solution_name` | str | 否 | `算法_<setup基名>` | 解名，如 `FEM_Setup1`（不是 `.sem` 里的内部 id `FEM_1`） |
| `sweeping_parameters` | dict | 否 | 自动抽变量 | `{变量: ['nominal', ...]}` |
| `timeout_seconds` | int | 否 | 600 | **导出阶段**最长等待秒数 |

返回：`{"success": true, "output_path": "...s2p", "requested_output_path": "...sNp", "exported": true, "setup_name": "Setup1(FEM)", "solution_name": "FEM_Setup1", "solved_first": bool, "elapsed_seconds": 5.1, "exit_code": 1, "script_log": "...", "message": "..."}`

> `exit_code` 是脚本宿主的进程返回码，`app.Exit()` 后**恒为 1**，不代表成败（见上文说明）。

要点与失败码：
- **产物按端口数命名**：请求 `x.sNp`，2 端口工程落地 `x.s2p`；`output_path` 给实际文件，`requested_output_path` 保留请求原样。
- **旧文件不算数**：导出前给已有同名产物拍指纹，只认被本次改写过的文件（否则错解名也会"秒成功"）。
- `SOLVE_REQUIRED`（工程未求解；返回体带 `suggested` = 建议的求解任务参数、`running_solve_task` = 该工程已在跑的任务 id）、`EXPORT_EMPTY`（产物只有表头、0 行数值）、`EXPORT_NO_OUTPUT`、`PROJECT_IN_USE`、`SETUP_NOT_FOUND`。
- **结果目录审计**：成功/失败返回体都带 `results_dir_before` / `results_dir_after`（`{exists, files, bytes}`）与 `results_dir_changed`，可据此判断“这次调用有没有动结果目录”。
- **结果失效快速失败**：`is_solved=True` 但结果目录为空时，等待窗口自动缩到 **30 s**（默认 600 s），返回 `EXPORT_NO_OUTPUT` 并指出"疑似结果已失效（GUI Message 面板实测 error 892，旧版报 167）"，实测 32 s 返回（原 92 s）。
- **结果目录（`<工程名>/1_Result`）不是硬门槛**：实测 BGA 工程把结果目录删掉后仍能导出（`.sem` 自带结果数据，打开工程时目录会被重建，4.2 s 出 91 行）。所以只在返回里记 `results_present_before`，真正导不出来由 `EXPORT_NO_OUTPUT` 兜底。
- 成功时带 `data_rows`（数出来的数据行数，本次实测 91 行）与 `sweeping_parameters_auto`；自动探测不到设计变量时返回 `warning`。

### `build_laspcem_report`

> 同导出：**只对已求解工程生效**，未求解立刻 `SOLVE_REQUIRED`；
> 要"先求解再建报告"，用 `run_laspcem_solve_async(…, then_report=True)`。


```python
from servers.laspcem.launcher import build_laspcem_report

build_laspcem_report(sem_path: str, setup_name: str = "", report_name: str = "Sparameter_1",
                     solution_name: str = "", timeout_seconds: int = 600,
                     port_count: int = 0) -> dict
```

给**已求解**的 .sem 工程建 S 参数网络报告（`report.CreateNetworkPlot` + `SaveAs`）；未求解立刻返回 `SOLVE_REQUIRED`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sem_path` | str | 是 | — | .sem 文件绝对路径 |
| `setup_name` | str | 否 | 自动探测 | setup 名（显示名） |
| `report_name` | str | 否 | Sparameter_1 | 报告名 |
| `solution_name` | str | 否 | `算法_<setup基名>` | 解名 |
| `timeout_seconds` | int | 否 | 600 | 建报告阶段最长等待秒数 |
| `port_count` | int | 否 | 0（自动） | 端口数 → 决定画哪些 S(i,j)；自动时看同目录 Touchstone 后缀，兜底 2 |

返回：`{"success": true, "report_name": "Sparameter_1", "verified": true, "elapsed_seconds": 3.1, "exit_code": 1, "script_log": "...", "message": "..."}`
`verified=true` 表示工程确实被 `SaveAs` 改写过（报告已落盘），不代表报告内容已校验。

> ⚠️ **`success` / `verified=true` 只说明「报告对象已写进工程」，不代表工程里有可用的仿真结果**。
> 2026-11-13 实测（Horn 副本，结果失效即 GUI 面板 error 892）：`build_laspcem_report` **3.4 s 成功**，
> 而同工程的 `export_laspcem_snp` **30.4 s 失败**（`EXPORT_NO_OUTPUT`）—— 建报告只写对象、不读结果，
> 所以两者结论相反是正常的。要判断结果是否可用，看 `export_laspcem_snp` 或 `results_dir`。

**`verified=false` 就是失败**（`exit_code` 恒为 1，不是判据）：脚本执行了但工程没被改写时返回
`REPORT_NOT_VERIFIED`（`success=false`、`retryable=true`，`details` 带 `verified` / `exit_code` /
`report_name` / `port_count` 与 `script_output`）。其余失败码：`REPORT_TIMEOUT`（等满 `timeout_seconds`
仍未见改写）、`SOLVE_REQUIRED`（工程未求解）、`PROJECT_IN_USE`、`SETUP_NOT_FOUND`。

### `import_aedt_as_sem`

```python
from servers.laspcem.launcher import import_aedt_as_sem

import_aedt_as_sem(aedt_path: str, output_dir: str = "", ansys_version: str = "2020.2",
                    with_geometry_parameters: bool = True, delete_operand_parts: bool = True,
                    delete_coordinate_systems: bool = False, background: bool = True,
                    compatible: bool = True, timeout_seconds: int = 900) -> dict
```

把 ANSYS HFSS `.aedt` 导入 LASPCEM 并生成 `.sem` 工程（等价 GUI 的 Import）。脚本照抄录制：`app.New(0)` → 关掉空的 `Untitled 0` → `aedt.Import({...})`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `aedt_path` | str | 是 | — | 源 `.aedt` 绝对路径 |
| `output_dir` | str | 否 | 与 aedt 同目录 | `.sem` 输出目录 |
| `ansys_version` | str | 否 | `2020.2` | 源文件 ANSYS 版本 |
| `with_geometry_parameters` | bool | 否 | True | 带上几何参数 |
| `delete_operand_parts` | bool | 否 | True | 删除布尔运算 operand 体 |
| `delete_coordinate_systems` | bool | 否 | False | 删除坐标系 |
| `background` | bool | 否 | True | 后台导入（异步） |
| `compatible` | bool | 否 | True | 兼容模式 |
| `timeout_seconds` | int | 否 | 900 | 等 `.sem` 出现的最长秒数 |

要点：
- `background=True` 时导入是**异步**的 —— 工具轮询 `output_dir` 里**新出现/被改写**的 `.sem`，拿到文件后关闭 GUI，不按输入文件名硬推（`CPWG-1.aedt` 实测产出 `CPWG-1-HFSSDesign1.sem`，名字来自 aedt 内的 design 名）。
- 返回里带 `is_solved` / `setup_name` / `setup_candidates`，新导入的工程应为 `is_solved=false`，正好可以接着调 `export_laspcem_snp`（会自动先求解）。
- **自动清理副产物**：导入会生成同名建模脚本 `<工程名>.py`（如 `TSV_SL-HFSSDesign1.py`），工具在拿到 `.sem` 后删除**本次生成**的那份（回传 `removed_scripts`）；用户原有的同名/其它 `.py` 不动。同时清掉残留锁 `~$<工程名>.lock`（`stale_lock_removed`）。
- 失败码：`IMPORT_NO_OUTPUT`（没产出 `.sem`）、`INVALID_PATH`、`IMPORT_FAILED`。

### `get_laspcem_setup`

> 除求解设置外，还回传**工程状态三态**与结果目录审计（只读）：
> `state` = `solved_ok`（解记录+结果都在，可直接导出）/ `results_missing`（有解记录但结果被删，
> 导出会失败并报 error 167）/ `unsolved`（没求解），并给出 `next_action` 与 `hint`。
> 实测 BGA 工程：`{"state": "results_missing", "results_present": false, "next_action": "run_laspcem_solve_async"}`。


```python
from servers.laspcem.launcher import get_laspcem_setup

get_laspcem_setup(sem_path: str) -> dict
```

读取 .sem 工程当前配置的求解设置参数（cem.EditSolution 那套键，约 64 个），直接从二进制文件提取，无需打开 LASPCEM。double 型字段（扫频 RangeStart/RangeEnd/RangeStep、Frequency）在 .sem 记录里只有引用序号、无数值，值为 null 并列入 `unresolved_double_keys`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `sem_path` | str | 是 | — | .sem 文件绝对路径 |

返回：`{"success": true, "sem_path": "...", "setup": {...}, "count": N, "resolved_count": M, "unresolved_double_keys": [...], "warning"?: "..."}`。

---

## 微波计算（7 个）

> 本地纯计算（不产生 task 信封，`details` 为 `null`，数据在顶层）。数值解算独立复核过（见 `审查报告.md` §十四）。

### `calculate_transline`

```python
from servers.microwave.tools import calculate_transline

calculate_transline(transline_type: str, er: float, freq: float, width=None, z0=None, h=None,
                    t: float = 0.035, gap=None, d_in=None, d_out=None, tand: float = 0.0) -> dict
```

计算四种传输线的**特性阻抗或线宽**（给 `z0` 求 `width`，或给 `width` 求 `z0`）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `transline_type` | str | 是 | — | `microstrip` / `stripline` / `gcpw`（接地共面波导）/ `coax` |
| `er` | float | 是 | — | 介质相对介电常数 |
| `freq` | float | 是 | — | 频率（GHz） |
| `width` / `z0` | float | 否 | None | 二选一：给 `z0` 求线宽，或给线宽求 `z0` |
| `h` | float | 否 | None | 介质厚度（mm）；coax 用 `d_in`/`d_out` |
| `t` | float | 否 | 0.035 | 铜厚（mm）= 35 µm |
| `gap` | float | 否 | None | gcpw 槽宽 |
| `d_in` / `d_out` | float | 否 | None | coax 内/外径 |
| `tand` | float | 否 | 0.0 | 介质损耗角正切 |

实测：4 种线型**往返自洽**（给 `z0` → 取 `width` → 回代 `z0'`，偏差 **0.0000%**；coax 手算校核 50.03 Ω）。⚠️ 参数只校验 `> 0`、**无物理下限**：`z0 = 1e-9` 也会 `success: true` 并回线宽 **9.08e10 mm**。

---

### `design_wilkinson`

```python
from servers.microwave.tools import design_wilkinson

design_wilkinson(h_mm: float, er: float, f0_ghz: float, n_sections: int,
                 z0: float = 50.0, fbw=None, ripple_db=None) -> dict
```

设计多级威尔金森功分器，输出各级微带线宽 / 线长与隔离电阻值（微带尺寸委托 `calculate_transline`，两者同源）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `h_mm` | float | 是 | — | 基板厚度（mm） |
| `er` | float | 是 | — | 基板介电常数 |
| `f0_ghz` | float | 是 | — | 中心频率（GHz） |
| `n_sections` | int | 是 | — | 级数（`n=1` 为经典 λ/4 单节） |
| `z0` | float | 否 | 50.0 | 系统阻抗（Ω） |
| `fbw` / `ripple_db` | float | 否 | None | 相对带宽 / 带内波纹；二者可互换推导 |

实测（独立节点法 S 参数解算复核）：臂阻抗 **Chebyshev 综合正确**（`n=2` 在 f0 恰好 −20.04 dB、`n=3` 在 f0 精确零点；`n=1` 回经典值 70.7 Ω / 100 Ω）。

返回键：`success / h_mm / er / f0_GHz / n_sections / z0 / fbw / ripple_db / warnings / lambda0_mm / arm_z_lines / sections[{section,z_arm,width_mm,length_mm,eeff}] / isolation_resistors`。

`ripple_db` 是**模型算出的带内最差回波损耗**（小反射近似；与精确等波纹级联链独立复核偏差 0.2~0.5 dB —— 窄带高回波损耗时偏乐观 ≤0.5 dB，钳位分支偏保守 ≈0.3 dB），**不是回显入参**。

⚠️ **参数自动钳位（2026-11-18 起显式告警，不再静默）**：两种钳位共用同一上限 `fbw=1.95`：

1. 请求优于该拓扑下限（偶模 2:1 变换器 DC 失配 ≈9.2 dB，等波纹解不存在）→ 带宽趋于无限，取上限并回写实际 RL；
2. 请求带宽超过 195% → 截断到 195% 并回写实际 RL。

`fbw` 与 `ripple_db` 同时给时**以 `fbw` 为准**，并在 `warnings` 里说明 `ripple_db` 被忽略。两种情况 `warnings` 都非空，agent 必须转告用户，不能只报 `success: true`。

⚠️ **一个已知缺陷（未修，详见 `审查报告.md` §十四）**：
**`MW-1` 隔离电阻方向错**：`n ≥ 2` 用启发式拟合，取值**朝输出端单调递减**（正确倾向应递增）。实测 `n=2` 带内最差隔离 **−13.30 dB**（数值最优 ≈ −20.97 dB）；`n=4/5/6/10` 一律同向递减 ⇒ 系统性偏差，n≥2 的隔离电阻值**不可直接采用**。

---

### `filter_order_lowpass`

```python
from servers.microwave.filter_order.tools import filter_order_lowpass

filter_order_lowpass(f_p_ghz: float, stopband_specs: list, rl_db: float = -20.0,
                     q_unloaded=None, max_notches: int = 4) -> dict
```

估计广义切比雪夫低通滤波器的**阶数与有限传输零点**（Cameron N+2 综合 + SLSQP 零点优化）。`stopband_specs` 每项 `[f, A]`（抑制点）或 `[f_lo, f_hi, A]`（抑制频段），频率 GHz、抑制 dB。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `f_p_ghz` | float | 是 | — | 通带截止频率（GHz） |
| `stopband_specs` | list | 是 | — | 阻带规范，每项 `[f, A]` 或 `[f_lo, f_hi, A]` |
| `rl_db` | float | 否 | -20.0 | 通带回波损耗（dB，负值） |
| `q_unloaded` | float | 否 | None | 谐振器无载 Q（None=无损） |
| `max_notches` | int | 否 | 4 | 最大有限传输零点对数 |

返回：`{"success": true, "N": 阶数, "notches": 零点对数, "notches_ghz": [...], "verify_rows": [...], "solutions": [...]}`。

实测锚点：`f_p_ghz=1`、`stopband_specs=[[2, 3, 40]]` → **N=5、1 对零点 @2GHz**。

---

### `filter_order_highpass`

```python
from servers.microwave.filter_order.tools import filter_order_highpass

filter_order_highpass(f_p_ghz: float, stopband_specs: list, rl_db: float = -20.0,
                      q_unloaded=None, max_notches: int = 4) -> dict
```

估计广义切比雪夫高通滤波器的阶数与有限传输零点。阻带位于 `f < f_p_ghz` 一侧，`stopband_specs` 各频率须 `< f_p_ghz`。参数与返回同 `filter_order_lowpass`。

实测锚点：`f_p_ghz=1`、`stopband_specs=[[0.3333, 0.5, 40]]` → **N=5、零点 @0.5GHz**（与低通 `[2, 3]` 原型互为镜像，零点互为倒数）。

---

### `filter_order_bandstop`

```python
from servers.microwave.filter_order.tools import filter_order_bandstop

filter_order_bandstop(f_s1_ghz: float, f_s2_ghz: float, stopband_specs: float | list,
                      passband_specs: list, rl_db: float = -20.0,
                      q_unloaded=None, max_notches: int = 4) -> dict
```

估计广义切比雪夫带阻滤波器的**原型阶数 N 与陷波零点对**（物理阶数 = 2N，倒数变换法复用低通核心）。`stopband_specs` 可为浮点（整个阻带抑制 dB）或列表 `[[f, A]]` / `[[f_lo, f_hi, A]]`；`passband_specs` 为 `[f_p1, A_p1, f_p2, A_p2]` 或 `[[f_p1, A_p1], [f_p2, A_p2]]`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `f_s1_ghz` / `f_s2_ghz` | float | 是 | — | 阻带下/上边（GHz），`f_s2 > f_s1` |
| `stopband_specs` | float \| list | 是 | — | 阻带抑制要求 |
| `passband_specs` | list | 是 | — | 两侧通带要求（`f_p1 < f_s1`、`f_p2 > f_s2`） |
| `rl_db` / `q_unloaded` / `max_notches` | — | 否 | — | 同低通 |

返回：`{"success": true, "N": 原型阶数, "order_bandstop": 2N, "notches": 零点对数, "notch_freq_pairs_ghz": [[下,上],...], "f0_ghz": 阻带中心, "fbw": 相对带宽, ...}`。

实测锚点：`f_s1=2.1, f_s2=2.2`、陷波≥20dB、两侧通带≤1dB、Q=500 → **N=3（物理阶数 6）、1 对陷波 @[2.100, 2.200]GHz**。

---

### `filter_order_bandpass`

```python
from servers.microwave.filter_order.tools import filter_order_bandpass

filter_order_bandpass(f_p1_ghz: float, f_p2_ghz: float, stopband_specs: list,
                      rl_db: float = -20.0, q_unloaded=None, max_zeros: int = 4) -> dict
```

估计广义切比雪夫带通滤波器的**阶数 N 与有限传输零点**（Cameron N+2 综合 + SLSQP 零点优化）。引入传输零点 k 可降低所需阶数，但受工程约束 `k ≤ N/2`；枚举在该约束被违反时停止，原因写入返回的 `stop_note`。`stopband_specs` 每项 `[f, A]`（抑制点）或 `[f_lo, f_hi, A]`（抑制频段），须落在通带两侧。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `f_p1_ghz` / `f_p2_ghz` | float | 是 | — | 通带下/上边（GHz），`f_p2 > f_p1` |
| `stopband_specs` | list | 是 | — | 阻带规范，每项 `[f, A]` 或 `[f_lo, f_hi, A]`（须在通带两侧） |
| `rl_db` | float | 否 | -20.0 | 通带回波损耗（dB，负值） |
| `q_unloaded` | float | 否 | None | 谐振器无载 Q（None=无损） |
| `max_zeros` | int | 否 | 4 | 最大有限传输零点数（非对数） |

返回：`{"success": true, "N": 阶数, "k": 零点数, "zeros_ghz": [...], "f0_ghz": 中心频率, "fbw": 相对带宽, "passband_ripple_db": 通带纹波, "N_lower_bound_no_zeros": 无零点阶数下界, "stop_note": 枚举停止原因, "solutions": [{k, N, zeros_ghz, min_margin_db, ...}]}`。

实测锚点：`f_p1=1.95, f_p2=2.05`、阻带 `[[1.85, 35], [2.20, 25]]` → **N=4、k=0**（k=1 时同样 N=4 但阻带裕量更大；k=2 违反 `k ≤ N/2` 停止）。

---

### `wilkinson_marchand_order`

```python
from servers.microwave.filter_order.tools import wilkinson_marchand_order

wilkinson_marchand_order(ratio: float, rl_db: float = 15.0) -> dict
```

估计 Wilkinson 功分器 / Marchand 巴伦的**节数**。切比雪夫变阻综合公式对 N≥2 有效；公式给出 N=1 时用单节三端口精确响应校验带边隔离度，达标则 N=1，否则升 N=2。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `ratio` | float | 是 | — | 频率比（高/低），>1 |
| `rl_db` | float | 否 | 15.0 | 带边隔离度指标（dB） |

返回：`{"success": true, "order": N}`。实测：频率比 6:1 → **3 节**。

---

## 射频链路计算（2 个）

纯本地计算：不走 gRPC(50055) / ADS / SimulationAgent(17866)，**EDI.exe 没起也能用**，返回里没有任务信封。
算法层是 `servers/c++demo/` 下 C++ 原版（`mixerspuranalyzer.cpp`、`rfdynamicrangecalculator.cpp`）的逐行 Python 移植，**语义以 C++ 为准**。

### `analyze_mixer_spur`

```python
from servers.rf_analysis.mixer_spur import analyze_mixer_spur

analyze_mixer_spur(mixers: list, if_candidates_mhz: list, max_order: int = 1,
                   direction: str = "receive", require_all_mixers: bool = True,
                   chart_if_candidates_mhz: list | None = None,
                   if_candidate_step_mhz: float = 1.0,
                   chart_candidate_step_mhz: float = 1.0,
                   maximum_detail_count: int = 12) -> dict
```

枚举 `m×源频 ± n×LO`（`m,n≥0`、`m+n≤max_order`、排除目标混频产物本身），判断杂散频率范围是否与目标输出频带（接收=IF、发射=RF）重叠。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `mixers` | list[dict] | 是 | — | 至少一个 `enabled`，字段见下表 |
| `if_candidates_mhz` | list[float] | 是 | — | 主 IF 候选点（MHz），**严格升序**且不含重复（乱序/重复直接 `INVALID_PARAMETERS`） |
| `max_order` | int | 否 | 1 | 最大阶次（`m+n≤max_order`） |
| `direction` | str | 否 | receive | `receive`（杂散源=RF、输出=IF）/ `transmit` |
| `require_all_mixers` | bool | 否 | true | 多 Mixer 判定：全部通过 / 任一通过 |
| `chart_if_candidates_mhz` | list[float] | 否 | null | 图表扫描点（同样严格升序） |
| `if_candidate_step_mhz` / `chart_candidate_step_mhz` | float | 否 | 1.0 | 合并连续通过点成范围用的步长 |
| `maximum_detail_count` | int | 否 | 12 | 保留明细的候选点数（`<0` 全留、`0` 不留） |

`mixers[]` 字段（缺省按默认值补全，补了哪些会写进返回的 `warnings`）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `name` | str | `""` | 标识，不参与计算 |
| `enabled` | bool | true | 必须是**真布尔**（`"false"` 字符串报错） |
| `relation` | str | 随 `direction`：receive=`low_side`、transmit=`sum` | 接收 `low_side/high_side`、发射 `sum/diff_lo/diff_if`；**显式传错才报错**（对齐 C++ GUI 切方向后的默认项） |
| `rf_center_mhz` | float | 1000.0 | 必须 **> 0** |
| `rf_bandwidth_mhz` / `if_bandwidth_mhz` | float | 0.0 / 1.0 | 允许 0（单频点） |
| `rf_power_dbm` / `lo_power_dbm` | float | −10 / 7 | 仅界面展示，**不参与**碰撞计算 |

返回：
```python
{
    "valid": True,                        # 只代表流程跑完，有没有通过点看下面那个列表
    "enabled_mixer_count": 1,
    "passed_if_candidates_mhz": [1000.0],
    "passed_if_ranges_mhz": [[1000.0, 1000.0]],
    "candidate_results": [                # 受 maximum_detail_count 限制
        {"if_center_mhz": 1000.0, "passed": True,
         "mixer_results": [{"mixer": {...}, "lo_center_mhz": 29000.0, "passed": True,
                            "error": "", "collisions": [{"m":0,"n":1,"sign":-1,
                                                         "min_mhz":500.0,"max_mhz":500.0}]}]},
    ],
    "chart_passed_if_candidates_mhz": [], "chart_passed_if_ranges_mhz": [],
    "detail_truncated": False,           # True = 明细被 maximum_detail_count 截断（另给一条 warning）
    "warnings": ["mixers[0] 未提供 relation，按 direction=receive 的缺省关系 low_side 计算"],
}
```

⚠️ 单 Mixer 的错误（负 LO、频率范围无效）落在该 Mixer 的 `error` 里，**不把 `valid` 拉成 false**。
⚠️ **明细截断会明确告知**：`candidate_results` 受 `maximum_detail_count` 限制，C++ 口径是 `<0`=全留、`0`=**按请求不保存（不算截断、不报警）**、`>0`=最多 N 条；
只有 `>0` 且没放全时返回 `detail_truncated: true` + 一条 warning。`passed_if_candidates_mhz` 永远完整，不会因截断而少点。
⚠️ **C++ 口径**：`absoluteRange` 把 `±X` 折成同一区间，所以 `m=0,n=1`（LO 自身泄漏）会出 `sign=±1` 两条 `min/max` 完全相同的明细——原版行为，**勿去重**（`tests/test_rf_tools.py::test_spur_lo_leakage_reported_for_both_signs_like_cpp` 锁着）。
⚠️ **两类诊断 warning（不改判定）**：① `require_all_mixers=false` 时，算不出来的 Mixer（负 LO / 频率范围无效）会点名——它按"不通过"计入"任一通过"，别误当该路无杂散；② 扫描步长小于候选点最小间隔时范围不会合并（容差 `step×1.01`），会按点各成一段。

实测锚点：RF 30 GHz / 带宽 200 MHz、`low_side`、order=3、IF 0.5~3 GHz → 6 点全通过（低边下 3 阶内只有 IF 谐波可能落带内，全通过是正常结果）；order=5、IF=15 GHz → 命中 `(0,1,±1)`、`(1,3,−1)`、`(2,3,−1)`。

---

### `calculate_rf_dynamic_range`

```python
from servers.rf_analysis.rf_dynamic_range import calculate_rf_dynamic_range

calculate_rf_dynamic_range(noise_figure_db: float, input_p1dbm: float, bandwidth: float,
                           bandwidth_unit: str = "mhz", required_snr_db: float = 0.0,
                           target_dynamic_range_db: float | None = None) -> dict
```

`噪声底 = −174 + 10·log10(BW_Hz) + NF`；`灵敏度 = 噪声底 + 所需 SNR`；`动态范围 = P1dB − 灵敏度`。给 `target_dynamic_range_db` 时顺带做达标评价。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `noise_figure_db` | float | 是 | — | 链路等效总 NF（dB，**≥0**） |
| `input_p1dbm` | float | 是 | — | 输入 1 dB 压缩点（dBm，可负） |
| `bandwidth` | float | 是 | — | 等效噪声带宽数值（**> 0**） |
| `bandwidth_unit` | str | 否 | mhz | `hz/khz/mhz/ghz`，大小写不敏感、两侧空格会被去掉；**省略或空串按 `mhz`** |
| `required_snr_db` | float | 否 | 0.0 | 最低所需信噪比（dB，可为负） |
| `target_dynamic_range_db` | float | 否 | null | 目标动态范围（dB） |

返回：`{"success": true, "noise_floor_dbm", "sensitivity_dbm", "dynamic_range_db"}`；给了 target 再加 `meets_target / margin_db / required_sensitivity_dbm`（`required_sensitivity = P1dB − target`，`meets_target = margin ≥ 0`）。

实测锚点：NF=2 dB、P1dB=−20 dBm、BW=100 MHz、SNR=10 dB、target=60 dB → **−92 / −82 / 62 dB，margin 2 dB，达标**。

---

### `run_laspcem_solve_async`

提交 LASPCEM 求解任务（异步，立即返回 task_id）

```python
run_laspcem_solve_async(sem_path: string, setup_name: string = "", timeout_seconds: integer = 7200, stall_seconds: integer = 900, then_export: boolean = false, then_report: boolean = false, export_path: string = "", report_name: string = "Sparameter_1", port_count: integer = 0) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sem_path` | string | 是 | `.sem` 工程绝对路径。 |
| `setup_name` | string | 否 | 求解 setup 名；留空自动探测。传错名字会**立刻**返回 SETUP_NOT_FOUND（不进队列）。 |
| `timeout_seconds` | integer | 否 | 求解超时（后台线程内生效）。 |
| `stall_seconds` | integer | 否 | 卡死判定（求解器还在但日志/工程/CPU 这么久没进展 ⇒ SOLVE_STALLED）；0 = 关闭。 |
| `then_export` | boolean | 否 | 求解完成后自动导出 sNp（服务端执行，结果在 `export_result`）。 |
| `then_report` | boolean | 否 | 求解完成后自动建报告（结果在 `report_result`）。 |
| `export_path` | string | 否 | 指定 sNp 输出路径；留空落到默认产物目录（`<result_dir>/export_laspcem_snp_<时间戳>.sNp`，实际后缀按端口数 .s2p/.s4p）。 |
| `report_name` | string | 否 | 报告名，默认 Sparameter_1。 |
| `port_count` | integer | 否 | 报告曲线端口数；0 = 自动（按 sNp 后缀推断，兜底 2）。 |

example: `{"sem_path": "<string>"}`

### `laspcem_solve_query`

查询 LASPCEM 求解任务（配合 run_laspcem_solve_async 的 task_id）。

```python
laspcem_solve_query(task_id: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | run_laspcem_solve_async 返回的 task_id。 |

example: `{"task_id": "<string>"}`

### `laspcem_task_list`

列出 LASPCEM 后台任务（含 operation=solve、phase、steps、状态与耗时）。

```python
laspcem_task_list() -> dict
```

### `laspcem_task_cancel`

取消 LASPCEM 求解任务（求解/导出/建报告 任一阶段都能取消）。

```python
laspcem_task_cancel(task_id: string) -> dict
```

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | run_laspcem_solve_async 返回的 task_id。 |

example: `{"task_id": "<string>"}`

## 图表（3 个）

### `list_result_curves`

```python
from servers.turbocharts.convert_raw import list_result_curves

list_result_curves(result_path: str) -> dict
```

调用引擎原生 `--curve-info` 列出 RAW 中可用的「依赖 → 曲线名 + 可用单位」。引擎是唯一事实源，不再手写解析 RAW 头部。

返回的 `curves` 结构为 `{依赖名: [{曲线名: [可用单位...]}, ...]}`，单位列表由引擎直接给出（每个曲线名映射到它可画的单位前缀）。画图时拼成 `<单位>_<曲线名>`（如 `db_s[1,1]`），单位从列表里选，依赖名原样传 `--dependcy`。

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `result_path` | str | 是 | RAW 文件路径 |

返回：
```python
{"success": True, "result_path": "C:/path/history/result.raw", "chart_type": "SP",
 "curves": {"freq": [{"S[1,1]": ["db", "dBm", "phase", "real", "imag", "mag", "VSWR", ...]}, ...]}}
```

`chart_type` 取自 RAW 首个 plot 的 `Plotname`（识别不出时为空串，可作 `turbocharts_convert(chart_type=...)` 的缺省值）。

### `unit_notes`：引擎声明 ≠ 实际绘图能力

返回里还带一个 `unit_notes` 数组，写明**实测校准**结论（`curves` 本身仍是引擎原样，不做过滤）：

| chart | 实测（2026-11-16） |
|---|---|
| `HB` | 引擎 `--curve-info` 会列出 `db/dBm/phase/real/imag/mag`，但绘图时**只有 `dBm_` 与 `real_` 出真图**；`db`/`phase`/`imag`/`mag` 以及 `VSWR`/`APS`/`MAS`/`MV`/`PSS` 全部被静默回退成 dBm 图（CSV 表头仍为 `dBm(x)`、数据与 dBm 逐行相同） |
| `SP` | `db`/`dBm`/`phase`/`real`/`imag`/`mag`/`VSWR` 各出不同图；`APS`/`MAS`/`MV`/`PSS` 需要 RAW 含对应数控器件数据，否则图能出但 CSV 只有表头 |

⚠️ 这是**引擎实现与它自己的 `--curve-info` / 说明文档不一致**（文档第 84 行声明「通用单位：db,dBm,phase,real,imag,mag」）。MCP 侧不改写引擎的答案，只把实测差异作为提示带上；**真正的判据是 `turbocharts_convert` 的产物表头自检**（见下文 `warnings`）。

引擎不可用 / 超时 / 输出非 JSON 时返回 `error_code: "CURVE_INFO_FAILED"`。

---

### `turbocharts_convert`

```python
from servers.turbocharts.convert_raw import turbocharts_convert

turbocharts_convert(
    raw_path: str,
    output_path: str = "",
    chart_type: str = "",
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
| `output_path` | str | 否 | 输出图片路径（PNG/JPG/BMP/SVG）；留空落到默认产物目录 `<result_dir>/turbocharts_convert_<时间戳>.png`，父目录自动创建 |
| `chart_type` | str | 否 | `"SP"` / `"HB"` / `"XDB"`；留空则调 list_result_curves 自动推断（引擎 --curve-info + Plotname），识别不出退 `"SP"`。**须与 RAW 的 Plotname 一致** |
| `csv_path` | str | 否 | 同步导出 CSV 路径（多曲线拆分时自动加曲线后缀） |
| `linename` | str | 否 | 曲线名 `<前缀>_<变量名>`，多条用 `&` 分隔 |
| `dependency` | str | 否 | 依赖轴，通常 `"freq"`（HB 下引擎忽略） |
| `ac_config` | str | 否 | 精度配置 |

曲线名 `linename` 格式与单位取自引擎自带说明（`servers/turbocharts/RAW 转图像工具使用说明.txt`）：`"_"` 前是单位，`"_"` 后是绘制线段名称。

| 单位 / 线段名（说明原文） | 示例 | 备注 |
|---|---|---|
| `db` | `DB_S[2,1]` / `db_s[1,1]` | 输出增益 `DB_S[2,1]`、反向增益 `DB_S[1,2]`（大小写不敏感） |
| `real`（实数） | `real_S[2,1]` | 线性幅度 |
| `phase` | `phase_S[2,1]` | 相位 |
| `vswr` | `VSWR_S[1,1]` | 驻波，原文注明只支持单条曲线 |
| `aps`（附加移向） | `APS_S[2,1]` | 数控衰减器附加相移 |
| `af`（幅度波动） | `AF_S[2,1]` | 数控器件幅度波动 |
| 数控移相器 | `MAS_S[2,1]` / `MV_S[2,1]` / `PSS_S[2,1]` | 衰减态 / 幅度波动 / 移相态 |
| 时延（线段名 `delays[2,1]`） | `real_delayS[2,1]` / `real_delays[1,1]` | 群时延 |
| `dbm`（原文未列，实测有效） | `dBm_S[2,1]` | 绝对功率，与 `db` 是两张不同的图 |
| `imag`（原文未列，实测有效） | `imag_S[2,1]` | 虚部 |

⚠️ 说明未覆盖、由实测补齐的规则：`chart_type=HB` 时引擎只认 `db` 类与 `real_`，`phase_`/`imag_`/`vswr_` 会被静默忽略并回退成 dBm 图；`"_"` 后的线段名必须与 RAW 的 Variables 段完全一致（大小写敏感）；多条曲线用 `&` 分隔（原文示例即 `&`）；逗号/分号/空格、裸变量名、拼错的线段名会被工具直接拦下（返回 `INVALID_LINENAME` / `CURVE_NOT_FOUND`），不再静默出空图。

CSV 行为：SP 多条曲线一次导出多列；**HB 多条曲线引擎只导出最后一条，工具自动按曲线拆分**；多条 VSWR 同样自动拆分。

返回：
```python
{
    "success": True,
    "return_code": 0,
    "img_generated": True,
    "csv_generated": True,
    "curves": ["dBm_Out1"],
    "curve_labels": {"C:/result/out.csv": ["freq", "dBm(Out1)"]},
    "warnings": ["CSV 已生成（1 个文件），请核对行数和列数与预期一致后再使用数据。"],
    "output_paths": {"img": "C:/result/gain.png", "csv": "C:/result/gain.csv"}
}
```

`success` 等于 `img_generated`（引擎 rc 恒为 0，曲线不存在/无数据也 rc=0），判成败看 `success` / `img_generated`，不要看 `return_code`。

---

### `compare_simulation_results`

```python
from servers.turbocharts.compare_results import compare_simulation_results

compare_simulation_results(
    result_paths: list[str],
    curve: str,
    output_path: str = "",
    chart_type: str = "",
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
| `output_path` | str | 否 | 输出图片路径；留空落到默认产物目录 `<result_dir>/compare_simulation_results_<时间戳>.png` |
| `chart_type` | str | 否 | `"SP"` / `"HB"` / `"XDB"`；留空从第一个 RAW 的 Plotname 自动识别，识别不出退 `"SP"` |
| `labels` | list | 否 | 每条曲线标签 |
| `dependency` | str | 否 | 依赖轴（默认 "freq"） |
| `csv_path` | str | 否 | 对比数据 CSV 输出路径 |
| `alignment` | str | 否 | 对齐方式："intersection" 或 "interpolation" |
| `reference_index` | int | 否 | interpolation 模式下的参考文件索引（默认 0） |

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

---

## 仿真器件管理（10 个）— 协议 v3

### `get_simulation_component_schema`

```python
from servers.eda.simulation_components import get_simulation_component_schema

get_simulation_component_schema(component_type: str, parameter_name: str = "") -> dict
```

查询仿真控件支持的参数名、值类型、单位、创建/更新权限和动态参数模式。返回 `schema_version`、`protocol_version`、`parameter_patterns`。创建或修改器件前优先调用。

### `update_components_from_schematic`

```python
from servers.eda.simulation_components import update_components_from_schematic

update_components_from_schematic(project_path: str, instance_name: str, parameters: dict, component_type: str = "", timeout_seconds: int = 120) -> dict
```

按实例名更新仿真器件参数。优先从已保存工程自动识别器件类型；实例未保存时可显式提供 `component_type`。显式类型与实际类型不一致会返回 `COMPONENT_TYPE_MISMATCH`。只更新传入的参数，其余保持原值。

### `delete_component_from_schematic`

```python
from servers.eda.simulation_components import delete_component_from_schematic

delete_component_from_schematic(project_path: str, instance_name: str, timeout_seconds: int = 120) -> dict
```

按实例名删除任意原理图器件及其连接线。MCP 只校验实例名非空，最终查找、删除和回滚由 EDI 服务完成。建议调用前先查询目标实例。

### `set_component_active_state`

```python
from servers.eda.simulation_components import set_component_active_state

set_component_active_state(project_path: str, instance_name: str, state: str, timeout_seconds: int = 120) -> dict
```

确定性设置器件状态为 NORMAL(激活状态)、DISABLED(禁用状态) 或 SHORTED(短路状态)。state 接受 NORMAL / DISABLED / SHORTED（大小写不敏感）。不是状态切换，重复调用具有幂等性。

### `replace_port_component`

```python
from servers.eda.simulation_components import replace_port_component

replace_port_component(project_path: str, target_instance_name: str, replacement_component_type: str, parameters: dict | None = None, timeout_seconds: int = 300) -> dict
```

替换端口器件类型（TermG ↔ P_nToneG）。如果有多个端口需要替换，应逐一替换，禁止一次同时替换多个端口。端口器件仅有 TermG 和 P_nToneG 两种类型。服务端保留位置、状态和外部连线。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | `.epp` 绝对路径 |
| `target_instance_name` | str | 是 | — | 要替换的端口实例名 |
| `replacement_component_type` | str | 是 | — | TermG / P_nToneG |
| `parameters` | dict | 否 | None | 可选参数字典 |
| `timeout_seconds` | int | 否 | 300 | 最长等待秒数 |

### `attach_out_component`

```python
from servers.eda.simulation_components import attach_out_component

attach_out_component(project_path: str, target_instance_name: str, pin_index: int | None = None, timeout_seconds: int = 120) -> dict
```

为目标器件引脚挂载 Out 器件并自动连线，这个器件的作用相当于 ADS 中的 Wire/Pin label。单引脚器件可省略 `pin_index`。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project_path` | str | 是 | — | .epp 绝对路径 |
| `target_instance_name` | str | 是 | — | 目标器件实例名 |
| `pin_index` | int | 否 | None | 0 开始的目标引脚编号 |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

---

### `load_schematic_from_file`

```python
from servers.eda.simulation_components import load_schematic_from_file

load_schematic_from_file(project_path: str, schematic_path: str, timeout_seconds: int = 300) -> dict
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

除了 Tool（启动时动态统计，当前 101 个），服务还注册了只读 Resource 和可复用 Prompt 工作流模板。

### Resources（7 个）

| URI | MIME | 说明 |
|---|---|---|
| `edi://service/overview` | `application/json` | 服务版本、协议版本、gRPC 目标、安全规则 |
| `edi://reference/simulation-components` | `application/json` | 仿真器件参数目录（与 `get_simulation_component_schema` 同源） |
| `edi://reference/operation-guide` | `text/markdown` | Markdown 操作规则：创建/删除/网表导入的安全约束 |
| `edi://service/status` | `application/json` | 实时运行时状态（gRPC 通道、队列占用） |
| `edi://reference/error-codes` | `text/markdown` | gRPC 状态码词典及建议动作 |
| `edi://projects` | `application/json` | 工作区工程目录清单（名称/路径/大小） |
| `edi://reference/turbocharts-guide` | `text/plain` | 引擎《RAW 转图像工具使用说明》原文 |

> Resource 是只读上下文，由客户端主动拉取。标准 MCP 客户端可通过 `resources/list` 和 `resources/read` 访问。

### Prompts（8 个）

| Prompt | 参数 | 用途 |
|---|---|---|
| `inspect_edi_project` | `project_path`, `detail_level` | 只读检查工程（概览→变量→器件→仿真配置） |
| `run_and_review_simulation` | `project_path`, `execution_mode`, `analyze_log` | 统一异步仿真流程 + 日志分析 |
| `configure_simulation_component` | `project_path`, `action`, `component_type`, `instance_name`, `requirements` | Schema→参数→确认→创建/更新 |
| `create_simulation_report` | `project_path`, `output_path`, `overwrite` | 查询工程 → 生成曲线 → 渲染 PDF/DOCX |
| `troubleshoot_edi_error` | `status`, `error_code` | 按状态码查错误词典、检查服务状态、给排查建议 |
| `analyze_signal_chain` | `project_path`, `start` | 追踪信号链路并逐级说明 |
| `assess_anti_burnout` | `project_path` | 抗烧毁评估并按功率裕量排序 |
| `select_component` | `sub_type_id`, `requirement` | 从公共/个人模型库选型（含替换闭环） |

> Prompt 是用户主动选择的工作流模板。标准 MCP 客户端可通过 `prompts/list` 和 `prompts/get` 访问。

> 注意：内置 EDI Chat (`/ui`) 当前主要消费 Tool，不自动拉取 Resources 或 Prompts。能否在客户端中显示取决于具体 MCP 客户端的实现。

---

## 文档（2 个）

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

### `fetch_artifact`

```python
from servers.multimodal_vision import fetch_artifact

fetch_artifact(file_path: str, ttl_seconds: int = 600) -> dict
```

把本机（引擎机）上的任意产物文件（.snp/.raw/图/报告/二进制）注册成可下载 URL。与 open_document 不同：不校验扩展名，远程场景下主机用返回的 url 直接下载，sha256 供下载后校验字节一致。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `file_path` | str | 是 | — | 产物文件绝对路径（任意扩展名） |
| `ttl_seconds` | int | 否 | 600 | 链接有效秒数（钳制到 30-3600） |

返回：`{"success": True, "url": "http://...", "file_name": "...", "size_bytes": N, "sha256": "...", "expires_in": 600}`

---

## 独立仿真工具（4 个）

> 直接调用 EDI 安装目录固定子目录下的四个 exe（`settings.simulation_tool_dir`，空 = 从
> exe/源码目录往上找 EDI 根再拼 `simulation_tool`，找不到回退 `C:\Program Files (x86)\EDI`），
> **不走 gRPC/HTTP**。四个 exe 的 stdout
> 都是单行 UTF-8 JSON、退出码 0/1，判成败直接看退出码 + JSON 的 success/errors（drawtool 是 status/error_code）。

### `export_simulation_report`

```python
from servers.simulation_tool import export_simulation_report

export_simulation_report(report: dict, output_dir: str, schematic: str = "", timeout_seconds: int = 300) -> dict
```

用 `Report_Exporter.exe` 把报告 JSON 渲染成 `document.pdf` / `document.docx` / `report.json`
（独立 exe 版：不依赖渲染服务，也不需要 EDI）。`report` **透传 exe 原生 JSON**，校验交给 exe
（errors 原样带回 code/stage/message）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `report` | dict | 是 | — | 报告数据对象，非空 dict。常用字段见下 |
| `output_dir` | str | 否 | 默认产物目录 | 输出目录；留空落到 `<result_dir>/export_simulation_report_<时间戳>/`（自动创建）；三个目标文件任一已存在时 exe 拒绝 |
| `schematic` | str | 否 | "" | 可选原理图图片路径，覆盖 report 的 schematic_path |
| `timeout_seconds` | int | 否 | 300 | 最长等待秒数 |

`report` 常用字段：`model_name`（封面型号）/ `description` / `conclusion` /
`components`（`[{type,model,manufacturer,specs}]`）/
`links`（`[{name, electrical_parameters:[{indicator,minimum,typical,maximum,unit,requirement,result}]}]`）/
`charts`（`[{link,indicator,image_path}]`）/ `schematic_path`。

返回：
```python
{"success": True, "report_dir": "...", "pdf_path": ".../document.pdf",
 "docx_path": ".../document.docx", "report_data_path": ".../report.json",
 "chart_count": 0, "errors": [], "artifacts": [...], "links": {"pdf": {...}, "docx": {...}}}
```

`links[].electrical_parameters` 结构与 `run_tr_simulation` 的 `results` 一致，可无缝衔接。

### `run_tr_simulation`

```python
from servers.simulation_tool import run_tr_simulation

run_tr_simulation(epp_path: str, links: list, indicators: str = "工作频率,输出增益,噪声系数",
                  threads: int = 5, edi_path: str = "", ads_path: str = "",
                  timeout_seconds: int = 1800) -> dict
```

跑 TR 链路仿真并提取各链路电参数（前置：EDI 已启动、ADS 许可可用；结果可直接交给 `export_simulation_report` 出报告）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `epp_path` | str | 是 | — | .epp 工程绝对路径（同目录需有 schematics/main/schematic.ep） |
| `links` | list | 是 | — | 链路数组（至少一条），每项 `{name, min_freq, max_freq, input_port, output_port, ...}`；跨字段约束 `min_freq<max_freq`、`input_port≠output_port`、`phase_step/atten_step>0` 由 MCP 层前置校验 |
| `indicators` | str | 否 | 工作频率,输出增益,噪声系数 | 逗号分隔指标 |
| `threads` | int | 否 | 5 | 指标并发线程数（正整数） |
| `edi_path` | str | 否 | "" | EDI 安装目录，默认往上找 EDI 根 |
| `ads_path` | str | 否 | "" | ADS 安装目录，为空时按 `EDI_ADS_PATH` 环境变量 → EDI `config/config.ini` 的 `[ads] hpeesof_dir` → 自动探测 `C:/Program Files/Keysight/ADS*`（多版本取最高）取值 |
| `timeout_seconds` | int | 否 | 1800 | 最长等待秒数（多链路 × 多指标跑 ADS 可能超过 10 分钟；上限 3600） |

返回：
```python
{"success": True, "simulation_path": "...", "results": [...], "errors": [],
 "artifacts": [...]}
```

`results` 每项 `{link, electrical_parameters:[{indicator,minimum,typical,maximum,unit}]}`。
**部分指标失败时 `success` 仍为 True 但 `errors` 非空**，必须检查。

---

### `run_mixer_simulation`

```python
from servers.simulation_tool import run_mixer_simulation

run_mixer_simulation(project: dict, out_dir: str, workers: int = 10,
                     turbocharts_exe: str = "", timeout_seconds: int = 1800) -> dict
```

执行混频链路仿真（上下变频/多通道/多级寻路，15 个指标）。前置：EDI 已启动（gRPC 127.0.0.1:50055）、网表 netlist.log 与器件库 component.json 在 epp 同目录。**新版不生成 PDF/DOCX 报告**，直接读返回的 indicators / artifacts。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project` | dict | 是 | — | 参数表 `{name, epp, links}`；links[i] = {switch_bits?, defaults, indicators} |
| `out_dir` | str | 否 | 默认产物目录 | 结果目录；留空落到 `<result_dir>/run_mixer_simulation_<时间戳>/`（response.json / indicator_results / runs / figs 落这里） |
| `workers` | int | 否 | 10 | 并发进程数；EDI 不稳定时降 5 |
| `turbocharts_exe` | str | 否 | "" | turbocharts 路径，默认用 MCP 自动探测（往上找 EDI 根） |
| `timeout_seconds` | int | 否 | 1800 | 最长等待秒数（上限 3600） |

`project.links[i].defaults` = `{in_port, out_port, input_freq_ghz, input_power_dbm, lo:[{freq_ghz, power_dbm, output_bw_ghz?}]}`；`indicators` 是 15 个指标之一带各自字段（详见 `servers/simulation_tool/Mixer_Simulator使用说明.md`）。

返回：`{"success": True, "out_dir": "...", "status": "ok", "condition_count": N, "indicators": [...], "failures": [...], "artifacts": [...]}`。

---

### `draw_chart`

```python
from servers.simulation_tool import draw_chart

draw_chart(series: list, image_path: str, chart_title: str = "", x_axis_title: str = "",
           y_axis_title: str = "", marks: list = None, file_stem: str = "chart",
           image_format: str = "png", show_window: bool = False, timeout_seconds: int = 120) -> dict
```

把一组或多组 XY 数据绘制为曲线图并保存为 PNG/JPG（固定 1600×1000）。输入透传 `drawtool.exe` 的 JSON 配置（写临时配置文件 → 位置参数传给 exe → 解析 stdout）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `series` | list | 是 | — | 数据组数组，每项 `{name, points: [[x,y],...]}`；name 缺失/空则不显示图例，已提供的 name 必须唯一 |
| `image_path` | str | 否 | 默认产物目录 | 输出目录绝对路径；留空落到 `<result_dir>/draw_chart_<时间戳>/`（不存在时自动创建） |
| `chart_title` / `x_axis_title` / `y_axis_title` | str | 否 | "" | 标题（空串=不显示） |
| `marks` | list | 否 | None | 标记数组，每项 `{series, point: [x,y], label}` |
| `file_stem` | str | 否 | "chart" | 输出文件名（不含扩展名） |
| `image_format` | str | 否 | png | png/jpg/jpeg |
| `show_window` | bool | 否 | False | 是否弹窗显示（自动化保持 False） |
| `timeout_seconds` | int | 否 | 120 | 最长等待秒数 |

返回：`{"success": True, "image_path": "...", "message": "...", "artifacts": [...], "links": {"image": {...}}}`。

⚠️ `drawtool.exe` 带管理员权限清单，启动会请求 UAC（服务以管理员运行时不会弹，否则会卡在 UAC）。

---

### `find_netlist_paths`

```python
from servers.simulation_tool import find_netlist_paths

find_netlist_paths(netlist_path: str, ep_path: str, pairs: list, max_paths: int = 10000,
                   max_states: int = 1000000, timeout: int = 30, encoding: str = "auto",
                   output_path: str = "") -> dict
```

寻路：给定网表 + 原理图 + 有向端口对，枚举每个端口对的全部信号路径，并给出每条路径上每个开关的目标 Bits（`required_bits`）。纯本地计算，不需要 EDI 运行 / ADS / gRPC，输入只读。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `netlist_path` | str | 是 | — | 网表文件路径（netlist.log） |
| `ep_path` | str | 是 | — | 对应的 schematic.ep 路径 |
| `pairs` | list | 是 | — | 有向端口对 `[{input, output}]`，正整数且 input≠output |
| `max_paths` | int | 否 | 10000 | 每端口对最多路径数，0 不限 |
| `max_states` | int | 否 | 1000000 | 每端口对搜索边数预算，0 不限 |
| `timeout` | int | 否 | 30 | 每端口对搜索时间预算（秒），0 不限 |
| `encoding` | str | 否 | auto | 源文件编码（auto=先 UTF-8/BOM 再 GB18030） |
| `output_path` | str | 否 | — | 结果 JSON 落盘路径；**留空则写默认产物目录**（`MCP_RESULT_DIR`，未配时 `%TEMP%/mcp_result`）下的 `find_netlist_paths_<时间戳>.json`，父目录自动创建 |

返回：
```python
{"success": True, "message": "寻路完成：2 个端口对，共 2 条路径",
 "output_path": "C:/Users/<user>/AppData/Local/Temp/mcp_result/find_netlist_paths_20261117_152222_420.json",
 "summary": {"ports": [1, 2],
             "pairs": [{"input_port": 1, "output_port": 2, "status": "ok", "complete": True, "path_count": 1}],
             "total_paths": 2},
 "result": {...exe 完整 JSON（netlist_path/ep_path/ports/results/errors）...}}
```

`success` 只看退出码 0（搜索完整且无状态错误）；某端口对 `incomplete`（complete=false）或某路径 `configuration_error` 时，需看 `summary` 各对状态与 `result.errors`。每条路径含 `switch_settings`（每个开关的 `required_bits` 目标状态）。

> **限制：拓扑 ≠ 物理。** 这是拓扑寻路，不是完整射频物理模型：放大器只允许 index 0→1；开关、功分器、电桥只允许公共端 ↔ 某一支路；**其余多端口器件（含混频器）按双向直通处理，不区分 RF/LO/IF，不判断频率转换、隔离度、增益、频段或路径是否独占**。所以「某条路径存在」不等于该路径在物理上可用。`switch_settings` 只含 EP `genl_type=27` 的开关；`Bits` 存在不代表器件是开关 —— 移相器/衰减器的 Bits 不由寻路决定，保持原值。另：`ep_path` 必须与该网表同工程（实测用错工程返回 `METADATA_MISSING`，不会静默给错路径）；`--timeout` 不含文件解析与 JSON 序列化耗时。器件规则全表见仓库 `servers/simulation_tool/Netlist_Pathfinder使用说明.md`。

错误码：`INVALID_PARAMETERS`（pairs/预算不合法、`--encoding` 无效、`output_path` 传了目录）、`FILE_NOT_FOUND`（网表或 EP 不存在）、`PATHFINDER_FAILED`（exe 未输出合法 JSON）、`TOOL_TIMEOUT`（子进程超时，retryable）、`EXE_NOT_FOUND`（安装目录缺 `Netlist_Pathfinder.exe`）、`EXE_NEEDS_ELEVATION`（WinError 740，服务需提权启动）、`WRITE_FAILED`（结果 JSON 写盘失败，响应里带 `output_path`）。

**落盘**：每次调用都会把 exe 的完整 JSON 写一份到 `output_path`（默认产物目录），`success=false` 也写 —— rc=1 时 `results` 里常带部分路径与逐层 `errors`，排障要看原文。返回里的 `result` 与文件内容完全一致。

---

### `describe_tool`

```python
from servers.describe_tool import describe_tool

describe_tool(name: str = "") -> dict
```

查看某个工具要哪些参数、怎么填、最小可运行示例 —— 参数拿不准时先调它，别猜也别读源码。`name` 留空返回全部工具名 + 一句话说明；工具不存在返回 `TOOL_NOT_FOUND` + 相似名建议。

返回：`{"success": True, "name": ..., "summary": 首行, "doc": 完整 docstring, "parameters": [...], "example": {...}, "example_source": "handwritten|skeleton", "notes": [...]}`。

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
    "hint": "ok",
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

> `hint` 是机器可读动作指令，agent 优先读它而非从 `success`/`task_success`/`outcome_known` 做布尔推断。EDA gRPC 家族取值：`ok` / `task_failed` / `outcome_unknown` / `service_unavailable` / `busy` / `rejected` / `protocol_error` / `too_large`；通用错误响应 `retry_safe` / `do_not_retry`；异步提交 `poll`。

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
