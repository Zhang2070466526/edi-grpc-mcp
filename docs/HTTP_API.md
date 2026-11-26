# EDI gRPC MCP HTTP 接口文档

本文档说明 EDI gRPC MCP 服务暴露的全部 HTTP 路由的**请求体、响应体**,以及每个接口在**成功 / 失败各种情况**下的返回。

> 默认地址 `http://127.0.0.1:50026`。默认只监听本机;加 `--host 0.0.0.0`(或设 `MCP_BIND_HOST=0.0.0.0`)可监听所有网卡(远程访问,见下节「访问控制与远程部署」)。
>
> ⚠️ **访问控制只有进程白名单,且仅作用于 `/mcp`**;远程/局域网部署另有若干坑 —— 见下节「访问控制与远程部署」。

---

## 路由总览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查(进程 + gRPC + TurboCharts + 独立仿真 exe) |
| GET | `/ready` | 就绪检查(初始化完成返回 200,否则 503) |
| GET | `/` | 与 `/ui` 相同,聊天界面 |
| GET | `/ui` | 内置聊天界面(HTML 单页应用) |
| GET | `/tools` | 工具浏览器页面(按引擎/领域分组,点击看用法) |
| GET | `/tools/list` | 已注册 MCP 工具名称 + 描述列表 |
| GET | `/tools/grouped` | 按「引擎 → 领域」分组的工具 JSON(含 usage/只读/破坏性标注) |
| POST | `/chat` | 聊天(LLM 多轮工具调用闭环) |
| POST | `/upload` | 文件上传(multipart/form-data,上限 100 MB) |
| GET | `/images/{token}` | 图片访问(token = urlencode 路径,**永久有效**) |
| GET | `/documents/{token}` | 文档/产物下载(token = urlencode 路径,**永久有效**) |
| GET | `/metrics` | 运行时指标(Prometheus 格式) |
| POST | `/mcp` | MCP 协议端点(JSON-RPC) |

---

## 访问控制与远程部署(先读)

**鉴权现状**:本服务**没有 token / 账号体系**,唯一接入控制是进程白名单 `MCP_ALLOWED_PROCESSES` —— 同机 TCP 反查来源进程,精确匹配命中放行,否则 **403**。默认值 `hermes_cli.main,edi-agent-service.exe`(写死在 settings.py);**显式置空 = 不拦截**(此时挂 `ProcessProbeMiddleware`:只打印来源进程,并对每条 `/mcp` 请求做一次 `psutil.net_connections()` 全表扫描)。

| 路由 | 是否受白名单保护 | 说明 |
|---|---|---|
| `POST /mcp` | ✅ **唯一受保护** | 未命中 → 403(响应体中性的 `{"error":"forbidden"}`,不暴露白名单/来源进程;审计信息只进服务端日志) |
| `/health`、`/ready`、`/metrics` | ❌ | 诊断路径,放行 |
| `/ui`、`/`、`/tools`、`/tools/list`、`/tools/grouped` | ❌ | 浏览器界面/工具浏览,放行(**直接返回全量工具名 + 描述**) |
| `POST /upload`、`POST /chat` | ❌ | 放行(LLM 端点、上传端点均无鉴权) |
| `GET /images/{token}`、`GET /documents/{token}` | ❌ | 无身份校验,token 是 **urlencode 的绝对路径**(确定性、可猜测)。⚠️ **`/documents/{token}` 是通用的「产物下载」通道**:远程取回 `.snp`/`.raw`/图/任意文件都复用它与 `_doc_store`(工具 `fetch_artifact`,**不校验扩展名/目录**)。**不要把 Bearer 之类的鉴权加到这个路由上**,否则主机的浏览器/`curl` 下载会失败(它们带不了自定义头) |

**远程 / 局域网访问(非 `127.0.0.1`)**:启动时加 **`--host 0.0.0.0`**(或设 `MCP_BIND_HOST=0.0.0.0`)即可,其余自动处理:

1. **Host 允许列表自动重建**:SDK 的 DNS-rebinding 校验默认只放行环回,非环回访问会被 `421 Invalid Host header` 拒绝。本服务在构造 app 前显式重建允许列表 = **本机全部网卡 IP + 主机名/FQDN(含大小写)+ 非环回 IPv6 + `MCP_EXTRA_ALLOWED_HOSTS`**。⚠️ **不关闭** `enable_dns_rebinding_protection`(关掉后伪造 `Host: evil.example.com` 会被放行,污染产物链接)。
   - 自动枚举覆盖不到的地址(典型:**反代域名**)才需要往 `MCP_EXTRA_ALLOWED_HOSTS` 补一条(逗号分隔)。
2. **进程白名单在远程模式自动忽略**:远程客户端反查不到来源进程,若仍启用会把所有请求拦成 403。`--host 0.0.0.0` 下自动忽略 `MCP_ALLOWED_PROCESSES` → 按上表**等于没有任何接入控制**(探针 `MCP_PROBE_ENABLED` 默认开,作为唯一的「谁在连」线索)。
3. **产物链接按请求 Host 生成**:`RequestBaseURLMiddleware` 把「允许列表内的 Host」写进 contextvar,`get_server_base_url()` 内部优先用它 → 主机用什么地址连,链接就是什么地址。
4. ⚠️ **异步任务里的产物链接仍指向 `127.0.0.1`**:异步任务线程池不继承请求上下文,`get_server_base_url()` 回落到启动时确定的地址(`0.0.0.0`/`::` 映射回 `127.0.0.1`)。**没有 `MCP_PUBLIC_BASE_URL` 之类的兜底**(未实现)——反代/端口映射场景下,异步任务注册的链接会失效。

> ⚠️ `transport_security`(Host 校验)**只作用于 `/mcp`**;上表其余路由(含 `/tools/list`、`/metrics`、`/upload`、`/chat`、`/images/*`、`/documents/*`)**不经过** Host 校验(伪造 Host 也 200)——**不要在这些路由上做按 Host 判断的安全决策**。
>
> **鉴权本期不做**(内网直接访问)→ 远程 = **无鉴权开放**。

远程机制详见 **`docs/IMPLEMENTATION.md`** 三、特殊机制(远程访问 / 进程白名单 / 产物传输 / 并发排队)。

---

## 远程排障速查(现象 → 原因 → 处置)

| 现象 | 原因 | 处置 |
|---|---|---|
| 从别的机器调 `/mcp` 返回 **421 `Invalid Host header`** | 客户端用的 `Host` **不在**允许列表 —— 典型是**反代域名**或自动枚举不到的地址 | 先看 `/ready` 的 `bind_host` / `allowed_hosts`(**若 `allowed_hosts=3` 说明还是环回模式**或没传 `--host`);确属枚举不到的名字再补 `MCP_EXTRA_ALLOWED_HOSTS`。该 Host 校验**只作用于 `/mcp`** |
| 调用返回 **403** | 白名单没命中(仅 `/mcp`;远程模式自动忽略白名单,不会再 403) | 远程用 `--host 0.0.0.0`(自动忽略);本机则 `MCP_ALLOWED_PROCESSES=` 清空或补进程名 |
| 本机 `curl` 正常、**别的机器连不上** | 服务仍绑在 `127.0.0.1` | 加 `--host 0.0.0.0`(或 `MCP_BIND_HOST=0.0.0.0`) |
| **`/ready` 里 `allowed_hosts` 恒为 `3`** | 环回模式(SDK 默认只给 3 条),或没传 `--host` | 带 `--host 0.0.0.0` 重启;启动日志会打印 `Bind: 0.0.0.0:PORT` + `Hosts: N allowed pattern(s)` |
| 远程已连上,但**产物链接指向 `127.0.0.1`** | 客户端用了不在允许列表的地址(链接按设计回落),或**异步任务**里注册的链接(无请求上下文) | 确认 `/ready` 的 `allowed_hosts` > 3;异步任务产物用 `fetch_artifact` 重新注册 |
| 连不上但端口看着开着 | 防火墙未放行 | 放行入站 TCP `MCP_PORT`(默认 50026) |
| 工具全失败、但服务本身正常 | EDI 客户端没在跑(ADS gRPC 50055 不通) | 看 **`GET /ready` 的 `grpc` 字段**(`online`/`offline`)—— 最直接的预检;重启机器后**记得重开 EDI** |
| **`/health`、`/ready` 每次要 ~0.15s,而 `/tools/list` 只要几毫秒** | 这两个端点各自做一次 `settimeout(0.15)` 的 EDI TCP 探测,EDI 不在时会**等满超时** | 属预期行为,**起 EDI 即消失** |
| 产物链接打开 **404** | 文件已删/改名(token 是路径本身,路径不变就永久有效;**没有过期**一说) | 重新调 `fetch_artifact` / `open_document(mode="link")` 注册新链接 |
| 大文件下载中断后想续传 | —— | 支持 **`Range`**(实测返回 **206**) |
| 一边跑任务一边下载/探活都卡住 | 同步工具占用了事件循环(服务被冻结) | 见 `docs/IMPLEMENTATION.md` 三、特殊机制(3.4 并发与排队) |
| 客户端报超时但服务端还在跑 | 客户端 read 超时比工具耗时短 | 长任务改走**异步**接口(`start_*` → 轮询) |
| 服务重启后旧 `task_id` 查不到 | 任务状态在**内存**里,重启即丢 | 返回 `TASK_NOT_FOUND` + **`outcome_known=false`**("结果未知",别当失败处理);日志在 `%TEMP%\edi\data\log\` |

> 远程排障核心场景见上表;完整机制见 `docs/IMPLEMENTATION.md` 三、特殊机制。

## 1. GET /health — 健康检查

**用途**:确认服务进程存活,以及 EDI gRPC、TurboCharts、独立仿真 exe 是否就绪。

**请求**:无参数。

**成功响应**(HTTP 200):
```json
{
    "status": "ok",
    "version": "0.1.13",
    "mcp_ready": true,
    "eda_grpc_ready": true,
    "turbocharts_ready": true,
    "simulation_tool_ready": true,
    "eda_grpc_server": "127.0.0.1:50055",
    "tool_count": 101,
    "tools_hash": "a1b2c3d4"
}
```

| 字段 | 说明 |
|---|---|
| `status` | `"ok"`(gRPC 在线)/ `"degraded"`(gRPC 离线,服务本身仍存活) |
| `eda_grpc_ready` | EDI gRPC 端口是否连通 |
| `turbocharts_ready` | TurboCharts 可执行文件是否存在 |
| `simulation_tool_ready` | 独立仿真 exe(Report_Exporter + TR_Simulator)是否都存在 |
| `tool_count` / `tools_hash` | 已注册工具数 / 工具名排序后 md5 前 8 位 |

> 该接口**始终返回 200**,通过 `status` 字段区分健康/降级,不会因 gRPC 离线而 5xx。

---

## 2. GET /ready — 就绪检查

**用途**:确认服务是否已完成初始化,供负载均衡 / 客户端判断是否可接收请求。

**请求**:无参数。

**成功响应**(HTTP 200,已就绪):
```json
{
    "status": "ready",
    "transport": "streamable-http",
    "stateless": true,
    "version": "0.1.13",
    "grpc": "online",
    "tool_count": 101,
    "tools_hash": "a1b2c3d4",
    "started_at": 1750000000.0,
    "bind_host": "127.0.0.1",
    "allowed_hosts": 3
}
```

| 字段 | 说明 |
|---|---|
| `grpc` | EDI gRPC 端口连通性:`"online"` / `"offline"` |
| `tool_count` / `tools_hash` | 工具数 + 工具集指纹(远程排障:两端工具集是否一致) |
| `bind_host` | 实际监听地址(`127.0.0.1` = 仅本机;`0.0.0.0` = 远程) |
| `allowed_hosts` | 生效的 Host 允许条目数(**环回模式恒为 3**;远程模式 > 3,报 421 时先看这个) |

**未就绪响应**(HTTP 503,初始化中):
```json
{
    "status": "starting",
    "message": "MCP 服务正在初始化,请稍后重试"
}
```

| 情况 | HTTP 状态码 | `status` |
|---|---|---|
| 已初始化完成 | 200 | `"ready"` |
| 正在初始化 | 503 | `"starting"` |

---

## 3. GET /ui(及 /)— 聊天界面

**用途**:返回内置聊天前端页面(单页应用)。`/` 与 `/ui` 等价。

**请求**:无参数。

**成功响应**:`Content-Type: text/html`,返回聊天界面 HTML。

**失败响应**(HTTP 404):HTML 文件缺失时返回 `<h2>chat_client.html not found</h2>`。

---

## 4. GET /tools/list — 工具列表

**用途**:返回所有已注册 MCP 工具的名称和描述。

**请求**:无参数。

**成功响应**(HTTP 200):
```json
[
    {"name": "list_epp_projects", "description": "扫描指定文件夹中的 .epp 工程文件。"},
    {"name": "open_edi_project", "description": "打开一个.epp 工程..."}
]
```

---

## 4.1 GET /tools — 工具浏览器页面

**用途**:返回工具浏览器前端页面(按引擎/领域分组,点击看用法)。

**请求**:无参数。

**成功响应**:`Content-Type: text/html`,返回 `tool_browser.html`;缺失时 404 返回 `<h2>tool_browser.html not found</h2>`。

---

## 4.2 GET /tools/grouped — 分组工具 JSON

**用途**:返回按「引擎 → 领域」两级分组的工具,每个工具附带完整 usage(原 docstring)与只读/破坏性标注。给工具浏览器页面(或需要分类视图的客户端)用。

**请求**:无参数。

**成功响应**(HTTP 200):
```json
{
    "total": 101,
    "groups": [
        {
            "group": "EDI · gRPC",
            "domains": [
                {
                    "domain": "工程管理",
                    "tools": [
                        {
                            "name": "create_project",
                            "description": "创建新的 EDI 工程...",
                            "usage": "完整 docstring...",
                            "readonly": false,
                            "destructive": false
                        }
                    ]
                }
            ]
        }
    ]
}
```

> 一级分组顺序固定:`EDI · gRPC → 独立仿真工具 → ANSYS HFSS → CST → LASPCEM → 本地纯计算 → 图表/图片/文档 → 自描述`;匹配不到的工具归入「其他」。

---

## 5. POST /chat — 聊天

**用途**:核心聊天接口,由 LLM 驱动多轮工具调用闭环。

**请求体**(`Content-Type: application/json`):
```json
{
    "session_id": "a1b2c3d4e5f6",
    "message": "帮我扫描 C:/Projects 下的工程"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `session_id` | string | 否 | 会话 ID;传空字符串或省略则自动新建会话 |
| `message` | string | 是 | 用户消息,不能为空 |

### 请求校验失败(HTTP 400)

| 情况 | 响应 |
|---|---|
| 请求体不是合法 JSON | `{"error": "invalid JSON body"}` |
| 请求体不是对象 | `{"error": "body must be an object"}` |
| `session_id` 不是字符串 | `{"error": "session_id must be a string"}` |
| `message` 不是字符串 | `{"error": "message must be a string"}` |
| `message` 为空(去空格后) | `{"error": "message required"}` |

### 正常响应(HTTP 200)

成功或业务失败都返回 200,通过 `success` 字段区分:
```json
{
    "success": true,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "找到 3 个工程:demo1.epp, demo2.epp, test.epp",
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

### 业务失败情况(HTTP 200,`success: false`)

以下情况都返回 HTTP 200,但 `success: false`,通过 `reply` 说明原因。完整响应结构统一为 `{success, session_id, request_id, reply, activities, context, media}`。

**① session_id 过长(>128 字符)**
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

**② 消息过长(>20000 字符)**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "消息过长(最大 20000 字符)。",
    "activities": [],
    "context": {},
    "media": []
}
```

**③ 会话锁超时(上一条消息还在处理中)**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "当前会话正在处理上一条请求,请稍后重试。",
    "activities": [],
    "context": {},
    "media": []
}
```

**④ LLM 未配置(缺少 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL)**
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

**⑤ LLM 返回非 200(如模型名无效、请求格式错误)**
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

**⑥ 模型一次请求的工具过多(>8 个)**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "模型一次请求了过多操作(9 个),已停止。",
    "activities": [],
    "context": {},
    "media": []
}
```

**⑦ 工具调用轮数超限(>5 轮)**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "工具调用轮数超过上限(5 轮),请简化你的问题或补充必要信息。",
    "activities": [
        {"tool": "list_epp_projects", "label": "扫描工程", "status": "success", "duration_ms": 120.5, "summary": "找到 3 个工程", "args": {}, "result": {}, "error": ""}
    ],
    "context": {},
    "media": []
}
```

**⑧ 模型接口连接失败(网络异常、超时等)**
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

**⑨ 服务内部异常(未预期的异常)**
```json
{
    "success": false,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "聊天服务内部错误,请重试。",
    "activities": [],
    "context": {},
    "media": []
}
```

### 破坏性操作确认(HTTP 200,`success: true`)

请求删除/替换/覆盖等破坏性操作时,不会立即执行,而是返回确认提示:
```json
{
    "success": true,
    "session_id": "a1b2c3d4e5f6",
    "request_id": "8a7df01a1028",
    "reply": "⚠️ **确认操作**\n\n从工程中删除器件 R1 及其连接线。此操作无法由 MCP 自动恢复。\n\n回复 **确认** 继续,或回复 **取消** 放弃。",
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

**用途**:上传文件到临时目录,返回本地路径供 Chat 工具(如 `analyze_image`)使用。

**请求体**(`Content-Type: multipart/form-data`):
- 表单字段 `file`:要上传的文件。

**成功响应**(HTTP 200):
```json
{
    "success": true,
    "file_path": "C:/Users/xxx/AppData/Local/Temp/mcp/uploads/a1b2c3d4_report.pdf",
    "file_name": "report.pdf",
    "file_size": 12345
}
```

**失败响应**:
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| 未提供 `file` 字段 | 400 | `{"error": "no file"}` |
| 文件超过 100 MB | 413 | `{"error": "file too large"}` |
| 保存异常 | 500 | `{"success": false, "error": "<异常信息>"}` |

> 文件保存到 `%TEMP%/mcp/uploads/`,文件名格式为 `{8位随机hex}_{原文件名stem}{扩展名}`,避免重名覆盖;单文件上限 **100 MB**。

---

## 7. GET /images/{token} — 图片访问

**用途**:通过 Token 访问本地图片(由 `show_image` / `capture_schematic` 等注册)。

**Token 机制**:token 就是 **urlencode 的绝对路径**(如 `C%3A%5Cfoo%5Cbar.png`),由路径确定性生成,**无状态、不签名、不过期** —— 路径不变就永久有效,文件移动/改名后自然失效(404)。`Cache-Control: max-age=600` 只是浏览器缓存提示,**不是 token 过期时间**。

**请求**:路径参数 `token`。

**成功响应**:`Content-Type: image/*`,返回图片二进制;响应头含 `Cache-Control: private, max-age=600` 与 `X-Content-Type-Options: nosniff`。

**失败响应**:
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| Token 无法解回有效路径 | 404 | `{"error": "not found or expired"}` |
| 图片文件已被删除/改名 | 404 | `{"error": "file gone"}` |

---

## 8. GET /documents/{token} — 文档/产物访问

**用途**:通过 Token 访问本地文档(由 `open_document` 注册)或任意产物文件(由 `fetch_artifact` 注册)。

**Token 机制**:同 `/images/{token}` —— urlencode 的绝对路径,**无状态、不签名、不过期**。

**请求**:路径参数 `token`。

**成功响应**:按文件类型返回;PDF 默认 `inline` 预览,DOCX 默认 `attachment` 下载,其余按 `mimetypes` 推断。

**失败响应**:
| 情况 | HTTP 状态码 | 响应 |
|---|---|---|
| Token 无法解回有效路径 | 404 | `{"error": "not found or expired"}` |
| 文件已被删除/改名 | 404 | `{"error": "file gone"}` |

---

## 9. POST /mcp — MCP 协议端点

**用途**:标准 MCP(Model Context Protocol)端点,遵循 MCP Streamable HTTP 传输规范。

**请求 / 响应**:JSON-RPC 2.0 格式,包括 `initialize`、`tools/list`、`tools/call`、`resources/list`、`resources/read`、`prompts/list`、`prompts/get` 等方法。

> 该端点由 FastMCP 框架处理,客户端(Claude Code、OpenClaw 等)通过 MCP SDK 接入,无需手动构造请求。详细协议见 [MCP 规范](https://modelcontextprotocol.io)。
>
> ⚠️ **本服务唯一受进程白名单保护的路径就是 `/mcp`**;非环回监听时还会被 SDK 的 DNS-rebinding 校验以 `421 Invalid Host header` 拒绝 —— **该阻塞已修**:非环回启动时会显式重建允许列表(本机全部 IP + 主机名大小写 + 非环回 IPv6 + `MCP_EXTRA_ALLOWED_HOSTS`)—— 见「访问控制与远程部署」节。

---

## 10. GET /metrics — 运行时指标

**用途**:输出 Prometheus 格式的运行时指标,供监控系统(Prometheus / Grafana)抓取。

**请求**:无参数。

**成功响应**(HTTP 200,`Content-Type: text/plain`):
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

**指标说明**:
| 指标 | 类型 | 含义 |
|---|---|---|
| `edi_tool_calls_total` | counter | 各工具调用总次数(label `tool`) |
| `edi_tool_errors_total` | counter | 各工具调用失败次数 |
| `edi_tool_duration_ms_sum` | counter | 各工具调用总耗时(毫秒) |
| `edi_sim_tasks` | gauge | 当前异步仿真任务数 |
| `edi_uptime_seconds` | gauge | 服务运行时长(秒) |

---

## 附:统一返回字段说明(gRPC 工具)

所有 gRPC 工具的返回(经 `/mcp` 的 `tools/call`)统一包含以下字段,语义见 [TOOLS_API.md](./TOOLS_API.md) 的「gRPC 工具统一返回格式」:

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