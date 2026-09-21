# EDI gRPC MCP HTTP 接口文档

本文档说明 EDI gRPC MCP 服务暴露的全部 HTTP 路由的**请求体、响应体**,以及每个接口在**成功 / 失败各种情况**下的返回。

> 默认地址 `http://127.0.0.1:50026`。默认只监听本机；加 `--host 0.0.0.0` 可监听所有网卡（远程访问，见下节「访问控制与远程部署」）。
>
> ⚠️ **访问控制只有进程白名单，且仅作用于 `/mcp`**；远程/局域网部署另有 3 处改动 + 一个 421 坑 —— 见下节「访问控制与远程部署」。

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

## 访问控制与远程部署（先读）

**鉴权现状**：本服务**没有 token / 账号体系**（源码已无 `MCP_API_KEY`，全仓零命中），唯一接入控制是进程白名单 `MCP_ALLOWED_PROCESSES`——同机 TCP 反查来源进程，精确匹配命中放行，否则 **403**。

| 路由 | 是否受白名单保护 | 说明 |
|---|---|---|
| `POST /mcp` | ✅ **唯一受保护** | 未命中 → 403（`PROCESS_UNKNOWN` = 反查不到进程；`PROCESS_NOT_ALLOWED` = 查到但不在白名单） |
| `/health`、`/ready`、`/metrics` | ❌ | 诊断路径，放行 |
| `/ui`、`/` | ❌ | 浏览器界面，放行 |
| `GET /tools/list` | ❌ | 放行（**直接返回全量工具名 + 描述**） |
| `POST /upload`、`POST /chat` | ❌ | 放行（LLM 端点、上传端点均无鉴权） |
| `GET /images/{token}`、`GET /documents/{token}` | ❌ | 无身份校验，靠**不可猜 token**（`secrets.token_urlsafe(24)`，10 分钟有效）。⚠️ **`/documents/{token}` 是通用的"产物下载"通道**：远程取回 `.snp`/`.raw`/图/任意文件都复用它与 `_doc_store`（新增工具 `fetch_artifact`，**不校验扩展名/目录**）——**不要把 Bearer 之类的鉴权加到这个路由上**，否则主机的浏览器/`curl` 下载会失败（它们带不了自定义头）|

白名单**留空 = 不拦截**（此时挂的是 `ProcessProbeMiddleware`：只打印来源进程，并对**每条 `/mcp` 请求**执行一次 `psutil.net_connections()` 全表扫描）。

**远程 / 局域网访问（非 `127.0.0.1`）需要改 7 项，不只是 `--host`**（2026-09-18 实测）：

> ✅ **第十八轮进度（2026-09-18 落地并端到端实测）**：**①–④ 已落地** —— 现在启动时加 **`--host 0.0.0.0`** 就能远程：LAN IP / 主机名（含大小写）/ 非环回 IPv6 **自动进允许列表**，产物链接**自动按请求 `Host` 生成**（实测 LAN 调用返回 LAN 链接且 `GET` 200 字节一致）；**⑤ `fetch_artifact`、⑥⑦ 未做**。`/ready` 新增 **`bind_host`** 与 **`allowed_hosts`** 两个自述字段（远程排障先看这两个）。逐项状态见 `docs/IMPLEMENTATION.md`；已端到端实测 **31/31**。

1. ✅ **（已落地）** **host 硬编码**：`start_servers.py:205` `host = "127.0.0.1"`，`main()` 无 `--host` → 加 `--host`（默认仍 `127.0.0.1`，**本机模式零变化**）；
2. ✅ **（已落地）** ⚠️ **`/mcp` 会被 SDK 的 DNS-rebinding 校验拒**：`mcp 1.28` 的 `FastMCP.__init__` 只在 host 为环回时自动生成 `allowed_hosts`，本仓是**构造之后**才改 `mcp.settings.host`（不重算）→ 实测 `Host=192.168.0.58:PORT` → **`421 Invalid Host header`**（真客户端走 LAN IP 握手失败）。修法（实测通过）= 在 `streamable_http_app()` **之前**显式设 `mcp.settings.transport_security`，**枚举本机全部非环回 IP（psutil）＋主机名/FQDN（含小写）** ＋ `MCP_EXTRA_ALLOWED_HOSTS`。
   - ⚠️ **实测**：白名单**只列 IP** 时，用**主机名**访问会被 421（`DESKTOP-1NH03PE` → 421，`192.168.0.58` → 200），且**大小写敏感**；
   - ❌ **不要**改成 `enable_dns_rebinding_protection=False` 图省事：实测伪造 `Host: evil.example.com` 会被**放行**（200），会污染产物链接；
3. ✅ **（已落地）** **base URL 被强映射**：`get_server_base_url()` 把 `0.0.0.0`/`::` 映射成 `127.0.0.1` → `open_document`/`show_image`/`fetch_artifact` 的 token 链接会指向**客户端自己的 localhost**。修法 = 中间件把请求 `Host` 存 contextvar，`get_server_base_url()` **内部**优先用它（必须在 `servers/utils.py` 内部改：`token_registry` 是 `from ... import` 按名字绑定，外部替换会静默失效），并**校验 Host 属于本机地址集合**；无请求上下文时回落 `MCP_PUBLIC_BASE_URL`；
4. ✅ **（已落地）** **进程白名单在远程模式自动忽略**（反查不到来源进程，`--host 0.0.0.0` 下自动忽略 `MCP_ALLOWED_PROCESSES`，不再 403）→ 按上表**等于没有任何接入控制**（探针保留 `MCP_PROBE_ENABLED`，默认开，作为唯一的"谁在连"线索）；
5. ⛔ **（本期未做）** **产物通道**：新增 `fetch_artifact(file_path, ttl_seconds=600)`（只校验文件存在、**不限扩展名与目录**，复用 `_doc_store` + 既有 `/documents/{token}` 路由，返回 `url`/`size_bytes`/`sha256`/`expires_in`）——否则 `.snp`/`.raw` 这类产物**拿不回来**（`open_document` 白名单只认 10 种办公格式，实测 `UNSUPPORTED_FORMAT`）；
6. ✅ **（已落地，位置有变）** **Host 校验**：放在 `RequestBaseURLMiddleware` 里（只把「允许列表内的 Host」写进 contextvar），比放在 `get_server_base_url()` 内更省一次全网卡枚举；
7. ⛔ **（本期未做）** **`MCP_PUBLIC_BASE_URL`/`MCP_PUBLIC_SCHEME` 兜底**（反代/端口映射/https/无请求上下文）—— 不挂反代不受影响；**只有**「异步任务里注册产物链接」会回落成 `127.0.0.1`。

> ✅ **地址零配置（已落地）**：绑 `0.0.0.0` + 动态枚举本机 IP/主机名（含非环回 IPv6）+ 产物链接按请求 `Host` 推导 → 主机用什么地址连，链接就是什么地址；只有客户端用**自动枚举不到的名字**（反代域名等）时才需要往 `MCP_EXTRA_ALLOWED_HOSTS` 补一条。动态枚举含**当前已启用的**网卡（VPN 适配器 down 时自然不在列表里，起来就自动进）。

> **鉴权本期不做**（2026-09-18 用户决定：内网直接访问）→ 远程 = **无鉴权开放**；`TokenGuard` 中间件原型**已备好并实测 11/11**（`docs/IMPLEMENTATION.md`），日后想加约 25 行。

> ⚠️ `transport_security` **只作用于 `/mcp`**；上表其余路由（含 `/tools/list`、`/metrics`、`/upload`、`/chat`）**不经过** Host 校验（实测伪造 Host 也 200）——**不要在这些路由上做按 Host 判断的安全决策**。

远程机制见 **`docs/IMPLEMENTATION.md`** 三、特殊机制（远程访问 / 进程白名单 / 产物传输 / 并发排队）。

---

## 远程排障速查（现象 → 原因 → 处置）

| 现象 | 原因 | 处置 |
|---|---|---|
| 从别的机器调 `/mcp` 返回 **421 `Invalid Host header`**（**新版已消除绝大部分场景**：LAN IP、主机名含大小写、非环回 IPv6 都自动在列表里，实测 200）| 客户端用的 `Host` **不在**允许列表 —— 典型是**反代域名**或自动枚举不到的地址 | 先看 `/ready` 的 `bind_host` / `allowed_hosts`（**若 `allowed_hosts=3` 说明服务还是旧版**或没传 `--host`）；确属枚举不到的名字再补 `MCP_EXTRA_ALLOWED_HOSTS`。该 Host 校验**只作用于 `/mcp`**（其余路由不校验）|
| 调用返回 **403 `PROCESS_UNKNOWN`** | 旧版 exe 且白名单没留空（新版远程模式自动忽略白名单，不会再 403）| 用新版 exe（远程自动忽略）；旧版则 `MCP_ALLOWED_PROCESSES=` 清空 |
| 本机 `curl` 正常、**别的机器连不上** | 服务仍绑在 `127.0.0.1` | 加 `--host 0.0.0.0`（§2.1）|
| **`/ready` 里 `allowed_hosts` 恒为 `3`** | 服务是**改动前**的版本（SDK 默认只给 3 条环回模式），或启动时没传 `--host` | 用带本改动的版本重启；新版本启动会打印 `Bind: 0.0.0.0:PORT` + `Hosts: N allowed pattern(s)` + `LAN:` 行 |
| 远程已连上，但**产物链接指向 `127.0.0.1`** | 服务是旧版（链接写死）或客户端用了不在允许列表的地址（链接按设计回落）| 换成带本改动的版本；确认 `/ready` 的 `allowed_hosts` > 3 |
| 连不上但端口看着开着 | 防火墙未放行（本机三档当前为关闭状态）| 放行入站 TCP `MCP_PORT`（默认 50026）|
| 工具全失败、但服务本身正常 | EDI 客户端没在跑（ADS gRPC 50055 不通）| 看 **`GET /ready` 的 `grpc` 字段**（`online`/`offline`）—— 最直接的预检；重启机器后**记得重开 EDI** |
| **`/health`、`/ready` 每次要 ~0.5s，而 `/tools/list` 只要几毫秒** | 这两个端点各自做一次 `settimeout(0.5)` 的 EDI TCP 探测，EDI 不在时会**等满 0.5s 超时** | 属预期行为，**起 EDI 即消失**；若 0.5s 也嫌慢，可把探测超时压到 0.15–0.2s（本仓改动约 3 行）|
| 产物链接打开 **404** | token 过期（默认 **600 秒**）或文件已删/改名 | 重新调 `fetch_artifact` / `open_document(mode="link")` 注册新链接 |
| 大文件下载中断后想续传 | —— | 支持 **`Range`**（实测返回 **206**）|
| 一边跑任务一边下载/探活都卡住 | 同步工具占用了事件循环（服务被冻结）| 见 `docs/IMPLEMENTATION.md` 三、特殊机制（3.4 并发与排队）|
| 客户端报超时但服务端还在跑 | 客户端 read 超时（Hermes 默认 **300s**）比工具耗时短 | 长任务改走**异步**接口（`start_*` → 轮询）|
| 服务重启后旧 `task_id` 查不到 | 任务状态在**内存**里，重启即丢 | 返回 `TASK_NOT_FOUND` + **`outcome_known=false`**（"结果未知"，别当失败处理）；日志在 `%TEMP%\edi\data\log\` |

> 远程排障核心场景见上表；完整机制见 `docs/IMPLEMENTATION.md` 三、特殊机制。

## 1. GET /health — 健康检查

**用途**：确认服务进程存活，以及 EDI gRPC、TurboCharts 是否就绪。

**请求**：无参数。

**成功响应**（HTTP 200）：
```json
{
    "status": "ok",
    "version": "0.1.9",
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
    "version": "0.1.9",
    "grpc": "online",
    "tool_count": 90,
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
>
> ⚠️ **本服务唯一受进程白名单保护的路径就是 `/mcp`**；非环回监听时还会被 SDK 的 DNS-rebinding 校验以 `421 Invalid Host header` 拒绝 —— **该阻塞已修（第十八轮）**：非环回启动时会显式重建允许列表（本机全部 IP + 主机名大小写 + 非环回 IPv6 + `MCP_EXTRA_ALLOWED_HOSTS`）—— 见「访问控制与远程部署」节。

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
