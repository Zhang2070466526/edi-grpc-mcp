# 独立报告导出工具

接收调用方提供的报告 JSON 和输出目录，用固定逻辑生成 `document.pdf`、`document.docx`、`report.json`。普通调用的标准输出只有一行 UTF-8 JSON，成功包含最终绝对路径，失败包含错误。程序不使用 AI，不依赖 Agent 会话，不要求 RPC 或 ADS。

## 调用

```powershell
# 推荐 JSON 文件方式，避免 Windows 引号和命令行长度限制。
./dist/Report_Exporter.exe --json-file ./example_report.json --output 'D:/reports/run001'

# 内联 JSON 方式；PowerShell 7.3+ 支持现代原生参数转义。
./dist/Report_Exporter.exe --json '{"model_name":"TR测试","description":"测试报告","links":[],"conclusion":"暂无仿真数据"}' --output 'D:/reports/run002'

# 可选：传入原理图图片，工具不会向 EDI 请求截图。
./dist/Report_Exporter.exe --json-file ./example_report.json --output 'D:/reports/run003' --schematic 'D:/images/schematic.png'
```

Python 入口使用 `python Report_Exporter.py`，参数相同。旧版 Windows PowerShell 的内联 JSON 引号处理可能不同，请优先使用 `--json-file`；从其他程序调用时用参数数组传递完整 JSON 字符串。

| 参数 | 说明 |
| --- | --- |
| `--json` | 内联 JSON 对象；与 `--json-file` 必须且只能选择一个 |
| `--json-file` | UTF-8 / UTF-8 BOM JSON 文件 |
| `--output` | 必填输出目录，可以包含中文和空格；自动创建 |
| `--schematic` | 可选原理图图片；覆盖报告中的 `schematic_path` |
| `--help` | 显示文本帮助并返回 0，属于单行 JSON 输出约定的例外 |

JSON 文件内的图片相对路径以 JSON 所在目录为基准；内联 JSON 以进程当前目录为基准。`--schematic` 也按该规则解析。输出目录相对路径始终以当前工作目录为基准。

## 报告 JSON

完整无曲线示例见 [example_report.json](example_report.json)。以下示例展示需要曲线的仿真指标：

```json
{
  "model_name": "NC35352ME-812",
  "description": "调用方填写产品简介。",
  "schematic_path": "images/schematic.png",
  "components": [{"type":"放大器","model":"示例型号","manufacturer":"厂家","specs":"8-12GHz"}],
  "links": [{
    "name": "发射链路1",
    "electrical_parameters": [
      {"indicator":"工作频率","minimum":8,"typical":"","maximum":12,"unit":"GHz"},
      {"indicator":"输出增益","minimum":39.5,"typical":"","maximum":40.8,"unit":"dB","requirement":"≥20dB","result":"通过"}
    ]
  }],
  "charts": [{"link":"发射链路1","indicator":"输出增益","image_path":"images/gain.png"}],
  "conclusion": "调用方填写总结结论。"
}
```

`model_name` 为封面型号，省略时按 `epp_path` 文件名、`title` 或默认“TR链路”确定。`description`、`conclusion` 可以为空；工具不会补写。`schematic_path`、`components`、`charts` 可省略。器件表字段为 `type/model/manufacturer/specs`。数值允许有限数字或数字字符串，空值用 `""` 或 `null`；布尔值、NaN、Infinity 被拒绝。平坦度典型值兼容 `±0.6`。

每条链路必须有唯一非空 `name`；`electrical_parameters` 中指标不能重复，单位必须与 `indicator_catalog.py` 一致，必须包含 `minimum/typical/maximum`，至少一个非空数值。有 `requirement` 时 `result` 必须为 `通过/失败/未知`；无要求时判定应为空。工具不计算判定。

凡需要 RAW 仿真的电参数都需要对应曲线，`charts` 用 `link/indicator/image_path` 标识，支持多链路。`工作频率/移相步进/衰减步进` 为直接参数，不需要曲线；`移相态/衰减态` 只能放曲线，不能进入电参数表。图片必须存在且可解码，支持 PNG/JPEG。单链路兼容旧版 `chart_paths`，此时指标取图片父目录名。

可选 `requested_indicators`、`successful_indicators` 沿用 Agent 路由语义：指定成功指标时，电参数必须属于成功指标，成功的 RAW 指标必须有图。未成功指标应从电参数中移除；调用方可以保存 `failed_indicators` 到 JSON 快照，当前版式不单列失败指标章节。没有 Agent 简介后缀、`agent_enriched` 或会话事实校验要求。

## 输出与错误

```json
{"success":true,"report_dir":"D:\\reports\\run001","pdf_path":"D:\\reports\\run001\\document.pdf","docx_path":"D:\\reports\\run001\\document.docx","report_data_path":"D:\\reports\\run001\\report.json","schematic_path":"","chart_count":0,"errors":[]}
```

成功退出码 `0`，失败退出码 `1`。失败时 `success=false`，结果路径为空，`errors` 包含 `code/stage/message`；参数错误为 `ARGUMENT_ERROR`，校验、图片或渲染错误为 `REPORT_EXPORT_FAILED`。已存在的三个目标文件任何一个都会阻止导出，请每次选择新目录。中途失败清理本次临时文件，避免留下半份报告。

报告保留既有版式：A4、中文宋体/英文 Times New Roman、封面、简介、拓扑图、器件表、电参数表、双栏典型曲线、结论。PDF 需要 Windows 字体 `C:/Windows/Fonts/simsun.ttc`，缺失会反馈错误；DOCX 在查看机器上使用相应字体。数据快照中的曲线和拓扑路径会规范成绝对路径，图片嵌入 PDF/DOCX。

## 打包

```powershell
cd D:/SimulationReportGenerator/Report_Exporter
../.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm Report_Exporter.spec
```

EXE 是控制台单文件版，位置 `dist/Report_Exporter.exe`，无需 Python，不请求管理员权限。源代码依赖见 `requirements.txt`。报告工具与 `SimulationAgent` 无运行时耦合，相关校验与排版源码已复制到本目录。
