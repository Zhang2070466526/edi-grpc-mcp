# TR 纯硬编码仿真工具

从 EDI 工程取得网表，完成路径分析、开关状态修改、参数注入、ADS 仿真和结果提取。流程完全使用固定代码，不调用 AI，不生成报告，不需要 API Key。普通调用 stdout 只有一行 UTF-8 JSON，包括错误情况。

## 前置条件

Windows；EDI 已运行，gRPC 服务 `127.0.0.1:50055` 可用；EPP 文件及同目录 `schematics/main/schematic.ep` 存在；ADS 安装和许可证可用；EDI 安装目录含 `turbocharts_app.exe`。工具输出目录和 EDI 的 simulation 目录可写。源代码需要 Python 3.10+ 和本目录 requirements.txt，EXE 不需要 Python。

## 调用

```powershell
./dist/TR_Simulator.exe --epp 'D:/EDI/EDI-Workspace/projects/NC35352ME-812-ideal_alternative/NC35352ME-812-ideal_alternative.epp' --edi 'D:/EDI' --ads 'C:/Program Files/Keysight/ADS2020' --indicators '输出增益,噪声系数' --threads 2 --json '[{"name":"发射链路1","min_freq":8,"max_freq":12,"input_port":1,"output_port":2}]'
```

Python 入口为 `python TR_Simulator.py`，参数相同。PowerShell 7.3+ 支持现代原生参数转义；旧版 Windows PowerShell 需要按其原生参数规则转义内联 JSON。其他程序调用推荐用参数数组传递完整 JSON 字符串。

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| --epp | 是 | 无 | EPP 工程文件 |
| --edi | 是 | 无 | EDI 安装目录 |
| --ads | 是 | 无 | ADS 安装目录 |
| --indicators | 否 | 工作频率,输出增益,噪声系数 | 逗号分隔指标 |
| --threads | 否 | 5 | 正整数，指标并发线程数 |
| --json | 否 | [] | 链路数组，实际运行至少一条链路 |
| --help | 否 | 无 | 文本帮助，退出码 0，是单行 JSON 约定的例外 |

`--components` 和链路 `requirements` 已移除，传入会返回错误。参数不接受缩写。工作频率始终加入输出。

## 链路 JSON

```json
[{"name":"发射链路1","min_freq":8,"max_freq":12,"input_port":1,"output_port":2,"phase_step":5.625,"atten_step":0.5,"phase_devices":["NC12246C_812PD1"],"atten_devices":["NC1342C_812PD1"],"freq_rx":10,"pwr_rx":-20,"freq_pout":10,"pwr_pout":-20}]
```

| 字段 | 默认值 | 单位与约束 |
| --- | --- | --- |
| name | 无 | 唯一非空字符串，合法 Windows 目录名 |
| min_freq/max_freq | 0 / 100 | GHz，有限数值，min < max |
| input_port/output_port | 1 / 2 | 正整数，不同且必须在工程中连通 |
| phase_step | 5.0 | 度，有限正数 |
| atten_step | 0.5 | dB，有限正数 |
| phase_devices/atten_devices | [] | 实例名数组，扫描指标需指定，必须在路径上且类型正确 |
| freq_rx | 频段中点四舍五入 | GHz，杂散/压缩/饱和指标使用，须在频段内 |
| pwr_rx | -20 | dBm，大信号输入功率 |
| freq_pout | 频段中点四舍五入 | GHz，输出功率使用，须在频段内 |
| pwr_pout | -20 | dBm，输出功率输入功率 |

数值必须是 JSON 数字，端口必须是 JSON 整数。字符串数字、布尔值、NaN、Infinity 均被拒绝，不接受表外字段。多链路依次运行，各链路内指标按线程数并发。

## 指标

| 类型 | 支持指标 |
| --- | --- |
| 直接参数 | 工作频率、移相步进、衰减步进 |
| S 参数 | 输出增益、反向增益、增益平坦度、噪声系数、输入回波损耗、输出回波损耗、输入驻波、输出驻波 |
| 移相扫描 | 移相误差RMS、移相幅度波动、移相态、延时步进 |
| 衰减扫描 | 衰减误差RMS、衰减附加相移、衰减态 |
| HB | 杂散抑制、输出功率 |
| XDB | OP1dB、IP1dB、Psat |

移相态/衰减态只生成曲线和 CSV，不进入数值电参数。回波损耗保留既有 DB_S 数值定义。直接参数不启动 ADS，其他指标按固定配置注入 SP、扫描、HB 或 XDB 控制器。

## 输出和错误

```json
{"simulation_path":"D:\\SimulationReportGenerator\\TR_Simulator\\dist\\data\\simulate_xxx","errors":[],"results":[{"link":"发射链路1","electrical_parameters":[{"indicator":"工作频率","minimum":"8","typical":"","maximum":"12","unit":"GHz"},{"indicator":"输出增益","minimum":"39.5","typical":"","maximum":"40.8","unit":"dB"}]}]}
```

成功退出码 0，任何错误退出码 1。数值沿用解析器字符串格式，空值为 `""`，平坦度典型值例如 `±0.6`。错误字段固定为 code/stage/link/indicator/message。参数解析失败时 simulation_path 为空；初始化后失败返回本次目录。参数/工程失败通常 results=[]；部分指标失败仍返回已提取数据和失败指标错误，调用方必须检查 errors。空数值记录 RESULT_VALUE_MISSING。

常见错误：ARGUMENT_ERROR、LINK_JSON_INVALID、NO_LINKS、UNKNOWN_LINK_FIELD、UNKNOWN_INDICATOR、PATH_NOT_FOUND、PORT_NOT_FOUND、SIGNAL_PATH_NOT_FOUND、DEVICE_NOT_ON_PATH、RAW_NOT_FOUND、SIMULATION_EXECUTION_FAILED、RESULT_VALUE_MISSING。RPC 不可用或失败会反馈网表/RAW 获取错误，不输出 Python traceback。

## 文件和架构

源代码运行写入 TR_Simulator/data/simulate_<时间戳>_<UUID>/，EXE 写入 EXE 旁的 data/。每次使用独立目录，底层 stdout/stderr 收入 simulation.log。各链路保存修改开关后的 netlist.log，各指标目录保存注入参数后的网表、result.raw、CSV 和 PNG。保留 PNG/CSV 供数值提取和独立报告工具使用；不生成 PDF/DOCX、简介、结论和判定，不截取报告原理图。

临时 ADS 网表位于 EDI 安装目录 simulation/<UUID>，结果复制后删除临时目录。未生成 RAW 或后处理未生成 CSV/PNG 均返回错误，不复用旧结果。

TR_Simulator.py 处理 CLI 和输出；epp_simulator.py 编排；ecclient.py 与 ecserver_pb2*.py 连接 RPC；netlist_path_finder.py 分析路径/Bits；simulation_diagnostics.py 校验参数和路径；simulation_modifier.py 注入指标参数；simulation_results.py 与 utils.py 读取规格；simulation_log.py 记录日志；debug_simulator.py 提供固定调试输入；TR_PATHFINDER.py 保留独立路径分析入口。

## 打包

```powershell
cd D:/SimulationReportGenerator/TR_Simulator
../.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm TR_Simulator.spec
```

单文件控制台 EXE 位于 dist/TR_Simulator.exe，无需 Python，不请求管理员权限，打包排除 AI 和报告库。报告工具见 [Report_Exporter](../Report_Exporter/README.md)。
