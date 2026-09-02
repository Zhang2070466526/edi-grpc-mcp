# Changelog

## 0.1.8（2026-09-01）

### 新增
- MCP 访问令牌鉴权（`MCP_API_KEY`）：配置后 `/mcp` `/ui` `/chat` `/tools/list` `/upload` 要求 URL 带 `?token=` 匹配才放行，用于「只允许指定 agent 访问」；留空则不鉴权（向后兼容）

## 0.1.7（2026-08-27）

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
