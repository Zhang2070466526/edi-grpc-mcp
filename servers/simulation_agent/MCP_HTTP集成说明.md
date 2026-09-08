# SimulationAgent HTTP 集成接口

本文档供现有 MCP 服务的开发人员使用。

## 1. 连接信息

默认地址：

```text
http://127.0.0.1:17866
```

集成接口仅监听 `127.0.0.1`，供同一台机器上的受信任 MCP 服务访问，不进行 HTTP
鉴权，也不需要 Authorization Header。启动参数 `--edi-mms-api-token` 只由 EXE 内部
访问 EDI MMS 器件查询服务使用，MCP 不需要读取、保存或传递该 Token。

## 2. 建议启动流程

1. 启动或等待 `SimulationAgent.exe`。
2. MCP 服务请求 `GET /api/v1/integration/health`。
3. 未成功时短暂退避重试；成功后读取 manifest、workflow 和 tools。
4. MCP 根据 tools 返回的 JSON Schema 注册或更新自己的工具映射。
5. 把 workflow 内容作为通用 Agent 的工作流指令、MCP prompt/resource，或高层工具说明。

## 3. 通用响应约定

立即执行和后台执行都返回 operation 对象：

```json
{
  "operation_id": "op_4b29...",
  "session_id": "43a59bca4e12",
  "request_id": "mcp-call-0001",
  "tool": "tr_find_paths",
  "status": "completed",
  "success": true,
  "created_at": "2026-09-07T18:20:00+08:00",
  "started_at": "2026-09-07T18:20:00+08:00",
  "finished_at": "2026-09-07T18:20:01+08:00",
  "result": {},
  "error": ""
}
```

operation 状态：

| 状态 | 含义 |
| --- | --- |
| `queued` | 已接收，等待后台任务开始 |
| `running` | 工具正在执行 |
| `completed` | 工具正常返回，读取 `result` |
| `failed` | 调用异常，读取 `error` 和 `error_type` |
| `interrupted` | EXE 停止期间 operation 被中断 |

注意：`operation.success=true` 表示 HTTP 工具包装正常完成。部分业务工具会正常返回
`result.success=false`，MCP 必须继续检查内部业务结果。

## 4. 接口列表

### 4.1 健康检查

```http
GET /api/v1/integration/health
```

```json
{
  "success": true,
  "status": "ready",
  "service": "SimulationAgent",
  "api_version": "1.0",
  "skill_version": "1.0",
  "tool_count": 18
}
```

### 4.2 服务清单

```http
GET /api/v1/integration/manifest
```

返回 API 版本、Skill 版本、鉴权来源和各接口相对地址。

### 4.3 工作流说明

```http
GET /api/v1/integration/workflow
```

响应类型为 `text/markdown`。MCP 应把这段文字提供给真正负责工具编排的通用 Agent。
仅由 MCP 后端读取但不注入 Agent 上下文，不会使模型自动遵守仿真流程。

### 4.4 工具发现

```http
GET /api/v1/integration/tools
```

每个工具包含：

```json
{
  "name": "tr_find_paths",
  "description": "...",
  "input_schema": {},
  "execution_mode": "immediate",
  "requires_user_confirmation": false
}
```

- `input_schema` 直接取自 SimulationAgent 当前 FunctionTool，避免参数契约漂移。
- `execution_mode=background` 表示默认返回 HTTP 202，需轮询 operation。
- `requires_user_confirmation=true` 表示调用请求必须提供 confirmation。
- `tr_open_document` 通过 HTTP 开放，用于用户明确要求在本机打开报告时调用。

### 4.5 创建或登记会话

```http
POST /api/v1/integration/sessions
Content-Type: application/json

{}
```

响应：

```json
{
  "success": true,
  "session_id": "43a59bca4e12"
}
```

也可以登记调用方生成的合法 ID：

```json
{
  "session_id": "mcp_project_001"
}
```

合法字符为字母、数字、下划线和短横线，长度 1～80。同一仿真工作流必须始终复用同一
`session_id`。

### 4.6 调用工具

```http
POST /api/v1/integration/tools/{tool_name}/invoke
Content-Type: application/json
```

请求：

```json
{
  "session_id": "43a59bca4e12",
  "request_id": "mcp-call-0001",
  "arguments": {
    "epp_path": "D:\\EDI\\projects\\demo\\demo.epp"
  }
}
```

`request_id` 最长 128 字符。未提供时由服务生成，但 MCP 正式接入应始终提供稳定 ID。
同一 EXE 生命周期内，重复提交相同 `session_id + request_id + tool + arguments` 会返回原
operation，不会重复执行。相同 request_id 被用于不同工具或参数时返回 HTTP 409。

工具默认按发现接口中的 execution_mode 执行。调用方可以显式传：

```json
{
  "background": true
}
```

不建议把 ADS 仿真类工具强制改成同步模式。

### 4.7 查询后台任务

```http
GET /api/v1/integration/operations/{operation_id}
```

当状态为 `queued` 或 `running` 时继续退避轮询。当状态进入 `completed`、`failed` 或
`interrupted` 时停止。

operation 状态只属于当前 EXE 生命周期，重启后不能继续通过旧 operation_id 查询；但
仿真计划、修订、结果和报告仍保存在会话 `workflow_state.json` 中。重启恢复时重新调用
`tr_get_workflow_state`。

## 5. 后台工具

下列工具默认以 operation 后台执行：

- `tr_execute_simulation_plan`
- `tr_run_simulation`
- `tr_parse_raw`
- `tr_generate_document`
- `tr_sync_project_components`
- `tr_restore_schematic`

其他工具默认等待执行完成后直接返回 operation。

当前 EDI gRPC 协议没有为单次 HTTP tool operation 提供可靠的中途取消，因此集成 API
没有提供取消端点。调用方超时后应继续使用原 operation_id 或相同 request_id 查询，不能
更换 request_id 重新执行。

## 6. 修改工程的工具

以下工具除正常参数外，还必须在 HTTP 外层提供用户确认信息：

- `tr_sync_project_components`
- `tr_restore_schematic`

请求示例：

```json
{
  "session_id": "43a59bca4e12",
  "request_id": "sync-001",
  "arguments": {
    "epp_path": "D:\\EDI\\projects\\demo\\demo.epp",
    "operations": []
  },
  "confirmation": {
    "confirmed_by_user": true,
    "confirmation_id": "user-confirm-20260907-001"
  }
}
```

缺少 confirmation、`confirmed_by_user` 不是 true 或 confirmation_id 为空时返回 HTTP 409。

## 7. HTTP 状态码

| 状态码 | 含义 |
| --- | --- |
| `200` | 查询成功或立即工具执行结束 |
| `201` | 会话已创建/登记 |
| `202` | 后台 operation 已接收 |
| `400` | JSON、session_id、request_id 或参数外层格式错误 |
| `404` | 会话、工具或 operation 不存在 |
| `409` | 幂等冲突或缺少用户确认 |
| `422` | 工具执行抛出异常 |

工具参数本身不符合 JSON Schema 时，operation 会进入 `failed`，错误信息位于 `error`。

## 8. MCP 映射建议

MCP 服务可以一对一映射 HTTP 工具：

```text
MCP tool name        = HTTP tools[].name
MCP description      = HTTP tools[].description
MCP inputSchema      = HTTP tools[].input_schema
MCP tool handler     = POST /api/v1/integration/tools/{name}/invoke
```

MCP handler 应在内部自动补充而不暴露给模型：

- `session_id`
- `request_id`
- operation 轮询逻辑

需要让用户看到和控制的业务参数继续保留在 MCP tool input 中。

## 9. 最小调用示例

PowerShell：

```powershell
$session = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:17866/api/v1/integration/sessions" `
  -ContentType "application/json" `
  -Body "{}"

$body = @{
  session_id = $session.session_id
  request_id = "capabilities-001"
  arguments = @{ indicator = "" }
} | ConvertTo-Json -Depth 10

$result = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:17866/api/v1/integration/tools/tr_get_simulation_capabilities/invoke" `
  -ContentType "application/json" `
  -Body $body
```

Python：

```python
import time
import uuid
import requests

BASE_URL = "http://127.0.0.1:17866/api/v1/integration"

session = requests.post(f"{BASE_URL}/sessions", json={}).json()

response = requests.post(
    f"{BASE_URL}/tools/tr_get_simulation_capabilities/invoke",
    json={
        "session_id": session["session_id"],
        "request_id": str(uuid.uuid4()),
        "arguments": {"indicator": ""},
    },
)
operation = response.json()

while operation["status"] in {"queued", "running"}:
    time.sleep(1)
    operation = requests.get(
        f"{BASE_URL}/operations/{operation['operation_id']}",
    ).json()

if operation["status"] != "completed":
    raise RuntimeError(operation["error"])

tool_result = operation["result"]
print(tool_result)
```

## 10. 工作流要求

MCP 开发人员还必须读取：

```http
GET /api/v1/integration/workflow
```

工具调用顺序、参数确认、失败纠错、报告事实保护、原理图同步和回退规则以该接口返回的
版本化 Markdown 为准。
