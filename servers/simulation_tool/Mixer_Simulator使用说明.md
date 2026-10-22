# 混频链路仿真工具

输入一个已选型的 EDI 工程（.epp）和混频链路参数，自动完成取网表 → 校验 → 频率规划 → 工况矩阵 → 改网表 → 跑 EDI/ADS → 解析结果 → 派生指标 → 出 PDF/DOCX 报告。普通调用 stdout 只有一行 UTF-8 JSON，包括错误情况。

## 前置条件

Windows；EDI 已运行，gRPC 服务 `127.0.0.1:50055` 可用；ADS 已安装（调用时给出根目录）；EDI 安装目录含 `turbocharts_app.exe`；PDF 使用系统自带字体 simsun.ttc / times.ttf。

## 调用

推荐 Python subprocess（JSON 走参数列表、不经过 shell，最稳）：

```python
import json, subprocess

link = {"name": "链路名", "direction": "down", "f_rf": [33, 37], "f_if": 3.9,
        "bw": 0.4, "pin": -30, "p_lo": 4,
        "channels": [{"name": "CH1", "rf_port": 1, "if_port": 2, "mixer": "<混频器实例名1>"}],
        "stages": [{"mixer": "<混频器实例名1>", "lo_port": 5, "relation": "diff",
                    "injection": "low", "f_if": 3.9}]}
cmd = [r"<mixer_simulation.exe路径>",
       "--epp", "<epp路径>", "--edi", r"<EDI根目录>", "--ads", r"<ADS根目录>",
       "--indicators", "变频增益,本振泄漏",
       "--json", json.dumps([link], ensure_ascii=False)]
r = subprocess.run(cmd, capture_output=True, shell=False)
print(r.returncode, json.loads(r.stdout.decode("utf-8")))
```

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `--epp` | 是 | - | .epp 工程文件路径 |
| `--edi` | 是 | - | EDI 安装根目录 |
| `--ads` | 是 | - | ADS 安装根目录 |
| `--indicators` | 否 | `变频增益` | 指标名，逗号分隔 |
| `--threads` | 否 | 5 | 并发线程数 |
| `--components` | 否 | `[]` | 器件选型 JSON 数组 |
| `--json` | 否 | `[]` | 链路参数 JSON 数组；`-` 表示从 stdin 读 |
| `--json-file` | 否 | - | 链路参数 JSON 文件路径；与 `--json` 二选一 |

`--json` 与 `--json-file` 同时给会报错。退出码 0 = 无 errors，1 = 有 errors。

## 链路 JSON

`--json` 是链路数组，每个元素一条链路。支持多通道、任意级变频、分段注入边。

### 链路级

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `name` | 是 | - | 链路名，作目录名；非空、不重复、无非法文件名字符 |
| `direction` | 是 | - | `down` 下变频 / `up` 上变频 |
| `f_rf` | 是 | - | RF 频段 `[min,max]` GHz，min < max |
| `f_if` | 是 | - | IF 中心频率 GHz，> 0 |
| `bw` | 是 | - | 信号带宽 GHz |
| `pin` | 是 | - | 默认输入功率 dBm |
| `p_lo` | 是 | - | 默认本振功率 dBm |

### 通道 channels（选填，默认单通道 1/2）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 否 | 通道名，默认 `CH{i}` |
| `rf_port` | 是 | RF 口端口号，正数，通道间不重复 |
| `if_port` | 是 | IF 口端口号，正数，通道间不重复 |
| `mixer` | 否 | 该通道混频器实例，覆盖 stages 里的共享 mixer |

### 级层 stages（必填，按信号流顺序）

| 字段 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `mixer` | 是 | - | 混频器实例名 |
| `lo_port` | 是 | - | LO 口端口号，正数 |
| `f_if` | 条件必填 | - | 跟踪本振：给 `f_if` 不给 `f_lo` |
| `f_lo` | 条件必填 | - | 固定本振：给 `f_lo` 不给 `f_if` |
| `relation` | 否 | `diff` | `diff` 差频 / `sum` 和频 |
| `injection` | 否 | 空（网表推导） | `high` / `low`，或分段数组 |
| `freq_mult` | 否 | 1 | 驱动频率 = `f_lo / freq_mult` |
| `p_lo` | 否 | 继承链路级 | 该级本振功率 dBm |

分段注入边示例：

```json
"injection": [
  {"f_rf": [6, 12], "side": "high"},
  {"f_rf": [12, 18], "side": "low"}
]
```

### 仿真控制 simulation（选填）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `max_order` | 5 | HB 阶数 |
| `step` | 0.1 | 扫频步进 GHz |
| `detune` | 0.1 | 落带临界点偏置 GHz |
| `sideband` | `SSB` | `SSB` / `DSB` |
| `pin_scan` | `[-40,-30,-25,-20,-10]` | P−1dB/Psat 功率扫描，≥3 且严格递增 |
| `if_filter` | 不启用 | 中频滤波器通带 `[lo,hi]` GHz |

### 增益挡位 control（选填，默认 `{}`）

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `front_switch` | 是 | 前端档开关实例名 |
| `throws` | 是 | Bits → 支路名映射，如 `{"01":"LNA","10":"AMP","00":"PAD"}` |
| `agc_attenuator` | 是 | 数控衰减器实例名 |
| `steps` | 否 | 档位数组，每项 `gain_db`/`front`/`agc_db` |

## 指标

9 个可请求指标：变频增益、增益平坦度、带内杂散、本振泄漏、镜像抑制、输入P−1dB、输出P−1dB、Psat、变频噪声系数。

## 输出和错误

```json
{
  "simulation_path": "D:\\...\\data\\simulate_xxx",
  "errors": [
    {"code": "UNHANDLED_ERROR", "stage": "simulation", "link": "链路名", "indicator": "变频噪声系数", "message": "..."}
  ],
  "results": [
    {
      "link": "链路名/CH1",
      "electrical_parameters": [
        {"indicator": "变频增益", "minimum": "25.19", "typical": "26.45", "maximum": "27.10", "unit": "dB", "note": ""}
      ]
    }
  ]
}
```

- `simulation_path`：本次产物目录；参数错误时为 `""`。
- `errors`：空表示成功；每项 `code`/`stage`/`link`/`indicator`/`message`，`stage` 取值 argument、parameter、simulation、result、report、runtime。
- `results`：每条链路/通道一项，`link` 为"链路名/通道名"。
- `electrical_parameters`：`indicator`/`minimum`/`typical`/`maximum`/`unit`/`note`。

单值口径只填 `minimum`，其余列空；取不到数值的指标三列都空（判定"待确认"）。判定结果不在 stdout，在落盘的 `09_指标判定表.md` 和 PDF/DOCX 里。

### 产物清单（每次运行必有，调用方必须向外传递）

结果目录在 exe 同级的 `data\simulate_<时间戳>_<随机>\`。**下面是完整产物，只此一份清单**：

| 顶层文件 | 内容 |
| --- | --- |
| `<工程名>_仿真报告.pdf` / `.docx` | 封面、简介、拓扑图、器件表、电参数表、曲线、结论 —— **正式交付物** |
| `09_指标判定表.md` | 指标 × 要求 × 合格判定（判定结论只在这里和 PDF/DOCX 里） |
| `02_结果主表.md` / `.json` | 各链路/通道的电参数结构化结果 |
| `01_错误清单.json` | 本次运行的失败项（成功时为空数组） |
| `04_留痕清单.md` | 网表 / RAW 的 md5 台账（逐工况可追溯） |
| `05_频率覆盖与临界点.md` | 频率规划与频段边界工况 |
| `08_收敛自检.md` | 谐波阶数收敛对比结论 |
| `10_PDF自检.md` | 报告自检（页数/图表是否齐全） |
| `schematic.png` | 原理图截图 |
| `simulation.log` | exe 主日志（步骤时间线 + 失败堆栈） |

子目录（明细，一般无需逐个取）：

- `<链路名>/conditions/<工况>/` —— 每工况一套：`netlist.log`、`history/result.raw`、`<指标>.csv`、`<指标>.png`、`<指标>_chart.raw`、`ads_output.log`

MCP 工具层（`run_mixer_simulation`）把产物显式暴露出来，避免调用方"不知道报告已经生成"：

- `report_artifacts`：`[{"type":"pdf"|"docx","path":"<绝对路径>","name":"..."}]`，PDF 排在 DOCX 之前
- `artifacts`：报告固定在**最前**（旧实现按路径字符串排序再截断 40 条，`conditions/` 明细会先把名额吃满，报告被静默挤掉 —— 2026-10-21 已修）
- `message`：追加"已生成仿真报告：…"；`warnings[0]` 也有同样提示

⚠️ 拿到成功结果后应**先看 `report_artifacts` 并直接交付报告**，不要再跑 `export_simulation_report` 重生成一遍 —— `Report_Exporter` 的 `indicator_catalog` 不含混频指标（变频增益/镜像抑制/本振泄漏等会被拒）。

常见错误码（按类别）：

| 类别 | code | 处理 |
| --- | --- | --- |
| 参数解析 | `ARGUMENT_ERROR`、`NO_LINKS`、`LINK_NOT_OBJECT`、`UNKNOWN_FIELD`、`MISSING_FIELD`、`INVALID_TYPE`、`DUPLICATE_FIELD` | 按链路 JSON 字段表改 |
| 链路名 | `LINK_NAME_EMPTY`、`LINK_NAME_DUPLICATED`、`LINK_NAME_INVALID` | 填合法唯一 name |
| 频率 | `FREQUENCY_RANGE_INVALID`、`F_IF_INVALID`、`BW_INVALID`、`STEP_INVALID`、`DETUNE_INVALID` | 按字段约束改（正数 GHz、min<max 等） |
| 端口 | `PORT_INVALID`、`PORTS_IDENTICAL`、`PORT_NOT_FOUND` | 按网表实际端口号改 |
| 变频形态 | `INJECTION_UNKNOWN`、`INJECTION_INVALID`、`FORM_CONFLICT`、`FORM_NOT_DECLARED` | 显式给 high/low 或分段；核对形态声明 |
| 器件 | `MIXER_BAND_MISMATCH`、`MIXER_FREQ_UNIT_MISSING`、`MIXER_FREQ_MISMATCH`、`IMT_OUT_OF_RANGE` | 换器件或核对频段；交调表外推判定待确认 |
| 控制 | `DIRECTION_INVALID`、`SIDEBAND_INVALID`、`IF_FILTER_INVALID`、`MAX_ORDER_INVALID`、`CONTROL_INVALID`、`REQUIREMENTS_INVALID`、`PIN_INVALID`、`P_LO_INVALID`、`PIN_SCAN_INVALID` | 按字段表改 |
| 结果 | `ZERO_PRODUCT`、`CONVERGENCE_FAILED`、`COVERAGE_INCOMPLETE`、`CHART_GATE_FAILED` | 查通路/端口/器件 .imt 覆盖；收敛/覆盖不足判定降级待确认 |
| 运行 | `UNHANDLED_ERROR` | 看 message + log 堆栈 + 工况 ads_output.log |

分层经验：网表实例重名/输出节点匿名可改参数重试；许可不可用、器件 DLL 与 ADS 版本不匹配不可重试；SP 变频噪声的"Frequency mapping incomplete"是模型限制，结果为空即可，不要反复重试。

⚠️ **别照 exe 的「模型 .ds 文件缺失，需补模型后重跑」去补 .ds** —— 实测是误报（模型库其实齐全），照做只白忙。判断真因请看工况目录下 `ads_output.log` 的 ADS 原文；工具层 `_collect_ads_diagnostics` 已把它提到 `details.first_error`，优先读那句。

## 注意事项

- `mixer`、`lo_port` 必填，程序不推导。
- 宽频段 + 小 `step` 会让工况数量很大（如 2–18 GHz、step 0.1 会到数千个），调用前控制频段或 `step`。
- 多通道是逐通道复制，不是双激励。
