# 混频链路仿真工具（mixer_simulation.exe）

输入一个已选型的 EDI 工程（`.epp`）和参数表 JSON，程序自动完成：取网表/器件库 → 寻路 → 频率规划 → 工况矩阵 → 改网表 → 跑 EDI gRPC → 解析 RAW → 派生指标。stdout 输出一行 `response.json`，方便上层程序调用。

支持上下变频、多通道、多级寻路；频率规划按混频器模式、滤波器和 `output_bw_ghz` 推导频率分支。

## 前置条件

- Windows。exe **带 requireAdministrator 清单**：2026-10-30 复核二进制内命中 `requireAdministrator`，非提权进程调用直接返回 `[WinError 740] 请求的操作需要提升`（Windows 不会弹 UAC 让用户确认）。调用方必须在**已提权会话**中运行；只有 `--help` 这类不启动子进程的场景才不受影响。
- EDI 已启动并监听 `127.0.0.1:50055`（程序固定连这个地址）。
- 网表 `netlist.log` 与器件库 `component.json` 固定从 `.epp` 同目录读取，工程须是标准导出布局：

```text
<epp目录>\xxx.epp
<epp目录>\netlist.log
<epp目录>\component.json
```

- turbocharts 默认 `C:\Program Files (x86)\EDI\turbocharts_app.exe`，装在别处用 `--turbocharts-exe` 覆盖。

`component.json` 是 EDI 工程目录里的器件库，不是参数表字段；缺它会直接失败。

## 命令行

```text
mixer_simulation.exe --project <参数表json路径> --out <结果目录> [--workers n] [--turbocharts-exe <路径>]
```

| 选项 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--project` | 是 | - | 参数表 JSON 路径 |
| `--out` | 是 | - | 结果目录；建议每次用新目录 |
| `--workers` | 否 | 10 | 并发进程数；EDI 不稳定时降到 5 |
| `--turbocharts-exe` | 否 | 自动探测 | turbocharts 可执行文件路径 |

退出码与输出：

| 退出码 | 场景 | stdout | `<out>\error.json` |
| --- | --- | --- | --- |
| `0` | 成功 | **单行** JSON（内容与 `response.json` 等价，格式不同） | — |
| `2` | 参数/工程预检失败、或仿真失败 | 同一份失败 JSON：`{"status":"failed","errors":[{location,reason}]}` | 写入（UTF-8） |
| `1` | `--project` 指定的参数表文件读不了（不存在/无权限） | PyInstaller 未捕获异常 traceback（**不是 JSON**） | **不写** |

⚠️ **编码**：`stdout` 是 **GBK（cp936）**，`error.json` / `response.json` 是 **UTF-8**。用 UTF-8 解 stdout 会在中文处抛 `UnicodeDecodeError`，按下例做「先 UTF-8 再回退 cp936」。

推荐用 Python subprocess 参数列表、`shell=False` 调用，不拼 shell；成功时 `stdout` 是**单行** JSON、失败时是**缩进多行** JSON，两种都能直接 `json.loads`：

```python
import json, subprocess

cmd = [r"<mixer_simulation.exe路径>", "--project", r"<参数表json路径>", "--out", r"<结果目录>", "--workers", "10"]
r = subprocess.run(cmd, capture_output=True, shell=False)          # 注意：不要 text=True
try:
    out = r.stdout.decode("utf-8")
except UnicodeDecodeError:
    out = r.stdout.decode("cp936", errors="replace")               # stdout 实际是 GBK
payload = json.loads(out)                                          # 失败时也是 JSON（exit 2）
```

## 参数表（--project JSON）

### 工程级

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 工程名 |
| `epp` | 是 | `.epp` 绝对路径 |
| `links` | 是 | 链路数组，至少一条 |

### 链路级

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `switch_bits` | 否 | 当前链路使用的全部开关及 Bits；无开关可省略 |
| `defaults` | 是 | 该链路公共工况 |
| `indicators` | 是 | 指标数组；受害链路可为空数组 |

`switch_bits[]`：`instance`（开关实例名）+ `bits`（只含 `0/1` 的 Bits 字符串）。

⚠️ **省略 `switch_bits` = 工具自动把路径上的开关置为导通**（不是「沿用网表里的 `Bits=` 值」）。2026-10-30 实测同一工程：不给 `switch_bits` 与显式 `bits="1"` 结果完全一致（变频增益 25.19/27.29 dB），而显式 `bits="0"` 得到 −10.39/−6.44 dB（隔离）。所以要测隔离/关断状态必须显式写 `bits`。

⚠️ **2026-11-10 实测：省略 `switch_bits` ≡ 沿用网表的 `Bits=`，不是自动导通**。`NC16103C_3040`（`component.json` type=`SP1T`、网表 `Bits="0"`）四组对照（峰值口径，Pin −30 dBm）：

| `WID330370_C022`(限幅器) | `NC16103C_30402`(SP1T) | 增益 |
| --- | --- | --- |
| 0 | 0 | −3.3 ~ +0.1 dB |
| 0 | 1 | **32.1 ~ 33.9 dB** |
| 1 | 0 | −3.3 ~ +0.1 dB |
| 1 | 1 | **32.1 ~ 33.9 dB** |

两点结论：① 该链路通断只由 `SP1T` 决定（两态，无中间态）；限幅器的 `Bits` 不影响增益。② **省略 `switch_bits` 得到的 0 dB 就是网表里的 `Bits="0"`**。**链路含开关时请显式写 `switch_bits`**。

### defaults

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `in_port` | 是 | RF 输入端口号 |
| `out_port` | 是 | IF 输出端口号 |
| `input_freq_ghz` | 是 | 输入频率范围 `[min,max]` |
| `input_power_dbm` | 是 | 默认输入功率 |
| `lo` | 是 | 按混频器顺序排列的本振数组 |

### lo

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `freq_ghz` | 是 | 本振频率范围 `[min,max]`；固定本振写 `[f,f]` |
| `power_dbm` | 是 | 本振功率 |
| `output_bw_ghz` | 否 | 该级混频器输出中频带宽；省略或 0 表示单点 |

`lo[]` 不写混频器实例名，按寻路得到的混频器顺序一一配对。

### indicators（15 个）

每个指标固定有 `name`，其它字段按指标类型填。

指标名先做**归一化**（去空白 + 忽略大小写）：`输入P1dB`、`PSAT`、`输入 P1dB ` 都会自动纠正成标准名并写回参数表；归一化后仍对不上 15 项才报 `UNKNOWN_INDICATOR`（始终带 `valid_indicators`；有相近名时再附带 `did_you_mean`）。仍建议直接写标准名。

⚠️ 指标级字段**逐指标校验**：除 `input_freq_ghz`、`peer_link` 外，字段既可写在指标对象里、也可放在该链路 `defaults`（两者任一即可）。这两个例外**只能写在该指标对象里**：`input_freq_ghz` 要标量（`defaults` 里是 `[min,max]` 区间）；`peer_link` 各链路的受害方不同，放 `defaults` 会被所有链路共用、还可能指向自己。

| 指标 | 字段 |
| --- | --- |
| 变频增益 | 可选 `frequency_sweep_ghz`、`orders` |
| 增益平坦度 | 无 |
| 镜像抑制 | 可选 `frequency_sweep_ghz`、`orders` |
| 本振泄漏 | 可选 `frequency_sweep_ghz`、`orders` |
| 输入 P1dB | `input_freq_ghz`、`input_power_sweep_dbm`、`orders` |
| 输出 P1dB | **三项必填，与输入 P1dB 相同**：`input_freq_ghz`、`input_power_sweep_dbm`、`orders`；仅 RAW 复用输入 P1dB 的 |
| Psat | **三项必填，同上**；仅 RAW 复用输入 P1dB 的 |
| 噪声 | 可选 `noise_sweep_ghz`、可选 `output_sideband`（默认 DSB）、`orders` |
| 带内杂散 | `input_power_dbm`、`orders` |
| 带内杂散抑制 | 无 |
| 带外杂散 | 可选 `frequency_sweep_ghz`、`orders` |
| 带外杂散抑制 | 无 |
| 通道间隔离度 | `peer_link: {"link_index": N}` —— **必须是对象**，N 为受害链路下标、**0 基**（两条链路互为受害即 `0→1`、`1→0`）；写数字 `"peer_link": 2` 会报 `LINK_JSON_INVALID`；可选 `frequency_sweep_ghz`、`input_power_dbm`、`orders` |
| 单通道线性动态范围 | `snr_min_db` |
| 交调抑制 | `tone_frequencies_ghz`、`input_power_dbm`、`orders` |

扫参字段统一为 `{start, stop, step}` —— **单位跟字段走**：频率类（`frequency_sweep_ghz`、`noise_sweep_ghz`）是 GHz，功率类 `input_power_sweep_dbm` 是 dBm。

指标级 `input_freq_ghz` 与 `input_power_sweep_dbm` 的形状（2026-10-30 实测）：

| 字段 | 有效形状 | 实测报错 |
| --- | --- | --- |
| `input_power_sweep_dbm`（输入 P1dB / 输出 P1dB / Psat） | **`{start, stop, step}`**（dBm） | 传列表 `[-40, -10]` → `必须是有效扫参对象` |
| 指标级 `input_freq_ghz`（输入 P1dB 等） | **标量单值**（GHz，如 `34.5`） | 传 `[34, 35]` 或 `{start, stop, step}` → `必须位于链路输入频率范围内` |

⚠️ **`输出 P1dB` / `Psat` 与 `输入 P1dB` 各自都要写全** `input_freq_ghz` + `input_power_sweep_dbm` + `orders`（2026-11-10 实测：只补 `input_power_sweep_dbm` 仍报 `links[i].indicators[j].orders / input_freq_ghz: 缺少必填字段`）。
指标级 `input_freq_ghz` **不能靠 `defaults` 顶替**：`defaults` 里是 `[min,max]` 区间、指标级要标量。包装层已把这条单独拦下（`_INDICATOR_LOCAL_ONLY`），hint 直接给标量写法，不再提示「放进 defaults」。

`tone_frequencies_ghz`（交调抑制）是**两元素列表**（GHz，如 `[34.0, 34.1]`）；同时 `orders` 必须给全 `FREQ`、`FREQ2`、`LO`，缺 `FREQ2` 会报 `缺少 HB 基频 ['FREQ2'] 的阶数`（2026-10-30 实测）。

`orders` 的键是 HB 基音名：第一输入音 `FREQ`、第二输入音 `FREQ2`、本振 `LO` 或多级 `LO1/LO2/...`；`MaxOrder` 自动取最大值。

### 示例

```json
{
  "name": "68",
  "epp": "D:/project/reference/.../68-.../xxx.epp",
  "links": [
    {
      "switch_bits": [{"instance": "NC16103C_30401", "bits": "1"}],
      "defaults": {
        "in_port": 1,
        "out_port": 2,
        "input_freq_ghz": [33, 37],
        "input_power_dbm": -30,
        "lo": [
          {"freq_ghz": [29.1, 33.1], "power_dbm": 4, "output_bw_ghz": 0.4}
        ]
      },
      "indicators": [
        {"name": "变频增益", "orders": {"FREQ": 5, "LO": 5}},
        {"name": "输入 P1dB", "input_freq_ghz": 35, "input_power_sweep_dbm": {"start": -40, "stop": -10, "step": 2}, "orders": {"FREQ": 5, "LO": 5}},
        {"name": "镜像抑制", "frequency_sweep_ghz": {"start": 33, "stop": 37, "step": 0.04}, "orders": {"FREQ": 5, "LO": 5}},
        {"name": "噪声", "orders": {"FREQ": 5, "LO": 5}}
      ]
    }
  ]
}
```

### 工况口径

- 镜像抑制：主信号和镜频各一个扫频工况；单级下变频镜频读 `(−1,+1)`。
- 带外杂散：只生成一条输入 RF 扫频，扫 `FREQ`，LO 跟踪。
- 带内杂散：LO 固定，按 `m·RF + n·LO = IF` 枚举落点，每个落点一个单点工况。
- 噪声：默认 DSB，一个 `noisefreq` 扫频网表；填 SSB 才按每个 `noisefreq` 点拆成单点工况。
- 单通道线性动态范围：积分带宽取末级 `output_range` 宽度。

## 返回 JSON

成功时 stdout 是**单行** JSON（GBK 编码），`<out>\response.json` 是**缩进多行**（UTF-8 编码）；两者内容等价、格式不同，`json.loads` 都能直接解析。失败时（退出码 2）stdout 与 `error.json` 都是**缩进多行** JSON。

```json
{
  "status": "ok",
  "condition_count": 12,
  "failures": [],
  "figs": {"dir": "figs", "count": 11},
  "runs": {"dir": "runs", "index": "runs/index.json"},
  "indicators": [
    {"indicator": "变频增益", "status": "ok", "unit": "dB", "min": 25.19, "max": 27.29, "points": 100},
    {"indicator": "镜像抑制", "status": "ok", "unit": "dB", "value": 41.40},
    {"indicator": "噪声", "status": "ok", "unit": "dB", "value": 5.24, "value_dsb": 5.24, "points": 100},
    {"indicator": "带内杂散", "status": "incomplete", "unit": "dBm", "reason": "数值底"}
  ]
}
```

- `status`：`ok` 或 `failed`。
- `condition_count`：仿真工况数（不含 derive）。
- `failures[]`：失败工况 `{id, indicator, error}`；成功时为空。
- `figs`：PNG 目录与数量。
- `runs`：RAW 目录与 `index.json` 路径。
- `indicators[]`：本次请求的指标摘要（≤ 15 个，未请求的不出现）；`unit` 为 `dB`/`dBm`/`dBc`；单值指标给 `value`，扫频指标给 `min`/`max`/`points`；`status=incomplete` 时带 `reason`。
- `warnings[]`（可选）：MCP 包装层的结果自检提示，例如「噪声 SSB 在带边是数值底，请用 DSB」「交调抑制为负值说明双音已推入压缩」。出现时先读它，别直接拿 `value` 下结论。
- **MCP 工具返回**再包一层信封：`success` / `out_dir` / `artifacts[]`（图片、RAW、json 的绝对路径清单，多文件时只列前 60 个）；exe 的 `figs` / `runs` 平铺字段仍在 `<out>/response.json` 里。MCP 侧参数名为 `project`（参数表 dict）/ `out_dir` / `workers` / `turbocharts_exe` / `timeout_seconds`（默认 1800，上限 3600），与下表 CLI 选项一一对应。

指标细节按顺序读：`response.json` → `indicator_results/<指标>.json` → `runs/index.json` → `runs/<condition>/history/result.raw`。

### 失败响应（退出码 2）

参数/工程预检失败与仿真失败都返回同一份 JSON（同时写入 `<out>\error.json`，UTF-8）：

```json
{
  "status": "failed",
  "errors": [
    {"location": "project.epp", "reason": "未找到器件库，期望 C:\\...\\11\\component.json"}
  ]
}
```

- 顶层是 `status: "failed"` + **`errors[]`**，每项 `{location, reason}`；**不是** `failures[]`。
- `failures[]` 只出现在**成功响应**里，表示"部分工况失败"（`{id, indicator, error}`），此时 `status` 仍为 `ok`、退出码仍为 0。
- `errors[]` 只给定位与原因，完整 ADS 日志不落盘。
- MCP 包装层会把 `errors[]` 拼成 `字段: 原因` 放进 `message`，并把原始 `errors[]` 一并返回 —— 不用再翻 `<out>\error.json` 才知道该改哪个字段。
- 包装层的**参数表预检**（结构 / 必填字段 / 指标名 / peer_link）一次收集**全部**问题：顶层 `error_code`、`message` 取第一条，另带 `error_count` 与 `all_errors[]`（每项含 `error_code`、`message`、`location` 或 `missing_field`）。改一轮就能全过，不必逐个试。

## 产物清单

每次运行落在 `--out` 指定目录：

```text
<out>/
├─ response.json
├─ indicator_results/01-...json ... 15-...json
├─ runs/
│  ├─ index.json
│  └─ <condition>/history/result.raw
└─ figs/<condition>.png
```

- `response.json`：指标摘要、失败项、图数量和 RAW 索引路径。
- `indicator_results/`：每个指标一份完整结果；曲线指标带 `per_point` 或 `per_branch`。
- `runs/index.json`：工况 ID、指标、状态、输入/输出/工作点、RAW 相对路径。
- `figs/*.png`：每个工况一张 RAW 频谱图（不是指标的派生曲线；数值以 `indicator_results` 为准）。
- 不生成 `conditions.json`、`charts.json`、CSV、`netlist.log`、`ads_output.log`，也不生成 PDF/DOCX 报告。

## 注意事项

- 多链路工程的 `indicators[]` 汇总会把各链路的点合并成 min/max（`points` = 链路数 × 频点），一条链路数值底就会污染 `增益平坦度` 这类汇总值；**分链路数值请读 `indicator_results/<指标>.json` 的 `per_branch`**。
- 复跑同一个 `out_dir` 时，包装层会先清掉上一轮的 `response.json` / `error.json` / `indicator_results` / `runs` / `figs`（目录里其它文件不动），避免旧指标 JSON 混进本次 `artifacts`、也避免噪声自检读到旧文件。
- 程序不接受 `--netlist`、`--components`；固定从 `epp` 同目录取网表和器件库。
- `component.json` 缺失会直接失败，它是器件库不是参数表字段。
- 扫频步长越大图越稀；要看连续轨迹就把 `frequency_sweep_ghz.step` 调小。
- `--workers` 过高可能压垮 EDI/ADS；偶发失败先降到 5。
- 多级链路：寻路和频率规划支持；噪声和镜频的逐级反算目前按单级实现。
- 原始 project、netlist、component 文件不会被修改。

## 已知问题（exe 侧待修）

以下为 2026-11-10 实测到的 exe 行为缺陷，包装层暂时无法修正，按「规避」列使用：

| # | 现象 | 实测证据 | 规避 |
| --- | --- | --- | --- |
| 1 | **`out_port` 不是 2 时，该链路全部指标取数错节点**（仿真本身是对的，错在从 RAW 取错变量） | 双通道工程 `in_port=3` / `out_port=4`、**显式 `switch_bits` 置导通**（`NC16103C_30402` 为 `SP1T`、`bits="1"`）：`变频增益` 仍报 **−144 dB**，而同一份 `result.raw` 按 IF=3.9 GHz 取 `Out2` 分量为 **正功率量级（−3 ~ +4 dBm，对应 27~34 dB 增益）**，`Out1` 只有 −174~−151 dBm（两者都远高于 −144 dB；绝对电平口径见下方矩阵说明）；exe 自带 figs 的曲线标签仍写 `dBm(Out1)` | 多通道工程目前只信首通道（`out_port=2`）的指标；其余通道用 `turbocharts_convert` 导 RAW 的 `dBm_Out2` 自行核算。**首通道不受影响**（ch1 `镜像抑制` 同工况 RAW：主 IF −4.8~−2.7 dBm、镜 IF −52.9~−45.1 dBm，与报出的 41.40 dB 自洽） |
| 2 | **`输入 P1dB` 的参考增益依赖请求组合** | 同一份 RAW：请求里同时带 `变频增益` 时得 **−19.38 dBm**；只请求 `输入 P1dB`（或连同 `输出 P1dB`/`Psat`）时得 **−33.81 dBm**。本条链前端有限幅器，模型在 Pin≈−33.5 dBm 处有 5 dB 台阶，被 exe 当成压缩点 | 把 `变频增益` 与 `输入 P1dB` 放在同一次请求里，**取 −19.4 dBm 那个值** |

> ⚠️ **别把两种现象混为一谈**：① **省略 `switch_bits`** 时该链路 IF 只有 −30 dBm（≈0 dB 增益，开关没导通，见「链路级」节补充）；② **显式置导通后**才 33 dB、但指标仍报 −144 dB —— 只有 ② 是取数缺陷。

> **ch2 电平已定位（2026-11-10 四组开关矩阵）**：`Out2`@IF 只有两种结果 —— `SP1T` 置 1 时 **+2.1~+3.9 dBm（峰值口径 32.1~33.9 dB，换算工具口径 ≈29.1~30.9 dB）**，置 0 时 **−33.3~−29.9 dBm（≈0 dB）**；限幅器 `Bits` 无影响。**6.6 dB 不是开关态造成的，没有中间态。**曾经的 27.3 dB 未能复现（可能来自另一次参数不同的运行），需要该次 `out_dir`/命令行才能核对。

## 排错

1. 退出码 `2`：先看 `<out>\error.json`（UTF-8）的 `errors[].location/reason`，再看 stdout（GBK 编码）。
2. 退出码 `1` 且 stdout 是 traceback：`--project` 指的参数表文件读不了（路径错/无权限）。**这种情况不写 `error.json`**，按 traceback 第一行修路径即可。
3. stdout 中文乱码或 `json.loads` 抛 `UnicodeDecodeError`：stdout 是 GBK，按「先 UTF-8 再回退 cp936」解码；`error.json`/`response.json` 才是 UTF-8。
4. `未找到网表/器件库`：检查 `.epp` 同目录是否有 `netlist.log`、`component.json`。
5. 所有工况都失败：先查 `127.0.0.1:50055` 是否监听；EDI 掉了就重启 EDI。
6. 出图为 0：检查 turbocharts 默认路径，或用 `--turbocharts-exe` 指定。
7. 个别工况失败：看 `failures[].error`；完整日志已不保留（错误摘要只在 `failures` 里）。
8. `[WinError 740] 请求的操作需要提升`：exe 带管理员清单，而调用方（终端 / MCP 服务）未提权。以管理员身份重启调用方后重试，不要试图在代码里绕过。
9. `[型号] Failed to parse the model file`：多数是**工程刚打开、EDI 还在把模型铺到 `<EDI>\simulation\models\`** 时抢跑（2026-10-30 实测：同一工程首次跑失败、重开工程并重新导出网表后连续两次结果逐位一致）。先重开工程 + 重新导出 `netlist.log`/`component.json` 再跑；仍失败才怀疑模型本身损坏。
10. `必须是有效扫参对象`：`input_power_sweep_dbm` 传了列表，改 `{start, stop, step}`。
11. `必须位于链路输入频率范围内`：指标级 `input_freq_ghz` 传了区间/对象，改成标量单值。
12. `PORT_NOT_FOUND`：`in_port`/`out_port` 不在网表 `Port:` 行的 `Num=` 里；hint 会列出可用端口号。另：`in_port` 与 `out_port` 相同会在 MCP 层直接报 `LINK_JSON_INVALID`。
13. `SWITCH_NOT_FOUND`：`switch_bits[].instance` 拼错，或该实例在网表里没有 `Bits=`。**旧行为是静默把它当成「没配开关」而全部导通**（结果看起来正常、其实是错的），现在提前拦下。
14. `LO_COUNT_MISMATCH`：`lo[]` 数量超过网表里的混频器总数。注意本检查按**网表混频器总数**比较，路径只经过部分混频器（如双通道共用本振）时仍以 exe 的寻路结果为准。
15. `工况矩阵为空（condition_count=0）`：exe 对「频率规划推不出工况」不给 `errors`，包装层补了这句推断 —— 多半是 `input_freq_ghz` 与 `lo` 的组合超出器件/滤波器范围，或 `out_port` 的频段对不上。
16. 成功响应里出现 `warnings[]`：先读它再下结论。目前有两类自检 —— 噪声 SSB 带边/全带数值底（改用 DSB）、交调抑制为负值（双音功率过高等）。
17. `UNKNOWN_INDICATOR`：指标名归一化（去空格/忽略大小写）后仍对不上 15 项清单；返回里始终带 `valid_indicators`，有相近名时附带 `did_you_mean`（`difflib` 相似度 < 0.6 就没有）。
18. `LINK_MISSING_FIELD`：包装层已按 `_INDICATOR_REQUIRES` 校验 `input_freq_ghz` / `input_power_sweep_dbm`（指标级 `input_freq_ghz` 只认标量）；仍报缺字段就说明是 `orders` 这类 exe 侧要求 —— `输入 P1dB` / `输出 P1dB` / `Psat` 都要带 `orders`。
