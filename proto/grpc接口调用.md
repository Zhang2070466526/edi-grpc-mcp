# ExternalCall gRPC 接口调用说明

本文说明外部程序如何调用 EDA-PMDS 的 `ExternalCall` gRPC 服务。协议使用统一的 `payload_json` 字符串传递业务参数，任务提交和任务结果获取是异步的。

## 1. 服务接口

```proto
service ExternalCall {
  rpc PerformAction(Request) returns (Response) {}
  rpc FetchEvent(FetchEventRequest) returns (stream Event) {}
}
```

- `PerformAction`：提交一个外部调用任务。
- `FetchEvent`：按 `client_uuid` 拉取任务状态和最终结果事件。

`PerformAction` 返回成功只表示服务端已受理任务，不表示任务执行完成。最终结果需要通过 `FetchEvent` 获取。

## 2. 通用请求结构

```proto
message Request {
  string client_uuid = 1;
  string task_id = 2;
  EventType type = 3;
  string payload_json = 4;
}
```

通用字段：

- `client_uuid`：客户端唯一标识，不能为空。提交任务和拉取事件时必须保持一致。
- `task_id`：任务唯一标识，不能为空。建议每次请求使用新的 UUID。
- `type`：任务类型。
- `payload_json`：JSON 字符串，按任务类型填写不同字段。

## 3. 任务类型

```proto
enum EventType {
  EVENT_TYPE_UNSPECIFIED = 0;
  OPEN_PROJECT = 1;
  SIMULATE_PROJECT = 2;
  VIEW_PROJECT_NETLIST = 3;
  LOG_EVENT = 4;
  ERROR_EVENT = 5;
  MODEL_REPLACE = 6;
  CAPTURE_SCHEMATIC = 7;
  CLOSE_PROJECT = 8;
  CALL_SIMULATION_CONTROLLER = 9;
  SIMULATE_NETLIST = 10;
  CREATE_SIMULATION_COMPONENT = 11;
  DELETE_SIMULATION_COMPONENT = 12;
  GENERATE_SCHEMATIC_FROM_NETLIST = 13;
  SET_COMPONENT_ACTIVE_STATE = 14;
  UPDATE_SIMULATION_COMPONENT = 15;
  REPLACE_PORT_COMPONENT = 16;
  ATTACH_OUT_COMPONENT = 17;
  LIST_SCHEMATIC_COMPONENTS = 18;
  GET_SCHEMATIC_COMPONENT_INFO = 19;
  LOAD_SCHEMATIC_FROM_FILE = 20;
  GET_COMPONENTS_STATIC_PARAMS = 21;
  SIMULATE_ANTI_BURNOUT = 22;
  CREATE_PROJECT = 23;
}
```

任务类型：

- `OPEN_PROJECT`：打开工程。
- `SIMULATE_PROJECT`：执行工程仿真。
- `VIEW_PROJECT_NETLIST`：查看工程网表。
- `MODEL_REPLACE`：按 CSV 执行模型替换。
- `CAPTURE_SCHEMATIC`：截图保存原理图图片。
- `CLOSE_PROJECT`：关闭工程，可选择是否保存工程。
- `CALL_SIMULATION_CONTROLLER`：直接调用 ADS 仿真器。
- `SIMULATE_NETLIST`：仿真指定的 `netlist.log`，返回 RAW 结果和仿真器输出日志。
- `CREATE_SIMULATION_COMPONENT`：按 `component_type` 通过器件工厂新增器件，使用工厂默认参数。
- `DELETE_SIMULATION_COMPONENT`：按器件实例名删除原理图上的通用器件。
- `GENERATE_SCHEMATIC_FROM_NETLIST`：将指定网表导入工程的唯一原理图。
- `SET_COMPONENT_ACTIVE_STATE`：按器件实例名确定性设置正常、禁用或短路状态。
- `UPDATE_SIMULATION_COMPONENT`：按器件实例名更新任意普通器件的已有参数；指定器件类型额外支持动态参数或变量新增。
- `REPLACE_PORT_COMPONENT`：将原理图上的 `TermG` 与 `P_nToneG` 相互替换，并保留原有外部引脚连线。
- `ATTACH_OUT_COMPONENT`：为指定器件的目标引脚新增并连接一个 `Out` 器件。
- `LIST_SCHEMATIC_COMPONENTS`：查询指定工程唯一原理图上的全部器件信息。
- `GET_SCHEMATIC_COMPONENT_INFO`：根据器件实例名查询指定器件的完整信息。
- `LOAD_SCHEMATIC_FROM_FILE`：使用指定的 `schematic.ep` 整体替换工程唯一原理图。
- `GET_COMPONENTS_STATIC_PARAMS`：转发器件固有参数查询，返回重量、尺寸、封装、所属厂商和成本等信息。
- `SIMULATE_ANTI_BURNOUT`：对工程原理图中具备抗烧毁数据的器件执行输入功率仿真和抗烧毁风险评估。
- `CREATE_PROJECT`：按指定名称、作者和父目录创建新工程，不显示创建向导，也不自动打开工程。

## 4. payload_json 示例

### OPEN_PROJECT

```json
{
  "project_path": "C:/path/to/project.epp"
}
```

### SIMULATE_PROJECT

```json
{
  "project_path": "C:/path/to/project.epp",
  "log_source": "logger_id"
}
```

`log_source` 用于标识调用方日志来源。

### SIMULATE_NETLIST

```json
{
  "netlist_path": "C:/path/to/netlist.log"
}
```

`netlist_path` 必须指向一个已经存在的文件。服务端会将网表复制到独立任务目录后再执行仿真，不会修改传入的原始网表。

### VIEW_PROJECT_NETLIST

```json
{
  "project_path": "C:/path/to/project.epp"
}
```

### MODEL_REPLACE

```json
{
  "project_path": "C:/path/to/project.epp",
  "csv_path": "C:/path/to/replace.csv"
}
```

模型替换成功后，服务端先发送最终结果事件，再关闭当前工程且不保存旧内存中的原理图，随后自动重新打开同一个 `.epp`，使文件中的替换结果加载到原理图界面。替换失败时不会关闭或重新打开工程。

### CAPTURE_SCHEMATIC

```json
{
  "project_path": "C:/path/to/project.epp",
  "img_path": "C:/path/to/schematic.png"
}
```

### CLOSE_PROJECT

```json
{
  "project_path": "C:/path/to/project.epp",
  "need_save": false
}
```

`need_save` 表示关闭工程前是否保存。

### CALL_SIMULATION_CONTROLLER

```json
{
  "netlist_path": "C:/path/to/netlist.log",
  "ads_path": "C:/Keysight/ADS"
}
```



### CREATE_SIMULATION_COMPONENT

```json
{
  "project_path": "C:/path/to/project.epp",
  "component_type": "SParameter"
}
```

- `component_type` 为器件工厂注册的类型名，区分大小写，不能为空。
- 该任务只负责创建器件，不接收 `parameters`；器件使用工厂提供的全部默认参数。
- 参数更新或动态参数新增统一使用 `UPDATE_SIMULATION_COMPONENT`。
- 每次调用都创建新器件，同类型器件可以存在多个。服务端根据工厂默认器件名自动分配未占用的实例名，并在最终事件的 `instance_name` 中返回。
- 工厂无法创建指定类型时，最终事件返回失败原因，原理图不会新增器件。

#### CREATE_SIMULATION_COMPONENT 支持的器件

该接口没有单独维护器件白名单，`component_type` 直接交给原理图 `SI_Factory` 创建。因此，当前版本支持下列已注册类型（名称区分大小写）：

```text
R, L, C, Ground, Pin,
ITRISEMI_ISV, ITRISEMI_OSV1, ITRISEMI_LNA, ITRISEMI_CPW,
XDB, ITRISEMI_Process_Include,
Match1, Match2, Match3, Match4, Match5, Match6, Match7, Match8, Match9,
PublicFET, ModelCorrection, Tran, VtPWL, LimiterSML, TimeDelay, Equalizer,
V_nTone, Out, TermG, CST, Ansys, Ice, P_nTone, SNP,
SParameter,
LPF_Butterworth, BPF_Butterworth, HPF_Butterworth, BSF_Butterworth,
LPF_Chebyshev, BPF_Chebyshev, HPF_Chebyshev, BSF_Chebyshev,
Amplifier2, PwrSplit2, PwrSplit3, TLIN, CouplerSingle, CouplerDual,
HarmonicBalance, MixerWithLO, Mixer2, LSSP, Var, Attenuator,
PhaseShifterSML, AntLoad, SPDTStatic, Optim, Goal,
PA_MS_OSV, V_Source, Term, Hybrid90, Hybrid180, Sweep,
Ckt_MGA_72543_LNA, EBOND, FreqMult, EBOND_ShapeJedec, VIA, VIA2,
InsertText, EnvTempVar, Misc_VTW_slot_30X60, TML_mlin, Misc_PAD,
Cap_M1AB, P_nToneG, SL_RDL1_C, ML_RDL1_C, SL_RDL1, ML_RDL1,
BGA_up, BGA_nup, TSV1_BGA_up, TSV1_BGA_nup, TSV1_D30,
TSV_pad, TSV_SL, CPW_SL, CPW_pad, CPW_TSV, CPWG_RDL1,
CPWG_RDL1_C, I1T_Device1, I1T_Device2, PROCESS_INCLUDE, TLIND,
Balun1, Balun2, Balun3Port, DAttenuator, MLoad, Circulator,
AC, BudNF, BudNFdeg, V_1Tone, Options, MeasEqn, Mixer
```

其中当前接口重点覆盖、并在 `UPDATE_SIMULATION_COMPONENT` 中具有明确特殊更新规则的仿真器件为：`SParameter`、`HarmonicBalance`、`XDB`、`Sweep(对应网表中的ParamSweep)`、`Var`、`P_nTone`、`P_nToneG`。

> 注册列表来自当前程序版本，工厂注册项发生变化时，以实际程序及 `CREATE_SIMULATION_COMPONENT` 的最终事件为准。

### UPDATE_SIMULATION_COMPONENT

```json
{
  "project_path": "C:/path/to/project.epp",
  "instance_name": "SP2",
  "parameters": {
    "Stop": {
      "value": "20",
      "unit": "GHz"
    },
    "Pts": {
      "value": "201"
    }
  }
}
```

- `instance_name` 为原理图上的器件实例名，精确匹配。
- 支持更新原理图上任意普通器件；不属于下述特殊类型的器件只能更新其 `paramsInfo_` 中已经存在的参数，不能新增参数。
- `parameters` 必须为非空对象；只更新请求中提供的参数，其他参数保持原值。
- `HarmonicBalance` 和 `XDB` 支持根据 `Freq[1]`、`Order[1]` 模板新增更大下标的 `Freq[x]`、`Order[x]` 参数；频率与阶数必须同下标成对存在，且下标从 1 开始连续。
- `P_nToneG` 和 `P_nTone` 支持根据 `Freq[1]`、`P[1]` 模板独立新增 `Freq[x]` 或 `P[x]`；两类参数不要求成对或连续。
- 更新 `SweepVar` 时，原理图中必须存在 `Var` 器件，且至少一个 `Var` 器件包含与其 `value` 完全相同的变量名。
- 更新 `SimInstanceName[1]`～`SimInstanceName[6]` 时，其 `value` 必须与原理图中已有器件实例名完全相同，且目标器件类型只能为 `HarmonicBalance` 或 `XDB`。
- 任一参数或引用校验失败时，不修改器件；保存失败时恢复修改前参数。
- 更新 `Var` 时，`parameters` 的每个一级 key 表示变量名：已有同名变量时更新，不存在时新增。
- `Var` 变量必须提供 `value`，可选提供 `min`、`max`、`status`、`tunable`，不支持 `unit`。
- 新增变量未提供可选字段时，默认 `Min=""`、`Max=""`、`Status="Disable"`、`Tunable="false"`。
- `status` 只接受 `min/max`、`+/- Delta %`、`Disable`；`tunable` 只接受布尔值或字符串 `"true"`、`"false"`。

#### UPDATE_SIMULATION_COMPONENT 支持的器件及参数名

该接口可以更新原理图上除 `InsertText` 外的任意已有器件，但参数名区分大小写，且禁止修改 `BasicParameters`：

- 普通器件：只能更新该器件当前 `paramsInfo_` 中已经存在的一级参数名，不能新增参数。
- 特殊器件：除更新已有参数外，还可按下表规则新增动态参数或变量。
- 调用前可使用 `GET_SCHEMATIC_COMPONENT_INFO` 查询单个器件，或使用 `LIST_SCHEMATIC_COMPONENTS` 查询全部器件；返回的 `parameters` 一级 key（排除 `BasicParameters`）就是该器件当前可更新的已有参数名。

| 器件类型 | 默认可更新参数名 | 允许新增的参数名 |
| --- | --- | --- |
| `TermG` | `Z0`、`Num`、`Noise` | 不支持新增参数 |
| `Out` | `ItemName`（器件实例名） | 不支持新增参数；仅允许通过该字段重命名 `Out` |
| `SParameter` | `Start`、`Stop`、`Step`、`Pts`、`NoiseInputPort`、`NoiseOutputPort`、`BandwidthForNoise`、`CalcNoise`、`CalcS`、`CalcGroupDelay`、`EnforcePassivity`、`GroupDelayAperture`、`FreqConversion`、`FreqConversionPort` | 不支持新增参数 |
| `HarmonicBalance` | `Freq[1]`、`Order[1]` | `Freq[x]`、`Order[x]`，`x` 为从 `1` 开始的正整数；同下标必须成对存在，并且下标连续 |
| `XDB` | `Freq[1]`、`Order[1]`、`GC_XdB`、`GC_InputPort`、`GC_OutputPort`、`GC_InputFreq`、`GC_OutputFreq`、`GC_InputPowerTol`、`GC_OutputPowerTol`、`GC_MaxInputPower`、`StatusLevel` | `Freq[x]`、`Order[x]`，规则与 `HarmonicBalance` 相同 |
| `Sweep` | `SweepVar`、`SimInstanceName[1]`～`SimInstanceName[6]`、`Start`、`Stop`、`Step`、`Pts` | 不支持新增参数；`SweepVar` 和 `SimInstanceName[x]` 需通过引用校验 |
| `Var` | 没有固定变量名；每个非 `BasicParameters` 的一级 key 都是一个变量名 | 任意非空变量名；已有同名变量时更新，否则新增 |
| `P_nTone` | `Z0`、`P[1]`、`Freq[1]`、`Num`、`Noise`、`Temp` | `P[x]`、`Freq[x]`，`x` 为从 `1` 开始的正整数；两类参数可独立新增且不要求连续 |
| `P_nToneG` | `Z0`、`P[1]`、`Freq[1]`、`Num`、`Noise`、`Temp` | `P[x]`、`Freq[x]`，规则与 `P_nTone` 相同 |

普通参数的请求字段为：

```json
"参数名": {
  "value": "参数值",
  "unit": "可选单位"
}
```

- `value` 必填，支持字符串、数字或布尔值。
- `unit` 可选；只有器件当前参数定义了 `Unit` 且请求单位存在于该列表中时才能传入。
- `Order[x]` 不支持 `unit`。
- `Out` 是唯一允许通过本接口修改 `ItemName` 的器件类型，请求格式为 `"ItemName":{"value":"OutResult"}`。新名称必须以字母或下划线开头，只能包含字母、数字和下划线，且不能与原理图上的其他器件实例名重复；不支持 `unit`。重命名成功后，后续接口应使用新名称定位该器件。
- `TermG.Z0` 和 `R.R` 可用单位为 `mOhm`、`Ohm`、`kOhm`、`MOhm`、`GOhm`、`TOhm`；`TermG.Num` 和 `TermG.Noise` 不支持单位。
- `L.L` 可用单位为 `fH`、`pH`、`nH`、`uH`、`mH`、`H`。
- `C.C` 可用单位为 `fF`、`pF`、`nF`、`uF`、`mF`、`F`。
- `SParameter` 的 `Start`、`Stop`、`Step`、`BandwidthForNoise`，`HarmonicBalance`/`XDB` 的 `Freq[x]`，以及 `XDB` 的 `GC_InputFreq`、`GC_OutputFreq` 等带单位参数，具体可用单位应以查询接口返回的 `Unit` 为准。
- `Var` 不使用上述普通参数结构，其变量字段仍为 `value`、可选 `min`、`max`、`status`、`tunable`。

### DELETE_SIMULATION_COMPONENT

```json
{
  "project_path": "C:/path/to/project.epp",
  "instance_name": "R1"
}
```

该任务按 `instance_name` 精确删除任意普通器件，并按原理图正常删除流程处理器件连接线。保存失败时撤销删除。

### SET_COMPONENT_ACTIVE_STATE

```json
{
  "project_path": "C:/path/to/project.epp",
  "instance_name": "R1",
  "state": "SHORTED"
}
```

`state` 只接受以下大写值：

- `NORMAL`：正常。
- `DISABLED`：禁用。
- `SHORTED`：短路。

该任务直接设置目标状态，不执行状态切换；保存失败时恢复原状态。

### ATTACH_OUT_COMPONENT

```json
{
  "project_path": "C:/path/to/project.epp",
  "target_instance_name": "U1",
  "pin_index": 0
}
```

- `target_instance_name` 为需要挂载 `Out` 的目标器件实例名，按名称精确匹配。
- `pin_index` 为可选的非负整数，使用从 `0` 开始的内部引脚编号。传入时按该编号检索目标引脚，器件不存在此引脚时失败。
- 未传 `pin_index` 时沿用单引脚规则：目标器件必须恰好只有一个引脚，否则任务失败并提示调用方通过 `pin_index` 确认添加至哪个端口。
- 服务端创建使用默认参数的 `Out` 器件，实例名按 `Out1`、`Out2`……自动分配未占用名称。
- 服务端根据“器件位置指向目标引脚”的方向判断引脚朝向，优先在该方向距离目标引脚 100 个原理图坐标单位处放置 `Out`，并同步旋转 `Out`，使其引脚朝向目标器件。
- 每个候选位置会将 `Out` 图元包围框向外扩展 10 个坐标单位，再与原理图已有器件的包围框检测重叠。首选方向冲突时，按顺时针顺序尝试其余三个方向；四个方向都冲突时任务失败，不创建器件。
- 目标引脚已有网络时，新增网线加入原网段；没有网络时创建新网段。
- 器件与网线在同一个撤销命令组内创建；连接或保存失败时整体回滚。

### LIST_SCHEMATIC_COMPONENTS

```json
{
  "project_path": "C:/path/to/project.epp"
}
```

- 服务端复用已打开的工程；工程未打开时自动打开，并读取工程唯一原理图。
- 返回原理图上的真实器件，不包含 `InsertText` 文字图元。
- 每个器件返回 `instance_name`、`component_type`、`general_type`、`sub_type`、`active_state`、`state` 和完整的 `parameters`。
- `active_state` 的数值 `0`、`1`、`2` 分别对应 `state` 的 `NORMAL`、`DISABLED`、`SHORTED`；其他数值对应 `UNKNOWN`。
- `parameters` 保持器件内部 `paramsInfo_` 的完整分组结构，不裁剪字段。

### GET_SCHEMATIC_COMPONENT_INFO

```json
{
  "project_path": "C:/path/to/project.epp",
  "instance_name": "R1"
}
```

- `instance_name` 按原理图器件实例名精确匹配。
- 返回的 `component` 对象与 `LIST_SCHEMATIC_COMPONENTS` 的数组元素结构完全一致。
- 指定器件不存在或名称对应 `InsertText` 文字图元时，最终事件返回失败及具体原因。

### LOAD_SCHEMATIC_FROM_FILE

```json
{
  "project_path": "C:/path/to/project.epp",
  "schematic_path": "C:/path/to/schematic.ep"
}
```

- `schematic_path` 必须指向已存在的 `.ep` 文件，文件内容必须是完整的 `edi-schematic` S-Expression 原理图数据。
- 服务端复用已打开工程；工程未打开时自动打开，并操作工程唯一原理图。
- 该任务调用 `Schematic::loadFromFile()`，会整体替换当前原理图，不执行追加或合并。
- 成功后立即保存目标工程，并返回加载后的器件数量和网段数量。
- 加载、解析或保存失败时恢复调用前的原理图状态。
- 该任务不复制模板目录中的 `model_data`、`symbol`、`match` 等依赖文件，也不触发模型库扫描；自定义器件必须已存在于当前工作区模型库，否则会沿用 `loadFromFile()` 的现有行为跳过无法创建的器件。

### GET_COMPONENTS_STATIC_PARAMS

批量查询：

```json
{
  "original_uuids": [
    "d0004c57-db4b-4587-b3c5-e82a07258cc7"
  ]
}
```

单条查询：

```json
{
  "original_uuid": "d0004c57-db4b-4587-b3c5-e82a07258cc7"
}
```

- `original_uuids` 与 `original_uuid` 必须且只能提供一个。
- `original_uuids` 必须是至少包含一个非空字符串的数组；`original_uuid` 必须是非空字符串。
- 该任务不需要 `project_path`，也不要求打开工程。
- gRPC 服务将请求体原样转发到 `POST /api/v1/components/static-params/`。
- 最终事件的 `payload_json` 是上游接口完整响应信封，保留 `code`、`message`、`data`，不增加或删除业务字段。
- `data` 与请求 UUID 顺序一一对应，未命中的 UUID 保留为 `null`。

### SIMULATE_ANTI_BURNOUT

```json
{
  "project_path": "C:/path/to/project.epp"
}
```

- `project_path` 必须指向已存在的 `.epp` 工程文件；工程已经打开时复用现有窗口，未打开时静默打开。
- gRPC 调用只执行计算并返回结果，不主动显示或激活仿真结果窗口。
- 服务端先查询器件的 `max_input_power`，最终 `results` 只包含 `isAntiBurnout == true` 的器件；不具备抗烧毁数据的器件不会参与评估，也不会出现在结果中。
- 找不到有效 `P_nToneG`/`P_nTone` 激励源或激励源 `Freq[1]` 无效的器件不插入功率探针、不参与仿真，但仍返回该器件信息，并在 `result` 中写入具体原因。
- 部分器件评估成功、部分器件失败时，最终事件仍为成功；每个器件的成功结论或失败原因统一放在自身的 `result` 字段中。
- 工程打开、工程保存、抗烧毁参数查询、网表生成或仿真进程等整体流程失败时，最终事件为失败，具体原因位于事件 `message`，`payload_json` 为 `{}`。

### CREATE_PROJECT

```json
{
  "name": "demo_project",
  "author": "JGL",
  "path": "D:/projects"
}
```

- `name` 必填，必须是合法的 Windows 文件名，且不能超过 255 个字符。
- `name` 不允许首尾空白、尾部英文句点、控制字符以及 `< > : " / \\ | ? *`。
- `name` 不允许使用 `CON`、`PRN`、`AUX`、`NUL`、`COM1`～`COM9`、`LPT1`～`LPT9` 等 Windows 保留名称，带扩展名形式同样禁止。
- `author` 可选，为空或未提供时使用 `bm`。
- `path` 可选，表示工程父目录，不是 `.epp` 文件路径；为空或未提供时使用当前工作空间的 `projects` 目录。
- 工程文件路径为 `<path>/<name>/<name>.epp`。目标工程目录或 `.epp` 文件已经存在时创建失败，不覆盖已有内容。
- 服务端直接调用 `NewProjectWizard::createProject(name, errorMsg, author, path)`，不显示向导，也不自动打开新工程。

### REPLACE_PORT_COMPONENT

```json
{
  "project_path": "C:/path/to/project.epp",
  "target_instance_name": "TermG1",
  "replacement_component_type": "P_nToneG",
  "parameters": {
    "Z0": {
      "value": "50",
      "unit": "Ohm"
    },
    "Num": {
      "value": "1"
    },
    "Freq[1]": {
      "value": "1",
      "unit": "GHz"
    },
    "P[1]": {
      "value": "polar(dbmtow(0),0)"
    },
    "Freq[2]": {
      "value": "2",
      "unit": "GHz"
    },
    "P[2]": {
      "value": "polar(dbmtow(-10),0)"
    }
  }
}
```

- `target_instance_name` 为原理图上待替换的器件实例名，目标类型必须是 `TermG` 或 `P_nToneG`。
- `replacement_component_type` 只接受 `TermG` 或 `P_nToneG`，并且必须与原器件类型不同。
- `parameters` 必须提供 JSON 对象，可以为空对象；未被客户端替换的参数使用新器件默认值。
- 创建 `TermG` 时，仅更新默认参数表中已经存在的参数；不存在的参数直接忽略。
- 创建 `P_nToneG` 时，普通参数遵循相同规则；`Freq[x]` 和 `P[x]` 即使不在默认参数表中，也会分别复制 `Freq[1]`、`P[1]` 的参数结构后新增，其中 `x` 为数字。
- 参数单位存在时必须属于该参数支持的单位列表；`P[x]` 不支持 `unit`。
- 新器件继承旧器件的位置、旋转、翻转和活动状态。旧器件外部引脚上的连线会重新连接到新器件外部引脚，同时保留线宽和手工布线路径。
- 服务端自动生成未占用的新实例名，并通过最终事件的 `new_instance_name` 返回。

### GENERATE_SCHEMATIC_FROM_NETLIST

```json
{
  "project_path": "C:/path/to/project.epp",
  "netlist_path": "C:/path/to/netlist.log",
  "clear_before_import": false
}
```

- `netlist_path` 必须是已存在的文件。
- `clear_before_import` 为可选布尔值，默认值为 `false`；为 `true` 时导入前清空 `main` 原理图，为 `false` 时追加。
- 服务端固定向工程中的 `main` 原理图导入器件和连接链路，成功后自动保存工程。最终事件返回 `schematic_path`、`symbols_added`、`nets_added`、`lines_added` 和 `net_points_added`。

上述工程类任务均使用工程中的唯一原理图。工程已打开时复用现有工程窗口，否则先打开工程再执行操作。
## 5. PerformAction 返回值

```proto
message Response {
  int32 code = 1;
  string message = 2;
  string client_uuid = 3;
  string task_id = 4;
  EventType event_type = 5;
}
```

- `code == 0`：任务已受理。
- `code != 0`：任务未受理，错误原因见 `message`。
- `client_uuid`：请求中的客户端标识。
- `task_id`：请求中的任务 ID。
- `event_type`：请求任务类型。

注意：`Response` 不是最终业务结果。业务是否完成、成功或失败，要看后续 `FetchEvent` 返回的事件。

## 6. FetchEvent

请求：

```proto
message FetchEventRequest {
  string client_uuid = 1;
}
```

返回事件：

```proto
message Event {
  string client_uuid = 1;
  string task_id = 2;
  EventType event_type = 3;
  ResultStatus status = 4;
  string message = 5;
  string payload_json = 6;
}
```

状态枚举：

```proto
enum ResultStatus {
  RESULT_STATUS_UNKNOWN = 0;
  RESULT_STATUS_RUNNING = 1;
  RESULT_STATUS_SUCCESS = 2;
  RESULT_STATUS_FAILED = 3;
}
```

常见事件顺序：

1. `PerformAction` 返回 `code == 0`。
2. `FetchEvent` 收到 `RESULT_STATUS_RUNNING`。
3. `FetchEvent` 收到 `RESULT_STATUS_SUCCESS` 或 `RESULT_STATUS_FAILED`。

## 7. 事件 payload_json

事件中的 `payload_json` 也是 JSON 字符串。字段如下：

- `OPEN_PROJECT`：`project_path`
- `SIMULATE_PROJECT`：`project_path`、`result_path`、`ads_output`
- `VIEW_PROJECT_NETLIST`：`project_path`、`netlist_path`
- `MODEL_REPLACE`：`project_path`
- `CAPTURE_SCHEMATIC`：`project_path`、`img_path`
- `CLOSE_PROJECT`：`project_path`、`need_save`
- `LOG_EVENT`：`level`、`source`、`text`
- `CALL_SIMULATION_CONTROLLER`：`netlist_path`、`ads_path`
- `SIMULATE_NETLIST`：`netlist_path`、`result_path`、`ads_output`
- `CREATE_SIMULATION_COMPONENT`：`project_path`、`component_type`、`action`、`instance_name`
- `UPDATE_SIMULATION_COMPONENT`：`project_path`、`instance_name`
- `DELETE_SIMULATION_COMPONENT`：`project_path`、`instance_name`
- `SET_COMPONENT_ACTIVE_STATE`：`project_path`、`instance_name`、`state`
- `REPLACE_PORT_COMPONENT`：`project_path`、`target_instance_name`、`old_component_type`、`new_component_type`、`new_instance_name`
- `ATTACH_OUT_COMPONENT`：`project_path`、`target_instance_name`、可选的 `pin_index`、`out_instance_name`
- `LIST_SCHEMATIC_COMPONENTS`：`project_path`、`component_count`、`components`
- `GET_SCHEMATIC_COMPONENT_INFO`：`project_path`、`instance_name`；成功时额外包含 `component`
- `LOAD_SCHEMATIC_FROM_FILE`：`project_path`、`schematic_path`、`component_count`、`net_segment_count`
- `GET_COMPONENTS_STATIC_PARAMS`：上游接口原始响应对象，包含 `code`、`message`、`data`
- `SIMULATE_ANTI_BURNOUT`：成功时仅包含 `results`；失败时为空对象 `{}`
- `CREATE_PROJECT`：成功时包含 `project_path`；失败时为空对象 `{}`
- `GENERATE_SCHEMATIC_FROM_NETLIST`：`project_path`、`netlist_path`、`schematic_path`、`clear_before_import`、`symbols_added`、`nets_added`、`lines_added`、`net_points_added`

## 8. SIMULATE_NETLIST 完整调用示例

建议先调用 `FetchEvent` 建立流式订阅，再调用 `PerformAction` 提交任务。两次调用的 `client_uuid` 必须一致。

### 8.1 PerformAction 请求

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-netlist-001",
  "type": "SIMULATE_NETLIST",
  "payload_json": "{\"netlist_path\":\"C:/test/netlist.log\"}"
}
```

如果 Postman 未识别最新枚举，可以重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `10`。

`PerformAction` 受理成功时返回：

```json
{
  "code": 0,
  "message": "task accepted",
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-netlist-001",
  "event_type": "SIMULATE_NETLIST"
}
```

### 8.2 FetchEvent 运行事件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-netlist-001",
  "event_type": "SIMULATE_NETLIST",
  "status": "RESULT_STATUS_RUNNING",
  "message": "task accepted",
  "payload_json": "{\"netlist_path\":\"C:/test/netlist.log\"}"
}
```

### 8.3 FetchEvent 成功事件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-netlist-001",
  "event_type": "SIMULATE_NETLIST",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "netlist simulation finished",
  "payload_json": "{\"netlist_path\":\"C:/test/netlist.log\",\"result_path\":\"C:/test/history/result.raw\",\"ads_output\":\"ADS simulator output...\"}"
}
```

`payload_json` 解析后的业务对象为：

```json
{
  "netlist_path": "C:/test/netlist.log",
  "result_path": "C:/test/history/result.raw",
  "ads_output": "ADS simulator output..."
}
```

字段说明：

- `netlist_path`：客户端传入的原始网表绝对路径。
- `result_path`：复制完成后的 RAW 文件绝对路径，固定放在原网表同级的 `history/result.raw`。
- `ads_output`：本次 ADS 仿真进程的标准输出和标准错误输出。日志只在最终成功或失败事件中统一返回。

### 8.4 服务端文件处理流程

假设程序目录为 `C:/Program Files/EDI`，请求中的 `task_id` 为 `postman-netlist-001`：

1. 创建临时目录 `C:/Program Files/EDI/simulation/postman-netlist-001/`。
2. 将传入的网表复制为该目录下的 `netlist.log`。
3. 在临时目录执行 ADS 仿真并生成 `result.raw`。
4. 将结果复制到原网表目录的 `history/result.raw`，存在旧文件时覆盖。
5. 将复制后的 `history/result.raw` 绝对路径写入最终事件的 `result_path`。
6. 删除临时目录 `simulation/postman-netlist-001/`。

如果仿真、RAW 复制或临时目录清理失败，最终状态为 `RESULT_STATUS_FAILED`。失败事件仍包含已经收集到的 `ads_output`；未成功归档 RAW 时，`result_path` 为空字符串。

## 9. 仿真器件管理完整调用示例

建议先调用 `FetchEvent` 建立流式订阅，再调用 `PerformAction`。以下请求使用相同的 `client_uuid`。

### 9.1 使用默认参数新增器件

以下请求新增一个使用工厂默认参数的 `SParameter` 器件。创建其他器件时只需替换 `component_type`。

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-create-sp-001",
  "type": "CREATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"component_type\":\"SParameter\"}"
}
```

创建 `Sweep` 的请求示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-create-sweep-001",
  "type": "CREATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"component_type\":\"Sweep\"}"
}
```

创建请求不能包含 `parameters`。如需设置参数，应在创建成功并取得 `instance_name` 后调用 `UPDATE_SIMULATION_COMPONENT`。

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `11`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-create-sp-001",
  "event_type": "CREATE_SIMULATION_COMPONENT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "simulation component created",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"component_type\":\"SParameter\",\"action\":\"created\",\"instance_name\":\"SP1\"}"
}
```

`action` 固定为 `created`；实际分配名称见 `instance_name`。

### 9.2 删除指定名称的器件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-delete-sp-001",
  "type": "DELETE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"SP1\"}"
}
```

如果 Postman 未识别最新枚举，可以将 `type` 临时填为数值 `12`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-delete-sp-001",
  "event_type": "DELETE_SIMULATION_COMPONENT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "component deleted",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"SP1\"}"
}
```

该任务不限于三种仿真器件，可以按名称删除普通原理图器件及其连接线。

### 9.3 按器件名更新参数

更新 SParameter 的部分参数：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-sp-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"SP2\",\"parameters\":{\"Stop\":{\"value\":\"20\",\"unit\":\"GHz\"},\"Pts\":{\"value\":\"201\"}}}"
}
```

其他普通器件同样可以更新已有参数。例如更新电阻 `R1` 的阻值：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-r-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"R1\",\"parameters\":{\"R\":{\"value\":\"75\",\"unit\":\"Ohm\"}}}"
}
```

对于此类普通器件，如果请求的参数名不在器件现有 `paramsInfo_` 中，任务失败并返回 `unsupported parameter`，已有参数不会被修改。

更新 Sweep 的变量与仿真实例引用。以下示例要求原理图中已有包含变量 `Vbias` 的 `Var` 器件，以及实例名为 `HB1` 的 `HarmonicBalance` 器件：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-sweep-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"Sweep1\",\"parameters\":{\"SweepVar\":{\"value\":\"Vbias\"},\"SimInstanceName[1]\":{\"value\":\"HB1\"},\"Start\":{\"value\":\"1\"},\"Stop\":{\"value\":\"10\"},\"Step\":{\"value\":\"1\"},\"Pts\":{\"value\":\"10\"}}}"
}
```

#### HarmonicBalance/XDB 新增频率

`HarmonicBalanceSetting` 和 `XDBSetting` 在界面中将每一行频率保存为同下标的一对参数：

- `Freq[x]`：内部包含 `Value`、`Unit`、`CurrentUnit`、`DefaultUnit`，接口使用 `value` 和可选的 `unit` 设置。
- `Order[x]`：内部包含 `Value`，接口使用 `value` 设置，不接受 `unit`。
- 新增频率时，`Freq[x]` 和 `Order[x]` 必须成对提供；`x` 必须紧接现有最大下标，不能跳号。也可以在一次请求中连续新增多组。
- 网表按 `Freq[1]` 开始连续读取，先输出全部 `Freq[x]`，再输出全部 `Order[x]`。例如 `Freq[2]` 最终写为 `Freq[2]=2.4 GHz`。

以下示例为 `HB1` 新增第二组和第三组频率；XDB 使用相同结构，只需将 `instance_name` 改为对应的 XDB 实例名：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-hb-freq-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"HB1\",\"parameters\":{\"Freq[2]\":{\"value\":\"2.4\",\"unit\":\"GHz\"},\"Order[2]\":{\"value\":\"5\"},\"Freq[3]\":{\"value\":\"4.8\",\"unit\":\"GHz\"},\"Order[3]\":{\"value\":\"3\"}}}"
}
```

#### P_nToneG/P_nTone 新增 Freq[x] 或 P[x]

该行为与 `EditInstanceParameter` 的 Add 操作保持一致：

- 新增 `Freq[x]` 时复制 `Freq[1]` 的内部结构，包含 `Value`、`Unit`、`CurrentUnit`、`DefaultUnit`、`Addable`、`Cutable`；新项的 `Cutable` 固定为 `true`。
- 新增 `P[x]` 时复制 `P[1]` 的内部结构，包含 `Value`、`Addable`、`Cutable`；新项的 `Cutable` 固定为 `true`。
- `Freq[x]` 和 `P[x]` 可单独新增，不要求同下标成对，也允许下标不连续。
- `Freq[x]` 接收 `value` 和可选的 `unit`；`P[x]` 只接收 `value`，不支持 `unit`。
- 网表生成时遍历全部参数，分别直接输出为 `Freq[x]=值 单位` 和 `P[x]=值`。

以下示例同时新增 `Freq[2]` 和 `P[3]`；如果只需新增其中一种，删除另一项即可：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-pntoneg-tone-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"PORT1\",\"parameters\":{\"Freq[2]\":{\"value\":\"2.4\",\"unit\":\"GHz\"},\"P[3]\":{\"value\":\"polar(dbmtow(-10),45)\"}}}"
}
```

`instance_name` 可以是 `P_nToneG` 或 `P_nTone` 器件的实际实例名。

`SimInstanceName[x]` 只支持 Sweep 默认参数表中的 `x=1..6`；目标名称精确匹配，目标类型只能为 `HarmonicBalance` 或 `XDB`。

#### Var 器件变量更新与新增

`Var` 器件的变量名直接存放在 `paramsInfo_` 的一级 key 中。接口字段与内部字段对应关系如下：

| 请求字段 | `paramsInfo_` 字段 | 是否必填 | 说明 |
|---|---|---|---|
| `value` | `Initial` | 是 | 原理图页面显示和仿真读取的变量初始值 |
| `min` | `Min` | 否 | 变量最小值 |
| `max` | `Max` | 否 | 变量最大值 |
| `status` | `Status` | 否 | 只接受 `min/max`、`+/- Delta %`、`Disable` |
| `tunable` | `Tunable` | 否 | 只接受布尔值或字符串 `"true"`、`"false"` |

下面的请求针对实例名为 `Var1` 的器件：如果 `Vbias` 已存在则更新它；如果 `InFreq` 不存在则新增它。

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-var-001",
  "type": "UPDATE_SIMULATION_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"Var1\",\"parameters\":{\"Vbias\":{\"value\":\"1.5\",\"min\":\"0\",\"max\":\"3\",\"status\":\"min/max\",\"tunable\":true},\"InFreq\":{\"value\":\"2.4e9\",\"status\":\"Disable\"}}}"
}
```

规则说明：

- 变量名区分大小写并按一级 key 精确匹配；名称为空或使用保留 key `BasicParameters` 时失败。
- 已有变量只覆盖请求中提供的字段；由于 `value` 必填，每次都会更新 `Initial`，其他未提供字段保持原值。
- 新增变量会创建完整内部结构；未提供的 `Min`、`Max` 为空，`Status` 为 `Disable`，`Tunable` 为 `false`。
- 调用 `setItemParamsInfo()` 后，`SGI_VarEqn` 会读取每个变量的 `Initial` 并刷新到原理图页面。
- 任一变量校验失败时，整次更新不生效；工程保存失败时恢复更新前的全部变量。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-update-sp-001",
  "event_type": "UPDATE_SIMULATION_COMPONENT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "simulation component updated",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"SP2\"}"
}
```

### 9.4 设置器件正常、禁用或短路状态

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-state-r1-001",
  "type": "SET_COMPONENT_ACTIVE_STATE",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"R1\",\"state\":\"SHORTED\"}"
}
```

最终成功事件中的 `payload_json` 返回 `project_path`、`instance_name` 和已经设置的 `state`。

### 9.5 替换 TermG 或 P_nToneG 并继承连线

将原理图中的 `TermG1` 替换为 `P_nToneG`：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-replace-port-001",
  "type": "REPLACE_PORT_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"target_instance_name\":\"TermG1\",\"replacement_component_type\":\"P_nToneG\",\"parameters\":{\"Z0\":{\"value\":\"50\",\"unit\":\"Ohm\"},\"Num\":{\"value\":\"1\"},\"Freq[1]\":{\"value\":\"1\",\"unit\":\"GHz\"},\"P[1]\":{\"value\":\"polar(dbmtow(0),0)\"},\"Freq[2]\":{\"value\":\"2\",\"unit\":\"GHz\"},\"P[2]\":{\"value\":\"polar(dbmtow(-10),0)\"}}}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `16`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-replace-port-001",
  "event_type": "REPLACE_PORT_COMPONENT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "port component replaced",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"target_instance_name\":\"TermG1\",\"old_component_type\":\"TermG\",\"new_component_type\":\"P_nToneG\",\"new_instance_name\":\"PORT2\"}"
}
```

替换操作在同一个原理图撤销命令组中完成。器件创建、参数应用、连线恢复、连接数量检查或工程保存失败时，服务端会撤销本次替换。

### 9.6 为指定器件端口挂载 Out

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-attach-out-001",
  "type": "ATTACH_OUT_COMPONENT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"target_instance_name\":\"U1\",\"pin_index\":0}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `17`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-attach-out-001",
  "event_type": "ATTACH_OUT_COMPONENT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "Out component attached",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"target_instance_name\":\"U1\",\"pin_index\":0,\"out_instance_name\":\"Out1\"}"
}
```

`pin_index` 仅在请求提供时回传；`out_instance_name` 是服务端实际创建的 `Out` 实例名。单引脚器件可以省略 `pin_index`。若省略后目标不是单引脚器件、指定引脚不存在、无法创建或连接 `Out`，最终事件返回 `RESULT_STATUS_FAILED`，工程保持调用前状态。

### 9.7 查询原理图全部器件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-list-components-001",
  "type": "LIST_SCHEMATIC_COMPONENTS",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\"}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `18`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-list-components-001",
  "event_type": "LIST_SCHEMATIC_COMPONENTS",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "schematic components listed",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"component_count\":1,\"components\":[{\"instance_name\":\"R1\",\"component_type\":\"R\",\"general_type\":\"\",\"sub_type\":\"\",\"active_state\":0,\"state\":\"NORMAL\",\"parameters\":{\"R\":{\"Value\":\"50\",\"Unit\":\"Ohm\"}}}]}"
}
```

### 9.8 根据器件名查询信息

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-get-component-001",
  "type": "GET_SCHEMATIC_COMPONENT_INFO",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"R1\"}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `19`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-get-component-001",
  "event_type": "GET_SCHEMATIC_COMPONENT_INFO",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "schematic component found",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"instance_name\":\"R1\",\"component\":{\"instance_name\":\"R1\",\"component_type\":\"R\",\"general_type\":\"\",\"sub_type\":\"\",\"active_state\":0,\"state\":\"NORMAL\",\"parameters\":{\"R\":{\"Value\":\"50\",\"Unit\":\"Ohm\"}}}}"
}
```

### 9.9 从文件加载原理图

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-load-schematic-001",
  "type": "LOAD_SCHEMATIC_FROM_FILE",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"schematic_path\":\"C:/test/schematic.ep\"}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `20`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-load-schematic-001",
  "event_type": "LOAD_SCHEMATIC_FROM_FILE",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "schematic loaded from file",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"schematic_path\":\"C:/test/schematic.ep\",\"component_count\":12,\"net_segment_count\":4}"
}
```

### 9.10 获取器件固有参数

批量查询请求：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-static-params-001",
  "type": "GET_COMPONENTS_STATIC_PARAMS",
  "payload_json": "{\"original_uuids\":[\"d0004c57-db4b-4587-b3c5-e82a07258cc7\",\"unknown-uuid\"]}"
}
```

单条查询时可将 `payload_json` 改为：

```json
"{\"original_uuid\":\"d0004c57-db4b-4587-b3c5-e82a07258cc7\"}"
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `21`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-static-params-001",
  "event_type": "GET_COMPONENTS_STATIC_PARAMS",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "器件固有参数获取成功",
  "payload_json": "{\"code\":200,\"message\":\"器件固有参数获取成功\",\"data\":[{\"模型id\":\"d0004c57-db4b-4587-b3c5-e82a07258cc7\",\"重量\":\"\",\"尺寸\":\"3.60mm×1.60mm×0.07mm\",\"封装\":\"\",\"所属厂商\":\"电科13所第十七专业部\",\"成本\":\"\"},null]}"
}
```

上游返回 `code == 200` 时事件状态为 `RESULT_STATUS_SUCCESS`；其他业务 `code` 对应 `RESULT_STATUS_FAILED`，但完整上游响应仍原样保存在 `payload_json`。网络超时、连接失败或响应不是 JSON 对象时，事件状态为失败，具体转发错误见事件 `message`。

### 9.11 抗烧毁评估

请求：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-anti-burnout-001",
  "type": "SIMULATE_ANTI_BURNOUT",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\"}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `22`。

部分器件成功、部分器件失败时的最终事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-anti-burnout-001",
  "event_type": "SIMULATE_ANTI_BURNOUT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "抗烧毁评估完成",
  "payload_json": "{\"results\":[{\"component_type\":\"Attenuator\",\"instance_name\":\"Attenuator1\",\"simulated_input_power\":\"12.4 dBm\",\"max_input_power\":\"1 W\",\"result\":\"无抗烧毁风险\"},{\"component_type\":\"BPFButterworth\",\"instance_name\":\"BPF1\",\"simulated_input_power\":\"\",\"max_input_power\":\"2 W\",\"result\":\"未找到有效的激励源\"}]}"
}
```

`results` 中每个对象包含：

- `component_type`：器件类型。
- `instance_name`：器件实例名。
- `simulated_input_power`：成功读取时为带单位的仿真输入功率；读取失败时为空字符串。
- `max_input_power`：带单位的最大允许输入功率。
- `result`：评估成功时为 `无抗烧毁风险` 或 `有抗烧毁风险`；该器件无法评估时为具体失败原因。

### 9.12 创建工程

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-create-project-001",
  "type": "CREATE_PROJECT",
  "payload_json": "{\"name\":\"demo_project\",\"author\":\"JGL\",\"path\":\"D:/projects\"}"
}
```

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `23`。

最终成功事件示例：

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-create-project-001",
  "event_type": "CREATE_PROJECT",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "project created",
  "payload_json": "{\"project_path\":\"D:/projects/demo_project/demo_project.epp\"}"
}
```

目标工程已存在或名称不符合 Windows 命名规则时返回具体失败原因，不会覆盖已有工程。

## 10. 网表生成链路完整调用示例

建议先调用 `FetchEvent` 建立流式订阅，再调用 `PerformAction` 提交任务。两次调用的 `client_uuid` 必须一致。

### 10.1 PerformAction 请求

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-generate-schematic-001",
  "type": "GENERATE_SCHEMATIC_FROM_NETLIST",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"netlist_path\":\"C:/test/netlist.log\",\"clear_before_import\":true}"
}
```

`clear_before_import` 可省略，默认值为 `false`：为 `true` 时导入前清空 `main` 原理图；为 `false` 时追加到现有原理图。

如果 Postman 未识别最新枚举，请重新导入 `ecserver.proto`，也可以临时将 `type` 填为数值 `13`。

### 10.2 FetchEvent 运行事件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-generate-schematic-001",
  "event_type": "GENERATE_SCHEMATIC_FROM_NETLIST",
  "status": "RESULT_STATUS_RUNNING",
  "message": "task accepted",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"netlist_path\":\"C:/test/netlist.log\",\"clear_before_import\":true}"
}
```

### 10.3 FetchEvent 成功事件

```json
{
  "client_uuid": "postman-test-client-001",
  "task_id": "postman-generate-schematic-001",
  "event_type": "GENERATE_SCHEMATIC_FROM_NETLIST",
  "status": "RESULT_STATUS_SUCCESS",
  "message": "schematic import finished",
  "payload_json": "{\"project_path\":\"C:/test/project.epp\",\"netlist_path\":\"C:/test/netlist.log\",\"schematic_path\":\"C:/test/schematics/main/schematic.ep\",\"clear_before_import\":true,\"symbols_added\":10,\"nets_added\":8,\"lines_added\":12,\"net_points_added\":4}"
}
```

字段说明：

- `schematic_path`：生成并保存后的 `main/schematic.ep` 文件绝对路径。
- `symbols_added`：本次新增的器件数量。
- `nets_added`：本次新增的网段数量。
- `lines_added`：本次新增的连接线数量。
- `net_points_added`：本次新增的网络连接点数量。

服务端固定操作工程中的 `main` 原理图，调用程序目录下的 `schematic_drawing_tool_edi/schematic_drawing_tool.exe` 解析网表，成功后自动保存工程。如果工程、`main` 原理图、网表文件、绘图工具或配置文件不可用，最终事件状态为 `RESULT_STATUS_FAILED`，原因通过 `message` 返回。
## 11. 注意事项

- `client_uuid`、`task_id`、`type` 必须填写。
- `payload_json` 必须是合法 JSON 对象字符串。
- 涉及工程的任务中，`project_path` 必须指向已存在的 `.epp` 文件。
- `CREATE_SIMULATION_COMPONENT` 只接收 `project_path` 和非空 `component_type`，按工厂默认参数创建器件；携带 `parameters` 会被拒绝。
- `UPDATE_SIMULATION_COMPONENT` 必须提供器件 `instance_name` 和非空 `parameters`；任意普通器件均可更新已有参数，只有 `HarmonicBalance`、`XDB`、`P_nToneG`、`P_nTone` 和 `Var` 支持按各自规则新增参数或变量；Sweep 引用参数必须通过对应校验。
- `DELETE_SIMULATION_COMPONENT` 和 `SET_COMPONENT_ACTIVE_STATE` 使用 `instance_name` 精确定位器件。
- 器件创建、参数更新、删除或状态设置成功后会立即保存工程；保存失败时回滚本次操作。
- `REPLACE_PORT_COMPONENT` 只能在 `TermG` 和 `P_nToneG` 之间替换；目标器件和新器件都必须只有一个外部引脚，替换会保留原连线关系。
- `ATTACH_OUT_COMPONENT` 必须提供 `target_instance_name`；多引脚器件还必须提供从 `0` 开始的 `pin_index`，未提供时目标必须是单引脚器件；成功后返回实际创建的 `out_instance_name`。
- `GENERATE_SCHEMATIC_FROM_NETLIST` 必须提供已存在的工程文件和网表文件，固定操作 `main` 原理图。
- `clear_before_import=true` 会在导入前清空 `main` 原理图，调用方应确认原内容允许删除。
- `MODEL_REPLACE` 必须提供已存在的 `.csv` 文件路径。
- `CAPTURE_SCHEMATIC` 必须提供图片输出路径。
- `CLOSE_PROJECT` 使用 `project_path` 和 `need_save` 关闭工程。
- 同一客户端会话内，提交任务和拉取事件应使用同一个 `client_uuid`。
- `SIMULATE_NETLIST` 的 `netlist_path` 必须是已存在的文件。
- `SIMULATE_NETLIST` 的 `task_id` 只能包含英文字母、数字、下划线、点和连字符，且不能是 `.` 或 `..`。
- `simulation/<task_id>` 已存在时任务会失败；每次调用应使用新的 `task_id`。
- 独立的 `LOG_EVENT` 暂未支持；仿真器日志通过最终事件的 `ads_output` 返回。
