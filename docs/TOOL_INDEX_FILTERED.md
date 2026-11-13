# EDI gRPC MCP 对外工具清单（过滤内部工具后）

> 自动生成，勿手改。MCP_HIDE_INTERNAL_TOOLS=1 时对外可见；权威数量见 /ready 的 tool_count。

共 48 个工具：

- `analyze_image` — 调用视觉模型分析本地图片内容，返回结构化文字结果 —— ⚠️ 结果可能编造（实测把电路原理图认成加法器），只作粗看、不得作为工程结论。
- `analyze_mixer_spur` — 混频杂散分析：枚举 m×源频 ± n×LO 的杂散组合，判断是否落入目标输出频带。
- `analyze_variables` — 分析工程中的变量定义和引用关系。
- `build_laspcem_report` — 给已求解的 .sem 工程建 S 参数网络报告（没求解就返回 SOLVE_REQUIRED）。
- `calculate_rf_dynamic_range` — 计算射频链路的灵敏度(S)与压缩受限动态范围(DR)。
- `calculate_transline` — 计算传输线的特性阻抗或线宽（微带 / 带状 / 接地共面波导 / 同轴）。
- `close_hfss_project` — ⚠️ 关闭 AEDT 项目（未保存改动会丢；force 会强杀进程）。
- `cst_export_farfield` — 导出 CST 模型的远场方向图为 ASCII .txt（自动判断是否已求解）。
- `cst_export_farfield_query` — 查询远场导出任务：返回进度，完成时附带 farfield_txts。
- `cst_export_snp` — 同步导出 CST 的 S 参数为 .sNp（阻塞等完成后返回文件路径，适合小模型）。长任务请用 cst_export_snp_async。
- `cst_export_snp_async` — 异步提交 CST 的 S 参数导出，立即返回 task_id，用 cst_export_snp_query 取结果。想一步到位用同步版 cst_export_snp。
- `cst_export_snp_query` — 查询 CST 的 S 参数导出任务进度（配合 cst_export_snp_async 的 task_id）。求解任务用 cst_solve_query。
- `cst_get_model_info` — 只读查看 CST 模型状态：是否已解、是否解包、端口数、远场监视器有无。
- `cst_list_results` — 列出 CST 模型结果树里的全部条目（S 参数 / 远场等）与监视器。
- `cst_open_project` — 打开并保存 CST 模型（= 解包归档文件），不求解。
- `cst_plot_farfield` — 把 CST 远场 ASCII .txt 画成方向图（极坐标切面 + 直角曲线各一张 PNG）。
- `cst_solve_async` — 异步求解 CST 模型（.cst），立即返回 task_id。
- `cst_solve_query` — 查询 CST 求解任务进度（配合 cst_solve_async 拿到的 task_id）。S 参数导出任务用 cst_export_snp_query。
- `cst_task_cancel` — ⚠️ 取消一个 CST 异步任务（会终止运行中的求解）。
- `cst_task_list` — 列出当前所有 CST 异步任务（求解 / 导出）及状态。
- `describe_tool` — 查工具参数/示例，或按能力关键词找工具（留空 name=列全部）；先查它，别猜也别读源码。
- `design_wilkinson` — 设计多级威尔金森功分器，输出各级微带线宽、线长和隔离电阻。
- `export_laspcem_snp` — 导出 .sem 工程的 S 参数为 Touchstone 文件（.sNp 会按端口数落成 .s<N>p）。
- `fetch_artifact` — 把本机产物文件注册成可下载 URL（仿真结果 / SNP / RAW / 图 / 报告 / 任意文件）。
- `filter_order_bandpass` — 带通阶数估算：通带 f_p1~f_p2、阻带在两侧，返回阶数与传输零点。低通/高通/带阻分别用 filter_order_lowpass/highpass/bandstop。
- `filter_order_bandstop` — 带阻阶数估算：阻带是 f_s1~f_s2 这一段，返回阶数与陷波零点。低通用 filter_order_lowpass、高通用 filter_order_highpass。
- `filter_order_highpass` — 高通阶数估算：通带自 f_p 向上、阻带在 f_p 之下，返回阶数与传输零点。低通用 filter_order_lowpass、带阻用 filter_order_bandstop。
- `filter_order_lowpass` — 低通阶数估算：通带到 f_p、阻带在 f_p 之上，返回阶数与传输零点。高通用 filter_order_highpass、带阻用 filter_order_bandstop。
- `get_hfss_analysis_status` — 查询 HFSS 异步仿真状态（默认只读本地，不访问 AEDT）。
- `get_hfss_project_info` — 查询当前 AEDT 打开的项目和活动设计（纯查询，不启动 AEDT）。
- `get_laspcem_setup` — 读取 .sem 工程当前配置的求解设置参数（cem.EditSolution 那套键，约 64 个）。
- `get_project_summary` — 获取 .epp 工程完整概览：元数据、原理图、元件统计、仿真配置、最近结果。
- `get_simulation_component_schema` — 查询已建模器件类型的参数 Schema 和权限。
- `import_aedt_as_sem` — 把 ANSYS HFSS `.aedt` 导入 LASPCEM 并生成 `.sem` 工程（等价于 GUI 的 Import）。
- `laspcem_solve_query` — 查询 LASPCEM 求解任务（配合 run_laspcem_solve_async 的 task_id）。
- `laspcem_task_cancel` — 取消 LASPCEM 求解任务（求解/导出/建报告 任一阶段都能取消）。
- `laspcem_task_list` — 列出 LASPCEM 后台任务（含 operation=solve、phase、steps、状态与耗时）。
- `launch_aedt` — 启动 AEDT（不打开项目）。已运行时仅返回状态。
- `launch_laspcem` — 启动 LASPCEM 电磁仿真软件。已运行时返回状态，不重复启动。
- `list_epp_projects` — 扫描指定文件夹中的 .epp 工程文件。
- `list_simulation_components` — 列出已保存工程中的全部器件及参数（含 wire→public 参数名映射）。
- `open_document` — 打开本地文档：link 模式生成 10 分钟 HTTP 链接，local 模式用系统默认程序打开。
- `open_hfss_project` — 启动 AEDT 并打开 .aedt 项目（COM 附着优先，subprocess 单次启动兜底）。
- `open_laspcem_project` — 在 LASPCEM 中打开 .sem 工程文件。
- `run_laspcem_solve_async` — 提交 LASPCEM 求解任务（异步，立即返回 task_id）
- `show_image` — 读取本地图片，返回标准 MCP ImageContent 和本地路径。
- `start_hfss_analysis_async` — 异步启动 HFSS Setup 仿真，立即返回 task_id。
- `wilkinson_marchand_order` — 估计 Wilkinson 功分器 / Marchand 巴伦的节数（阶数）。
