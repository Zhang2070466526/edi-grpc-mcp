# MCP 服务访问控制方案（进程白名单）

> 目标：让本机 MCP 服务**只被一个指定 agent（如 Hermes）使用**，其他 agent 一律拒绝。

---

## 1. 背景与需求

MCP 服务（`start_servers.py`）跑在本机，通过 Streamable HTTP（`127.0.0.1:50026`）对外提供工具。当前有多个 agent 软件可能同时存在（Claude Code、Hermes、OpenClaw 等），需求是：

- **只放行一个指定 agent 进程**；
- 其他 agent 进程即便知道服务地址，也无法使用。

场景特征：**同机、同一 OS 用户、多个本地进程**。

---

## 2. 候选方案对比

| 方案 | 原理 | 是否可行 | 原因 |
|---|---|---|---|
| 共享 token（URL / header） | 密钥匹配 | ❌ | 密钥写在配置文件里，同用户其他进程能读 |
| `clientInfo.name` 白名单 | 校验客户端自报名称 | ❌ | 名称是客户端自报，可被伪造 |
| 反向代理（`mcp_auth_proxy.py`） | 代理持私钥、开放转发 | ❌ | 代理是「开放转发器」，谁连 5026 都能被带进真实服务 |
| **进程白名单（本方案）** | 校验「连接是哪个进程发起的」 | ✅ | OS 的 TCP 表记录 owning PID，客户端无法伪造 |
| OS 用户隔离 | 不同用户 + 文件权限 | ✅（更彻底） | 运维成本高，需给每个 agent 分用户 |

**核心结论**：同机 + 同用户下，「文件里的秘密」（token、私钥）和「客户端自报的身份」（clientInfo）都不可靠；真正能防住的只有 OS 层身份 —— 要么「连接来源进程」，要么「不同 OS 用户」。

---

## 3. 方案原理

当一个进程发起 TCP 连接时，是它自己调用了 `connect(127.0.0.1:50026)`。**操作系统会把这条连接记进 TCP 连接表，并标注它归哪个 PID 所有**（即 `netstat -ano` 里最后一列 PID）。

关键点：**PID 是 OS 记的，不是客户端自报的**。另一个进程（如 `openclaw.exe`）发起连接，OS 记的是 openclaw 的 PID；它无法让这条连接「看起来」是 Hermes 发起的。

反查流程：

```
请求进来 → 拿到来源端口(request.client) → 查 TCP 表反查 PID → 查 PID 的 exe 路径 → 白名单比对 → 放行/403
```

---

## 4. 详细设计

### 4.1 新增模块 `servers/process_guard.py`

核心函数与中间件：

```python
def find_client_process(client_port: int, server_port: int) -> tuple[str, str]:
    """反查来源端口对应的进程 (exe 完整路径, 命令行)；查不到返回 ('', '')。"""
    import psutil
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_ESTABLISHED:
            continue
        if not conn.laddr or not conn.raddr:
            continue
        # 方向：连接归客户端进程所有，raddr 是服务端，laddr 是客户端
        if conn.raddr.port == server_port and conn.laddr.port == client_port:
            try:
                p = psutil.Process(conn.pid)
                return p.exe() or "", " ".join(p.cmdline() or []) or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return "", ""
    return "", ""
```

中间件 `ProcessWhitelistMiddleware`（Starlette `BaseHTTPMiddleware`）：

- 白名单为空 → 不拦截（功能关闭）；
- 反查不到来源进程 → 默认**拒绝**（`fail_open=False`，宁可错杀不放过）；
- **匹配规则**：白名单每个条目作为「子串」，在 `exe路径 + 空格 + 命令行` 里查找，命中任一即放行；未命中 → `403`。

> 为什么要命令行匹配：实测 Hermes 连 MCP 的来源是它的 gateway-service，即
> `python.exe -m hermes_cli.main gateway run`（通用 python 跑专属模块），
> 光靠 exe 路径（python.exe）无法精确区分，需匹配命令行关键词 `hermes_cli`。

### 4.2 配置项 `servers/settings.py`

```python
# 进程白名单：只放行匹配这些子串的进程访问 /mcp（逗号分隔，可填 exe 路径或命令行关键词如 hermes_cli）；留空禁用
mcp_allowed_processes: str = ""
```

环境变量：`MCP_ALLOWED_PROCESSES`。示例：`MCP_ALLOWED_PROCESSES=hermes_cli`。

### 4.3 接线 `start_servers.py`

在 `_run_http_server` 里，`starlette_app = mcp.streamable_http_app()` 之后：

```python
if _cfg.mcp_allowed_processes:
    allowed = {p.strip() for p in _cfg.mcp_allowed_processes.split(",") if p.strip()}
    starlette_app.add_middleware(ProcessWhitelistMiddleware,
                                 server_port=port, allowed=allowed)
```

### 4.4 关键实现细节

- **归一化**：比较时统一「小写 + 正斜杠」，避免 Windows 路径分隔符/大小写差异。
- **缓存**：「端口 → exe」映射，TTL 60s，避免 keep-alive 连接复用同一端口时每次扫全表。
- **白名单填法**：优先填命令行关键词（如 `hermes_cli`，精确锁定 Hermes 的 gateway 进程）；也可填完整 exe 路径（如 `C:\...\Hermes.exe`）或进程名。关键词越具体越难误伤。

---

## 5. 落地步骤（两阶段）

### 阶段 1：探针（先确认 Hermes 的真实进程）

临时挂一个 `ProcessProbeMiddleware`，只打印不拦截：

```
[process-probe] /mcp <- 127.0.0.1:51951 exe=C:\...\hermes.exe
```

你启动服务 → Hermes 连一次 → 看打印的 `exe=`，确认：
1. Hermes 实际是哪个 exe（完整路径）；
2. 它有没有 spawn 子进程来连（若有，白名单要填那个子进程）。

### 阶段 2：白名单拦截

1. `.env` 设 `MCP_ALLOWED_PROCESSES=C:\...\hermes.exe`；
2. 重启服务，上拦截；
3. 验证：Hermes 正常用；再用 `python -c` 发个 POST 模拟别的进程，应返回 403。

---

## 6. 边界与局限

| 边界 | 处理 / 说明 |
|---|---|
| 查不到来源进程（psutil 失败、进程已退） | 默认拒绝；临时调试可设 `fail_open=True` |
| Hermes spawn 子进程连接 | 白名单填实际连接的那个子进程 exe |
| 走反向代理（5026） | 来源进程变成代理，不是 Hermes；要校验需在代理侧也做进程校验，或去掉代理直连 |
| 有人把恶意程序改名为 `hermes.exe` 且放到同路径 | 理论上可绕过，但门槛远高于读 token；对「区分不同 agent 软件」够用 |

**不可防的（诚实说明）**：同机同用户下，进程名/路径理论上能被「改名 + 放同路径」绕过。若需 100% 隔离，仍需 OS 用户隔离（Hermes 独立用户 + 文件权限）。

---

## 7. 测试计划

新增 `tests/test_process_guard.py`：

- mock `psutil.net_connections` 返回一条 `(raddr.port=50026, laddr.port=12345, pid=xxx)`，断言 `find_client_exe` 反查到正确 exe；
- 中间件：白名单命中 → 放行；不命中 → 403；白名单空 → 不拦截；查不到进程 + `fail_open=False` → 403。

---

## 8. 附录：依赖与兼容性

- 依赖：`psutil`（已在 `pyproject.toml` 依赖中）。
- 平台：Windows（`psutil.net_connections` 走 `GetExtendedTcpTable`，可拿到 owning PID）；Linux/macOS 也可用（读 `/proc/net/tcp` / `sysctl`）。
