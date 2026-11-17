# 独立网表寻路工具

输入网表文件、对应 EP 原理图文件、端口对列表，输出每个有向端口对的全部简单路径，以及每条路径上每个开关的目标 Bits。纯本地固定代码，Python 3.10+，运行时仅依赖标准库；无需 EDI 运行、ADS、gRPC、AI 或 HTTP 服务。源网表与 EP 均只读。不输出 `schema_version`。

## 调用

在仓库根目录运行：

```powershell
.venv/Scripts/python.exe Netlist_Pathfinder/Netlist_Pathfinder.py --netlist 'D:/EDI/EDI-Workspace/projects/NC35352ME-812-ideal_alternative/netlist.log' --ep 'D:/EDI/EDI-Workspace/projects/NC35352ME-812-ideal_alternative/schematics/main/schematic.ep' --pairs "1-2,2-1"
```

EXE 参数相同：

```powershell
Netlist_Pathfinder.exe --netlist 'C:/Program Files (x86)/EDI_fzTest/data/EDI-Workspace/projects/Ka波段收发TR组件_实际/netlist.log' --ep 'C:/Program Files (x86)/EDI_fzTest/data/EDI-Workspace/projects/Ka波段收发TR组件_实际/schematics/main/schematic.ep' --pairs "1-2,3-1"
```

`--pairs` 使用英文逗号分隔有向端口对，每项格式为 `输入端口-输出端口`：

```text
--pairs "1-2"          # 端口 1 → 端口 2
--pairs "1-2,3-1"      # 端口 1 → 端口 2，以及端口 3 → 端口 1
--pairs "1-2,2-1"      # 分别查询正向和反向
```

端口对按输入顺序返回，重复项保留。支持逗号和连字符两侧空格；建议始终加双引号。编号为不带前导零的正整数，输入输出编号必须不同。拒绝空项、末尾逗号、中文逗号及其他分隔符。

已移除 JSON 端口对输入和 `--pairs-file`，仅支持上述简写。输出仍为 JSON，方便程序读取。

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--netlist` | 必填 | 网表文件路径，无扩展名限制 |
| `--ep` | 必填 | 与该网表对应的 schematic.ep，不能使用其他工程的 EP |
| `--pairs` | 必填 | 有向端口对列表，例如 `"1-2,3-1"` |
| `--max-paths` | 10000 | 每个端口对最多保存路径数；0 不限 |
| `--max-states` | 1000000 | 每个端口对尝试搜索边数预算；0 不限 |
| `--timeout` | 30 | 每个端口对搜索时间预算（秒）；0 不限；不含文件解析和 JSON 序列化 |
| `--encoding` | auto | 先 UTF-8/BOM，解码失败再 GB18030；也可指定 gb18030 等编码 |
| `--help` | 无 | 输出文本帮助并退出 |

普通调用 stdout 只有一行 UTF-8 JSON，日志不混入输出。退出码 0 表示每个端口对均完成且没有状态计算错误；无路径属于正常查询结果。参数、文件、解析、端口、开关状态错误或搜索不完整返回 1。`--help` 是单行 JSON 约定的例外。调用方仍应读取结果状态与各层 errors。

## 现有器件规则

规则沿用项目 `NetlistPathFinder` 的器件连通语义，集中在 `pathfinder.py`，未修改旧智能体和仿真工具。

| 器件分类 | EP genl_type | 连通规则 |
| --- | --- | --- |
| 放大器/反演器 | 1、29、30、31、43 | 仅允许 index 0 → index 1 |
| 开关 | 27 | 仅允许 index 0 ↔ index n（n > 0） |
| 功分器、电桥 | 34、35 | 同上，不允许两个非零支路直接互通 |
| 其余已匹配 EP 的器件 | 其他值，包括空值 | 所有不同非地节点之间双向连通 |

这里的 index 是**网表实例行中位置节点从 0 开始的编号**，不是端口的 `Num`，也不按 EP 图形坐标、旋转角度或 pin 出现顺序重新排序。网表决定连线拓扑，EP 按实例名提供 genl_type。EP 多余器件不参与寻路。EP 中匹配到但类型为空的器件按旧规则归入双向器件。

例如：

```text
LinearDevice:SW1 Common BranchA BranchB Bits="00" Model="SP2T"
```

Common 为 index 0，BranchA 为 index 1，BranchB 为 index 2。

地节点 `0` 不加入信号图，防止所有器件经公共地误连，但保留原引脚位置。例如 `SW1 Common 0 BranchB` 中 BranchB 仍为 index 2。重复网络名的不同引脚也保留各自 index，不覆盖；同名网络间不产生自身环边。

端口由网表 `Port:Name SignalNode ... Num=N` 识别，不要求 EP 有同名端口；同一编号重复则报错。不同端口编号若连接到同一网络，返回一条零器件路径。其他端口所连接的网络仍可作为中间网络；工具不计算端口负载效应。

下列基础连接器件允许不出现在 EP：`Short`、`ebond`、`C`、`Res`、`R`、`L`、`CPWG_RDL1`、`Match`，按双向规则处理。其他网表实例缺少 EP 信息时，返回 `METADATA_MISSING`，不会凭 Bits、型号名称或节点数量猜测器件类型。

**这些是拓扑寻路规则，不是完整射频物理模型。**功分器/电桥采用现有公共端口规则；混频器等其余多端口器件按旧规则双向连通，不区分 RF/LO/IF 角色，不判断频率转换、隔离度、增益、频段或路径是否独占。`Bits` 的存在不意味着器件是开关，移相器和衰减器的 Bits 不由寻路决定，因此保持原值且不进入 switch_settings。

## 开关 Bits 计算

搜索忽略网表当前开关状态，枚举所有允许的公共端 ↔ 支路连接。目标状态按现有编码规则计算：

```text
throw_index = max(from_index, to_index)  # 其中一个 index 必须为 0
bit_width = len(原网表 Bits 二进制字符串)
required_bits = format(throw_index, f"0{bit_width}b")
```

即：**直接使用非零支路 index 作为整数编码，不减 1**。反向经过同一支路时编码不变；左侧补零，输出 Bits 始终为字符串，保留位宽。

| 位宽 | 经过引脚 | throw_index | required_bits |
| --- | --- | --- | --- |
| 2 | 0 ↔ 1 | 1 | `01` |
| 2 | 0 ↔ 2 | 2 | `10` |
| 3 | 0 ↔ 1 | 1 | `001` |
| 3 | 0 ↔ 4 | 4 | `100` |
| 1 | 0 ↔ 2 | 2 | 无法表示，报错 |

仅支持非空二进制字面量，例如 `Bits="001"`。缺失 Bits、空值、`itob(x)`、其他表达式等返回 `SWITCH_BITS_INVALID`，不会执行表达式。若 `throw_index >= 2**bit_width`，返回 `SWITCH_BITS_OVERFLOW`，不截断、不增加位宽、不改成零基编码。

状态无法计算时，仍保留该条拓扑路径：`configurable=false`，对应 `required_bits=null`，路径 errors 提供原因。其他开关的合法计算结果仍保留。这表示拓扑存在但当前编码规则不能给出完整可用配置。

本工具继承的是现有项目的 index 编码约定，不能保证任何厂商模型都遵守。真实工程中的 `NC1628C_112` 存在三节点、1 位 Bits 的情况；对 index 2 的需求明确报告位宽不足。若实际模型采用 `0/1` 或其他编码，需取得模型定义后再扩展编码规则，不能仅凭外观推断。

只计算当前路径经过的开关。路径外的开关无附加约束；不同路径和端口对各自独立，不合并状态，不判断多条路径能否同时启用，也不保证其他支路被隔离。

## 全部路径的含义与资源预算

“全部”指在上述器件连通规则下，不重复网络节点、不重复器件的全部简单路径。不同器件、不同引脚经过方式视为不同路径；只比较节点序列无法区分并联器件或接到同一网络的不同开关支路，因此不会据此合并。

使用迭代 DFS 和反向可达性剪枝，一次解析供所有端口对复用。没有旧实现的默认 30 级深度上限，也不受 Python 递归深度限制。路径顺序遵循网表器件顺序、原引脚 index 顺序；`p1` 等编号在每个端口对内重新开始。

分支与回环可能使简单路径数量指数增长。默认预算用于控制运行时间与结果内存，达到限制返回 `status=incomplete`、`complete=false`，保留已经得到的路径，并返回 MAX_PATHS_REACHED、MAX_STATES_REACHED 或 TIMEOUT。**这时 path_count 仅是已返回路径数，不是总路径数；0 条部分结果也不代表不存在路径。**路径数量恰好达到上限且搜索确实已穷尽时可标记完成；只有发现额外路径才认定路径数量截断。

如需取消预算，可设置 `--max-paths 0 --max-states 0 --timeout 0`，但全量路径和最终 JSON 仍需要相应内存与时间。本版不提供流式文件输出。

## 输出结构

顶层包含 netlist_path、ep_path、ports、results、errors；输入与解析失败时 results 为空。每个 result 包含原端口对、status、complete、path_count、paths、errors；正常搜索还含 search_states。

完整真实输出示例见 `example_result.json`（NC35352ME-812-ideal_alternative 的 1→2、2→1）。该工程 1→2 的两只路径开关目标均为 `10`，2→1 均为 `01`；这是该工程的连线结果，不是所有工程通用的发射/接收状态定义。

每条路径包括：

- `nodes`：从输入到输出的网络节点序列。
- `steps`：器件实例、Model、分类 type、EP genl_type、进入/离开节点和 index。
- `switch_settings`：以开关实例名索引，值包含 throw_index、bit_width、current_bits、required_bits。
- `configurable` 和 `errors`：是否已成功计算路径上的全部开关状态。

| result.status | complete | 含义 |
| --- | --- | --- |
| ok | true | 已完成枚举，全部路径的开关编码可计算；无开关路径同样成功 |
| no_path | true | 已完成枚举，没有有效路径 |
| configuration_error | true | 枚举完成，但至少一条路径开关编码失败 |
| incomplete | false | 搜索未穷尽；路径中还可能包含状态计算错误 |
| error | false | 端口不存在，无法开始该组搜索 |

complete 仅表示拓扑枚举完整，不能替代 configurable；检查错误时同时查看顶层、端口对和路径三级 errors。

## 输出示例

以一只两位开关为例，EP 中 SW1 的 `genl_type` 为 `27`，网表为：

```text
Port:IN Common 0 Num=1
Port:OUT BranchB 0 Num=2
LinearDevice:SW1 Common BranchA BranchB Bits="00" Model="SP2T"
```

调用 `--pairs "1-2"`，输出如下（为方便阅读进行了缩进；实际 stdout 为单行 JSON）：

```json
{
  "netlist_path": "D:/project/netlist.log",
  "ep_path": "D:/project/schematic.ep",
  "ports": [
    1,
    2
  ],
  "results": [
    {
      "input_port": 1,
      "output_port": 2,
      "status": "ok",
      "complete": true,
      "path_count": 1,
      "paths": [
        {
          "path_id": "p1",
          "nodes": [
            "Common",
            "BranchB"
          ],
          "steps": [
            {
              "component": "SW1",
              "model": "SP2T",
              "type": "switch",
              "genl_type": "27",
              "from_node": "Common",
              "to_node": "BranchB",
              "from_index": 0,
              "to_index": 2
            }
          ],
          "switch_settings": {
            "SW1": {
              "throw_index": 2,
              "current_bits": "00",
              "bit_width": 2,
              "required_bits": "10"
            }
          },
          "configurable": true,
          "errors": []
        }
      ],
      "errors": [],
      "search_states": 3
    }
  ],
  "errors": []
}
```

路径经过 SW1 的 index 0 → index 2，因此 `throw_index=2`，按原两位宽度得到 `required_bits="10"`。`current_bits` 保留网表原值 `"00"`，工具不修改源文件。

## 解析支持与边界

支持 EDI 的扁平 `Type:Name 节点... 参数=值` 格式，节点名遵循 C++ 标识符规则或地节点 `0`。支持引号内空格、参数等号两侧空格、反斜杠续行、空行和字符串外的 `;`、`#`、`//` 行尾注释以及 `*` 行注释。EP 用括号和引号感知的 S 表达式解析，不会把字符串中的括号当作结构。

忽略已知仿真控制器、选项及变量赋值。`define`、`.subckt`、`include`/`#include` 明确返回 UNSUPPORTED_HIERARCHY；不展开外部文件或子电路，也不支持任意 SPICE 方言。非法语句、非法节点、重名器件、重复端口均报错。

自动编码回退只是为了兼容旧工程，不修复文件内容已有的乱码。输入文件若在编码层面可被错误解码而不报错，应使用 `--encoding` 明确指定正确编码。



## 具体示例
{
  "netlist_path": "C:\\Program Files (x86)\\EDI_fzTest\\data\\EDI-Workspace\\projects\\Ka波段收发TR组件_实际\\netlist.log",
  "ep_path": "C:\\Program Files (x86)\\EDI_fzTest\\data\\EDI-Workspace\\projects\\Ka波段收发TR组件_实际\\schematics\\main\\schematic.ep",
  "ports": [
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    16,
    17
  ],
  "results": [
    {
      "input_port": 1,
      "output_port": 2,
      "status": "ok",
      "complete": true,
      "path_count": 1,
      "paths": [
        {
          "path_id": "p1",
          "nodes": [
            "N__2",
            "N__4",
            "N__3",
            "N__7",
            "N__9",
            "N__10",
            "N__12",
            "N__14",
            "N__15",
            "N__16",
            "N__67",
            "N__255",
            "N__260",
            "N__261",
            "N__19",
            "N__18",
            "N__20"
          ],
          "steps": [
            {
              "component": "MASW_0110361",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__2",
              "to_node": "N__4",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "Limiter1",
              "model": "",
              "type": "bidirectional",
              "genl_type": "",
              "from_node": "N__4",
              "to_node": "N__3",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "HMC1040_DIE1",
              "model": "HMC1040_DIE",
              "type": "amplifier",
              "genl_type": "29",
              "from_node": "N__3",
              "to_node": "N__7",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110362",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__7",
              "to_node": "N__9",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "MASW_0110363",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__9",
              "to_node": "N__10",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MAAP_0113794",
              "model": "MAAP_011379",
              "type": "amplifier",
              "genl_type": "30",
              "from_node": "N__10",
              "to_node": "N__12",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110364",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__12",
              "to_node": "N__14",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "MAAD_0110471",
              "model": "MAAD_011047",
              "type": "bidirectional",
              "genl_type": "3",
              "from_node": "N__14",
              "to_node": "N__15",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "NC12163C_30401",
              "model": "NC12163C_3040",
              "type": "bidirectional",
              "genl_type": "32",
              "from_node": "N__15",
              "to_node": "N__16",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "ZPD25_2W26_40G_66711",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__16",
              "to_node": "N__67",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "ZPD25_2W26_40G_66713",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__67",
              "to_node": "N__255",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "ZPD25_2W26_40G_667113",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__255",
              "to_node": "N__260",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "ZPD25_2W26_40G_667115",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__260",
              "to_node": "N__261",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "MASW_0110365",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__261",
              "to_node": "N__19",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MAAM_0113377",
              "model": "MAAM_011337",
              "type": "amplifier",
              "genl_type": "31",
              "from_node": "N__19",
              "to_node": "N__18",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110366",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__18",
              "to_node": "N__20",
              "from_index": 2,
              "to_index": 0
            }
          ],
          "switch_settings": {
            "MASW_0110361": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110362": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            },
            "MASW_0110363": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110364": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            },
            "MASW_0110365": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110366": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            }
          },
          "configurable": true,
          "errors": []
        }
      ],
      "errors": [],
      "search_states": 40
    },
    {
      "input_port": 2,
      "output_port": 1,
      "status": "ok",
      "complete": true,
      "path_count": 1,
      "paths": [
        {
          "path_id": "p1",
          "nodes": [
            "N__20",
            "N__17",
            "N__21",
            "N__261",
            "N__260",
            "N__255",
            "N__67",
            "N__16",
            "N__15",
            "N__14",
            "N__13",
            "N__11",
            "N__9",
            "N__8",
            "N__5",
            "N__6",
            "N__2"
          ],
          "steps": [
            {
              "component": "MASW_0110366",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__20",
              "to_node": "N__17",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MAAP_0113796",
              "model": "MAAP_011379",
              "type": "amplifier",
              "genl_type": "30",
              "from_node": "N__17",
              "to_node": "N__21",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110365",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__21",
              "to_node": "N__261",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "ZPD25_2W26_40G_667115",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__261",
              "to_node": "N__260",
              "from_index": 0,
              "to_index": 2
            },
            {
              "component": "ZPD25_2W26_40G_667113",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__260",
              "to_node": "N__255",
              "from_index": 0,
              "to_index": 2
            },
            {
              "component": "ZPD25_2W26_40G_66713",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__255",
              "to_node": "N__67",
              "from_index": 0,
              "to_index": 2
            },
            {
              "component": "ZPD25_2W26_40G_66711",
              "model": "ZPD25_2W26_40G_667",
              "type": "splitter",
              "genl_type": "34",
              "from_node": "N__67",
              "to_node": "N__16",
              "from_index": 0,
              "to_index": 2
            },
            {
              "component": "NC12163C_30401",
              "model": "NC12163C_3040",
              "type": "bidirectional",
              "genl_type": "32",
              "from_node": "N__16",
              "to_node": "N__15",
              "from_index": 1,
              "to_index": 0
            },
            {
              "component": "MAAD_0110471",
              "model": "MAAD_011047",
              "type": "bidirectional",
              "genl_type": "3",
              "from_node": "N__15",
              "to_node": "N__14",
              "from_index": 1,
              "to_index": 0
            },
            {
              "component": "MASW_0110364",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__14",
              "to_node": "N__13",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MAAM_0113375",
              "model": "MAAM_011337",
              "type": "amplifier",
              "genl_type": "31",
              "from_node": "N__13",
              "to_node": "N__11",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110363",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__11",
              "to_node": "N__9",
              "from_index": 2,
              "to_index": 0
            },
            {
              "component": "MASW_0110362",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__9",
              "to_node": "N__8",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MAAP_0113793",
              "model": "MAAP_011379",
              "type": "amplifier",
              "genl_type": "30",
              "from_node": "N__8",
              "to_node": "N__5",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "QPA2640T2",
              "model": "QPA2640T",
              "type": "amplifier",
              "genl_type": "30",
              "from_node": "N__5",
              "to_node": "N__6",
              "from_index": 0,
              "to_index": 1
            },
            {
              "component": "MASW_0110361",
              "model": "MASW_011036",
              "type": "switch",
              "genl_type": "27",
              "from_node": "N__6",
              "to_node": "N__2",
              "from_index": 2,
              "to_index": 0
            }
          ],
          "switch_settings": {
            "MASW_0110366": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110365": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            },
            "MASW_0110364": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110363": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            },
            "MASW_0110362": {
              "throw_index": 1,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "01"
            },
            "MASW_0110361": {
              "throw_index": 2,
              "current_bits": "01",
              "bit_width": 2,
              "required_bits": "10"
            }
          },
          "configurable": true,
          "errors": []
        }
      ],
      "errors": [],
      "search_states": 402
    }
  ],
  "errors": []
}
