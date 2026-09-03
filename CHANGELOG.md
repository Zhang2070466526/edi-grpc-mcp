# Changelog

## 2026-09-03

### 重构
- 错误响应统一：`tool_error` 重命名为 `error_response`，全部手写 `{"success": False, "error_code", "message"}` 字典改为 `error_response()`（~35 处），额外上下文字段统一归入 `details` 子对象
- 响应构建器归位：`submitted_response` / `queue_full_response` 从 `task_runner.py` 移到 `utils.py`，与 `error_response` 聚为「统一响应构建」section；`utils.py` 按职责重排为 6 个带标题的 section
- 新增 `servers/eda/project_reader.py`：`ProjectReader` + S-expression 解析器（`parse_sexp`/`parse_components`/`parse_paramsinfo`/`_walk_find`/`_kv`）从 `config.py` 拆出，`config.py` 回归纯配置
- 新增 `servers/eda/signal_chain.py`：`get_signal_chain` 及网表解析/追踪辅助函数从 `design_export.py` 拆出
- 诊断工具归位：`get_service_status` 从 `grpc_client.py`（内部通信层）移到 `edi_launcher.py` 与 `get_service_logs` 聚合；`grpc_client` 公开 `is_queue_busy` / `get_cached_channel` 只读诊断接口
- 新增 `call_project_grpc()` helper：消除「validate_project_path + call_grpc」三段式样板，5 个模块复用；新增 `require_nonempty` / `build_artifact` helper
- 新增 `TokenStore`（`servers/token_registry.py`）：图片/文档临时 token 存储去重（替换 image_display/document 各自近重复实现）
- 新增 `com_session()` 上下文管理器：ANSYS COM `CoInitialize`/`CoUninitialize` 去重
- 死代码清理：删除冗余 `validate_local_file`、6 处死 import（含 `settings.py` 的 `sys`、`workspace_copy.py` 的 `mcp` 等）

## 2026-09-02

### 新增
- 器件固有参数查询工具 `get_components_static_params`（gRPC `GET_COMPONENTS_STATIC_PARAMS = 21`）：按器件 UUID 查询重量/尺寸/封装/厂商/成本，用于选型后的合理性检查；proto 重新编译生成枚举 21
- 端口占用自动清理：`start_servers.py` 启动时若端口被占用，自动结束占用进程后继续启动（新增 `_find_port_pid` / `_kill_port_process`）
- 启动日志打印访问令牌值：`Auth: enabled (?token=xxx required on /mcp)`，便于客户端直接复制 token
- 抗烧毁评估工具 `simulate_anti_burnout`（gRPC `SIMULATE_ANTI_BURNOUT = 22`）：对具备抗烧毁数据的器件执行输入功率仿真和风险评估；proto 重新编译生成枚举 22
- 日志读取工具 `get_service_logs`：读取 EDI 服务端日志（`logs/eda_YYYY-MM-DD.log`）并分析异常（ERROR/WARN/异常堆栈统计，支持关键词/级别过滤，路径可由 `EDI_LOG_DIR` 配置）
- 信号链路追踪工具 `get_signal_chain`：解析网表，按「节点↔器件接力」算法追踪信号流（源→负载），返回链路器件序列/角色/分支数（v1 只做结构，不做规格）
- 工程创建工具 `create_project`（gRPC `CREATE_PROJECT = 23`）：按名称/作者/父目录创建新工程（不显示向导、不自动打开）；proto 重新编译生成枚举 23

### 修复
- 修复 8 个中危 bug：`start_simulation_async` 超时未校验、`update_simulation_component` 丢弃查找错误、`launch_edi` 成功报失败、`compare_simulation_results` CSV 解析 x/y 错位与插值未校验非参考文件单调、`close_hfss_project` 关错项目、HFSS/CST runner 缺运行超时兜底、Chat 会话淘汰误删活跃会话、`/upload` 缺大小限制
- 修复 5 个低危问题：`register_document_url` 绕过路径校验、VSWR 拆分残留连续 `&` 与丢 `ac_config`、`cst_export_snp` port_count 未校验、Chat 路径脱敏只处理反斜杠、`get_simulation_async_result` 端点 success 语义与 status 相反
- 修复 2 个防御性问题：`analyze_image` max_tokens 类型校验、`compare_simulation_results` 输出目录校验
- Chat 前端：修复鉴权启用后 fetch 不带 token 导致工具列表/聊天/上传 401 的问题；修复 session 失效后前端未同步新 `session_id` 导致多轮对话上下文断裂的问题

### 重构
- Chat 前端 UI 全面优化：整体视觉（圆角/阴影/间距）、侧栏工具分类重做、消息气泡加时间戳、顶栏 EDI 在线状态彩色圆点
- 删除 RAG 知识库模块（`servers/knowledge/`，ChromaDB + DashScope，从未注册为 MCP 工具）
- 隐藏 `copy_image_to_workspace`（暂不使用 OpenClaw，`OPENCLAW_WORKSPACE_PATH` 恒为 None，函数代码保留可恢复）

### 测试
- 新增 `tests/test_bugfixes.py`（21 个回归测试），全量 288 → 309 项

## 2026-09-01

### 新增
- MCP 访问令牌鉴权（`MCP_API_KEY`）：配置后 `/mcp` `/ui` `/chat` `/tools/list` `/upload` 要求 URL 带 `?token=` 匹配才放行，用于「只允许指定 agent 访问」；留空则不鉴权（向后兼容）
- 打包冒烟测试（`scripts/smoke_test_exe.py`）：打包完成后自动启动 exe 验证「健康检查 + 鉴权 + 工具注册」

### 其他
- 项目改名：PyPI 包名 / 命令 `edi-mcp` → `edi-grpc-mcp`，服务显示名 `EDA MCP` → `EDI gRPC MCP`（打包产物目录 `dist/edi-mcp/` 与 exe 名 `edi_mcp_server.exe` 保持不变）

## 2026-08-27

### 新增
- CST 电磁仿真模块（`servers/cst/`，5 个工具）：异步求解、S 参数导出、远场方向图导出（自动判断是否已求解）、任务查询
- 通用异步任务队列 `TaskRunner`（`servers/task_runner.py`）：支持 metadata、`require_idle` 原子提交、`run_sync` 同步执行、`max_run_seconds` 超时兜底

### 重构
- HFSS 异步队列迁移到 `TaskRunner`，删除手写 `queue.Queue` / worker 线程
- CST 两个查询工具合并为 `query_task()` 统一封装
- 提取公共函数：`is_network_path`、`IMAGE_MIME_MAP`、`SIM_COMPONENT_TYPES`、`_build_cmd`、`_task_not_found`

### 修复
- HFSS 结果校验误报（目录 mtime 启发式不可靠）
- `get_service_status` 的 `queue_locked` 恒 False
- 进程退出时非 daemon 线程阻塞
- TTL 惰性清理 / 卡死任务永不清理
- HFSS「busy 检查 + submit」的 TOCTOU 竞态
- `cst_export_snp` 绕过串行队列
- CST S 参数正则不一致导致合法项被丢弃
- CST 资源泄漏（会话 / 结果句柄 / 临时副本）
- ANSYS COM 附着静默失败
- `analyze_variables` 引用检测在循环内（跨原理图引用漏配）

### 测试
- 新增 `test_cst`、`test_task_runner`、`test_utils`、`test_settings`、`test_ansys`（220 → 283 项）
