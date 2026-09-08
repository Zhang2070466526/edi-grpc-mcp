# EDI gRPC MCP HTTP 接口文档

本文档说明 EDI gRPC MCP 服务暴露的全部 HTTP 路由的**请求体、响应体**,以及每个接口在**成功 / 失败各种情况**下的返回。

> 默认地址 `http://127.0.0.1:50026`。所有接口均只监听本机（`MCP_HOST` 强制 `127.0.0.1`）。

---

## 路由总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（进程 + gRPC + TurboCharts） |
| GET | `/ready` | 就绪检查（初始化完成返回 200，否则 503） |
| GET | `/ui` | 内置聊天界面（HTML 单页应用） |
| GET | `/tools/list` | 已注册 MCP 工具列表 |
| POST | `/chat` | 聊天（LLM 多轮工具调用闭环） |
| POST | `/upload` | 文件上传（multipart/form-data） |
| GET | `/images/{token}` | 图片访问（10 分钟临时 Token） |
| GET | `/documents/{token}` | 文档访问（10 分钟临时 Token） |
| GET | `/metrics` | 运行时指标（Prometheus 格式） |
| POST | `/mcp` | MCP 协议端点（JSON-RPC） |

---

## 1. GET /health — 健康检查

**用途**：确认服务进程存活，以及 EDI gRPC、TurboCharts 是否就绪。

**请求**：无参数。

**成功响应**（HTTP 200）：
```json
{
    "status": "ok",
    "version": "0.1.8",
    "mcp_ready": true,
    "eda_grpc_ready": true,
    "turbocharts_ready": true,
    "eda_grpc_server": "127.0.0.1:50055"
}
```

| 情况 | `status` | 说明 |
|---|---|---|
| EDI gRPC 在线 | `"ok"` | `eda_grpc_ready: true` |
| EDI gRPC 离线 | `"degraded"` | `eda_grpc_ready: false`（服务本身仍存活） |

> 该接口**始终返回 200**，通过 `status` 字段区分健康/降级，不会因 gRPC 离线而 5xx。

---

## 2. GET /ready — 就绪检查

**用途**：确认服务是否已完成初始化，供负载均衡 / 客户端判断是否可接收请求。

**请求**：无参数。

**成功响应**（HTTP 200，已就绪）：
```json
{
    "status": "ready",
    "transport": "streamable-http",
    "stateless": true,
    "version": "0.1.8",
    "grpc": "online",
    "tool_count": 55,
    "started_at": 1750000000.0
}
```

**未就绪响应**（HTTP 503，初始化中）：
```json
{
    "status": "starting",
    "message": "MCP 服务正在初始化，请稍后重试"
}
```

| 情况 | HTTP 状态码 | `status` |
|---|---|---|
| 已初始化完成 | 200 | `"ready"` |
| 正在初始化 | 503 | `"starting"` |

---

## 3. GET /ui — 聊天界面

**用途**：返回内置聊天前端页面（单页应用）。

**请求**：无参数。

**成功响应**：`Content-Type: text/html`，返回聊天界面 HTML。

**失败响应**（HTTP 404）：HTML 文件缺失时返回 `<h2>chat_client.html not found</h2>`。

---

## 4. GET /tools/list — 工具列表

**用途**：返回所有已注册 MCP 工具的名称和描述。

**请求**：无参数。

**成功响应**（HTTP 200）：
```json
[
    {"name": "list_epp_projects", "description": "扫描指定文件夹中的 .epp 工程文件。"},
    {"name": "open_edi_project", "description": "打开一个.epp 工程..."}
]
```

---

## 5. POST /chat — 聊天

**用途**：核心聊天接口，由 LLM 驱动多轮工具调用闭环。

**请求体**（`Content-Type: application/json`）：
```json
{
    "session_id": "a1b2c3d4e5f6",
    "message": "帮我扫描 C:/Projects 下的工程"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 否 | 会话 ID；传空字符串或省略则自动新建会话 |
| `message` | string | 是 | 用户消息，不能为空 |

### 请求校验失败（HTTP 400）

| 情况 | 响应 |
|---|---|
| 请求体不是合法 JSON | `{"error": "invalid JSON body"}` |
| 请求体不是对象 | `{"error": "body must be an object"}` |
| `session_id` 不是字符串 | `{"error": "session_id must be a string"}` |
| `message` 不是字符串 | `{"error": "message must be a string"}` |
| `message` 为空（去空格后） | `{"error": "message required"}` |

### 正常响应（HTTP 200）

成功或业务失败都返回 200，通过 `success` 字段区分：
```json
{
    "success": true,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "找到 3 个工程：demo1.epp, demo2.epp, test.epp",
    "activities": [
        {
            "tool": "list_epp_projects",
            "label": "扫描工程",
            "status": "success",
            "duration_ms": 123.4,
            "summary": "找到 3 个工程",
            "args": {"folder_path": "C:/Projects"},
            "result": {"success": true, "count": 3},
            "error": ""
        }
    ],
    "context": {
        "current_project_name": null,
        "simulation_task_id": null,
        "simulation_status": null,
        "simulation_ads_output_tail": "",
        "simulation_log_complete": false
    },
    "media": []
}
```

### 业务失败情况（HTTP 200，`success: false`）

以下情况都返回 HTTP 200，但 `success: false`，通过 `reply` 说明原因。完整响应结构统一为 `{success, session_id, request_id, reply, activities, context, media}`。

**① session_id 过长（>128 字符）**
```json
{
    "success": false,
    "session_id": "",
    "request_id": "8a7df01a1028",
    "reply": "session_id 过长。",
    "activities": [],
    "context": {},
    "media": []
}
```

**② 消息过长（>20000 字符）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "消息过长（最大 20000 字符）。",
    "activities": [],
    "context": {},
    "media": []
}
```

**③ 会话锁超时（上一条消息还在处理中）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "当前会话正在处理上一条请求，请稍后重试。",
    "activities": [],
    "context": {},
    "media": []
}
```

**④ LLM 未配置（缺少 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "Chat 不可用。请在 .env 中配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL。",
    "activities": [],
    "context": {},
    "media": []
}
```

**⑤ LLM 返回非 200（如模型名无效、请求格式错误）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "LLM 调用失败: 400: {\"error\":{\"message\":\"Model Not Exist\",\"type\":\"invalid_request_error\"}}",
    "activities": [],
    "context": {},
    "media": []
}
```

**⑥ 模型一次请求的工具过多（>8 个）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "模型一次请求了过多操作（9 个），已停止。",
    "activities": [],
    "context": {},
    "media": []
}
```

**⑦ 工具调用轮数超限（>5 轮）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "工具调用轮数超过上限（5 轮），请简化你的问题或补充必要信息。",
    "activities": [
        {"tool": "list_epp_projects", "label": "扫描工程", "status": "success", "duration_ms": 120.5, "summary": "找到 3 个工程", "args": {}, "result": {}, "error": ""}
    ],
    "context": {},
    "media": []
}
```

**⑧ 模型接口连接失败（网络异常、超时等）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "模型接口连接失败: ConnectError",
    "activities": [],
    "context": {},
    "media": []
}
```

**⑨ 服务内部异常（未预期的异常）**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "聊天服务内部错误，请重试。",
    "activities": [],
    "context": {},
    "media": []
}
```

### 破坏性操作确认（HTTP 200，`success: true`）

请求删除/替换/覆盖等破坏性操作时，不会立即执行，而是返回确认提示：
```json
{
    "success": true,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "⚠️ **确认操作**\n\n从工程中删除器件 R1 及其连接线。此操作无法由 MCP 自动恢复。\n\n回复 **确认** 继续，或回复 **取消** 放弃。",
    "activities": [],
    "context": {
        "current_project_name": "demo",
        "simulation_task_id": null,
        "simulation_status": null,
        "simulation_ads_output_tail": "",
        "simulation_log_complete": false
    },
    "media": []
}
```
用户回复「确认」后才真正执行。

---

## 6. POST /upload — 文件上传

**用途**：上传文件到临时目录，返回本地路径供 Chat 工具（如 `analyze_image`）使用。

**请求体**（`Content-Type: multipart/form-data`）：
- 表单字段 `file`：要上传的文件。

**成功响应**（HTTP 200）：
```json
{
    "success": true,
    "file_path": "C:/Users/xxx/AppData/Local/Temp/mcp/uploads/a1b2c3d4_report.pdf",
    "file_name": "report.pdf",
    "file_size": 12345
}
```

**失败响应**：
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| 未提供 `file` 字段 | 400 | `{"error": "no file"}` |
| 保存异常 | 500 | `{"success": false, "error": "<异常信息>"}` |

> 文件保存到 `%TEMP%/mcp/uploads/`，文件名格式为 `{8位随机}_{原文件名}`，避免重名覆盖。

---

## 7. GET /images/{token} — 图片访问

**用途**：通过临时 Token 访问本地图片（由 `show_image` / `capture_schematic` 等注册）。

**请求**：路径参数 `token`（由 `register_image_url` 生成的随机字符串，10 分钟有效）。

**成功响应**：`Content-Type: image/png` 等，返回图片二进制，`Cache-Control: private, max-age=600`。

**失败响应**：
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| Token 不存在或已过期 | 404 | `{"error": "not found or expired"}` |
| 图片文件已被删除 | 404 | `{"error": "file gone"}` |

---

## 8. GET /documents/{token} — 文档访问

**用途**：通过临时 Token 访问本地文档（由 `open_document` / `generate_simulation_report` 注册）。

**请求**：路径参数 `token`（10 分钟有效）。

**成功响应**：按文档类型返回文件；PDF 默认 `inline` 预览，DOCX 默认 `attachment` 下载。

**失败响应**：
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| Token 不存在或已过期 | 404 | `{"error": "not found or expired"}` |
| 文档文件已被删除 | 404 | `{"error": "file gone"}` |

---

## 9. POST /mcp — MCP 协议端点

**用途**：标准 MCP（Model Context Protocol）端点，遵循 MCP Streamable HTTP 传输规范。

**请求 / 响应**：JSON-RPC 2.0 格式，包括 `initialize`、`tools/list`、`tools/call`、`resources/list`、`resources/read`、`prompts/list`、`prompts/get` 等方法。

> 该端点由 FastMCP 框架处理，客户端（Claude Code、OpenClaw 等）通过 MCP SDK 接入，无需手动构造请求。详细协议见 [MCP 规范](https://modelcontextprotocol.io)。

---

## 10. GET /metrics — 运行时指标

**用途**：输出 Prometheus 格式的运行时指标，供监控系统（Prometheus / Grafana）抓取。

**请求**：无参数。

**成功响应**（HTTP 200，`Content-Type: text/plain`）：
```
# HELP edi_tool_calls_total 工具调用总次数
# TYPE edi_tool_calls_total counter
edi_tool_calls_total{tool="list_epp_projects"} 5

# HELP edi_tool_errors_total 工具调用失败次数
# TYPE edi_tool_errors_total counter
edi_tool_errors_total{tool="list_epp_projects"} 0

# HELP edi_tool_duration_ms_sum 工具调用总耗时(毫秒)
# TYPE edi_tool_duration_ms_sum counter
edi_tool_duration_ms_sum{tool="list_epp_projects"} 1234

# HELP edi_sim_tasks 当前异步仿真任务数
# TYPE edi_sim_tasks gauge
edi_sim_tasks 1

# HELP edi_uptime_seconds 服务运行时长(秒)
# TYPE edi_uptime_seconds gauge
edi_uptime_seconds 3600
```

**指标说明**：
| 指标 | 类型 | 含义 |
|---|---|---|
| `edi_tool_calls_total` | counter | 各工具调用总次数（label `tool`） |
| `edi_tool_errors_total` | counter | 各工具调用失败次数 |
| `edi_tool_duration_ms_sum` | counter | 各工具调用总耗时（毫秒） |
| `edi_sim_tasks` | gauge | 当前异步仿真任务数 |
| `edi_uptime_seconds` | gauge | 服务运行时长（秒） |

---

## 附：统一返回字段说明（gRPC 工具）

所有 gRPC 工具的返回（经 `/mcp` 的 `tools/call`）统一包含以下字段，语义见 [TOOLS_API.md](./TOOLS_API.md) 的「gRPC 工具统一返回格式」：

```json
{
    "success": true,
    "completed": true,
    "outcome_known": true,
    "task_success": true,
    "status": "SUCCEEDED",
    "message": "...",
    "project_path": "...",
    "result_path": "...",
    "ads_output": "...",
    "log_complete": true,
    "details": {}
}
```
