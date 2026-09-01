# 部署指南 — 打包产物使用 & 智能体对接

> 面向最终用户：如何部署打包好的 `edi-mcp.zip`，以及配置 AI 客户端接入 MCP 服务。
> PyPI：<https://pypi.org/project/edi-mcp/>

---

## 目录结构

解压 `edi-mcp.zip` 到任意目录（以 `C:\EDI` 为例）：

```
C:\EDI\
  EDI.exe                    ← EDI 客户端（自动检测）
  turbocharts_app.exe        ← TurboCharts 图表工具（可选）
  edi-mcp\
    edi_mcp_server.exe       ← MCP 服务主程序（双击运行，无黑框）
    _internal\               ← 依赖库（不要手动修改）
    .env                     ← 配置文件
    start_server.bat         ← 备用手动启动脚本
```

> EDI 和 turbocharts 放在 `edi-mcp/` 同级目录即可，服务自动检测。
> 检测顺序：`EDI.exe` → `EDA-PMDS.exe` → `CAIS.exe`

---

## 配置 .env（可以忽略）

编辑 `edi-mcp\.env`：

```ini
EDA_GRPC_SERVER=127.0.0.1:50055     # gRPC 地址（通常不改）
EDI_PATH=                            # 留空自动检测
TURBOCHARTS_PATH=                    # 留空自动检测
MCP_TRANSPORT=streamable-http         # streamable-http（Web）或 stdio（本地）
MCP_PORT=50026                       # 监听端口
MCP_HOST=127.0.0.1                   # 监听地址
MCP_API_KEY=                          # 可选：访问令牌，配置后 /mcp 等端点要求 ?token= 匹配（只允许指定 agent 访问）
OPENCLAW_WORKSPACE=                             # 留空自动检测 rfclaw/openclaw-service/state/workspace
LLM_API_KEY=sk-xxx                   # 可选：聊天 AI 功能
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
# 可选：图片视觉分析（配置三项后自动开启）
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
# 可选：仿真报告渲染（默认 127.0.0.1:17867）
REPORT_RENDER_URL=http://127.0.0.1:17867/api/v1/reports/render
```

> 留空的字段走自动检测，一般只需确认 `EDA_GRPC_SERVER` 正确。

---

## 启动 & 验证

| 方式 | 操作 |
|---|---|
| 双击运行 | 双击 `edi_mcp_server.exe`（无黑框，后台运行） |
| 命令行 | `cd C:\EDI\edi-mcp && .\edi_mcp_server.exe` |

验证服务状态：

```
http://127.0.0.1:50026/health
```

正常返回：

```json
{"status":"ok","mcp_ready":true,"eda_grpc_ready":true}
```

检查日志：

```
%TEMP%\edi\data\log\edi_mcp_YYYYMM.log
```

---

## 智能体配置

### Claude Code（stdio）

```json
{
  "mcpServers": {
    "eda": {
      "command": "C:/EDI/edi-mcp/edi_mcp_server.exe",
      "args": ["--transport", "stdio"],
      "env": { "EDA_GRPC_SERVER": "127.0.0.1:50055" }
    }
  }
}
```

### OpenClaw

配置文件路径：`%USERPROFILE%\.openclaw\workspace\config\mcporter.json`（如 `C:\Users\JGL\.openclaw\workspace\config`）

```json
{
  "mcpServers": {
    "eda-mcp": {
      "baseUrl": "http://127.0.0.1:50026/mcp"
    }
  }
}
```

### 其他客户端

| 配置项 | 值 |
|---|---|
| 传输方式 | Streamable HTTP |
| URL | `http://127.0.0.1:50026/mcp` |

### 访问控制（可选）

在 `.env` 配置 `MCP_API_KEY=xxx` 后，`/mcp` `/ui` `/chat` 等端点只接受带正确 `?token=xxx` 的请求，其余返回 401——用于「只允许指定 agent 访问」。

例如只允许 Hermes Agent 使用，把它的 url 改成：

```yaml
mcp_servers:
  edi-mcp-sse-50026:
    url: http://127.0.0.1:50026/mcp?token=xxx
```

而 OpenClaw 等不带 token 的客户端连 `http://127.0.0.1:50026/mcp` 会被拒绝（401）。

---

## 验证客户端连接

在聊天界面或 MCP 客户端中直接说：

```
当前你检测到了那些mcp服务，这个mcp服务有那些工具
```

客户端会列出已连接的 MCP 服务，如果出现 `eda-mcp` 就代表连接成功。

---


## OpenClaw 图片显示

`show_image` 始终可用，返回 MCP ImageContent。`analyze_image` 调用视觉模型分析图片（配置 VISION_API_KEY 后自动开启，仅用户明确要求时使用，会上传到第三方）。`copy_image_to_workspace` 仅在 `OPENCLAW_WORKSPACE` 有效时注册。

> 不需要修改 `openclaw.json`，不需要 TOOLS.md，不需要 `[embed]`。

---

## 常见问题

**双击 exe 没反应？**

这是正常现象（无黑框后台运行）。访问 `http://127.0.0.1:50026/health` 确认。如果打不开，命令行运行看错误日志。

**端口被占用？**

MCP 服务默认监听 50026，如果端口已被上一个未退出的进程占用会导致启动失败。

1. 查找占用端口的进程：
```powershell
netstat -ano | findstr 50026
```
输出示例：`TCP  127.0.0.1:50026  0.0.0.0:0  LISTENING  12345`

2. 最后一列 `12345` 就是 PID，强制结束：
```powershell
taskkill -f -pid 12345
```

3. 如果是 EXE 打包版，也可以直接按进程名杀：
```powershell
taskkill -f -im edi_mcp_server.exe
```

**gRPC 未就绪？**

确认 EDI 已启动且 50055 在监听。检查方法同上：
```powershell
netstat -ano | findstr 50055
```
没有 LISTENING 状态说明 EDI 未启动或 gRPC 服务未开启。

**图片显示"不在聊天中显示"？**

检查 `.env` 中 `OPENCLAW_WORKSPACE` 是否正确，或确认工作区在 edi-mcp 同级目录（自动检测 rfclaw/openclaw-service/state/workspace）。

**图片显示白屏或 Outside allowed folders？**

不要使用 `[embed]` 或直接发送本地路径。显示图片用 `show_image`，分析图片内容用 `analyze_image`。

**如何停止服务？**

```powershell
taskkill -f -im edi_mcp_server.exe
```
