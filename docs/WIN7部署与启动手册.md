# EDI gRPC MCP —— Win7 部署与启动

> **本文档是 Win7 相关的唯一权威文档**（2026-09-14 合并整理）。
> 已并入原《WIN7 部署方案》全部有效内容，并删除《WIN7 降级可行性评估》（其结论已收进 §2）。
>
> **两条路径，按需选一条**：
> - **路径 A（推荐）**：拿到的是**已经打包好的** `dist\edi-mcp\` → 直接部署启动，见 **§3**。目标机**不需要** Python / pip / venv / 联网。
> - **路径 B**：要在 Win7 上**从源码跑/重新打包** → 先建环境（§4）→ 打 SDK 补丁（§5）→ 打包（§6）。
>
> **实测基线**：开发机 74 PE / 86.6 MB；Win7 真机 115 PE / 87.5 MB；两者体检均 0 红线；`/ready` → `tool_count=90`、`tools_hash=ae6cd062`。

---

## 0. 一分钟版（照抄）

**路径 A（部署已打包产物）**

1. 目标机装两个补丁：**KB4474419**（SHA-2 签名）→ **KB3118401**（UCRT）——**顺序不能反**
2. 把整个 `edi-mcp\` 目录拷到目标机，例如 `C:\edi-mcp\`（**整个目录，不是只拷 exe**）
3. 双击 `start_server.bat`
4. 浏览器打开 `http://127.0.0.1:50026/ready` → 看到 `"status":"ready"` 就算成功

**路径 B（从源码建环境并打包）**

```cmd
cd /d C:\edi-grpc-mcp
scripts\win7\setup_win7_env.bat
.venv-win7\Scripts\python.exe -m pip install pyinstaller==6.22.3
.venv-win7\Scripts\python.exe scripts\win7\build_win7_exe.py --smoke
```

---

## 1. 适用范围与约束

| 项 | 值 |
|---|---|
| 目标系统 | Windows **7 SP1**（32/64 位均可，全程位数要一致；本项目按 **x64** 交付） |
| 部署形态 | 服务与 `EDI.exe` **必须同机**；**不允许用 VM 绕开** |
| 服务端口 | MCP/HTTP **50026**；EDI gRPC **50055** |
| 交付形态 | PyInstaller **目录模式**（`edi_mcp_server.exe` + `_internal\` + `.env` + `start_server.bat`） |
| 明确不做 | 不自研/替换 mcp 传输层、不改 SDK 架构、不做 14 个依赖的大规模降级 |

---

## 1.5 仓库与环境布局（一仓双 venv）

**同一个仓库里并存两套环境**，代码只有一份（源码与 SDK 版本无关；唯一的补丁打在 venv 里的 mcp SDK 上，见 §5）：

| 目的 | 命令（仓库根目录） | 环境 |
|---|---|---|
| 跑**最新**服务 | `.venv\Scripts\python.exe start_servers.py` | Python 3.12 + mcp 1.28.1（开发栈） |
| 跑 **Win7 等价**服务（源码） | `.venv-win7\Scripts\python.exe start_servers.py` | Python 3.10 + 冻结栈（§7） |
| 给 Win7 **打包交付** | `.venv-win7\Scripts\python.exe scripts\win7\build_win7_exe.py --smoke --dist dist-win7/edi-mcp` | 同上 + PyInstaller 6.22.3 |
| 跑测试 | 两套都能跑：`.venv\Scripts\python.exe -m pytest tests -q` / `.venv-win7\Scripts\python.exe -m pytest tests -q` | —— |

**三条环境纪律**：

1. **`--dist` 分开产物**：开发机的 3.12 构建默认输出 `dist/edi-mcp`，Win7 交付构建用 `--dist dist-win7/edi-mcp`。混在一起会分不清哪个能上 Win7 —— 判据：`_internal\python312.dll` = 3.12 打的（**Win7 必崩**）；`python310.dll` + 74/115 个 PE = 3.10 冻结栈打的（可用）。
2. **绝不跨机拷** `.venv` / `.venv-win7`：`pyvenv.cfg` 绑创建机的解释器路径。到目标机用 `scripts\win7\install_win7_env.py` 重建（§4.2）。
3. **绝不把 `.env` 拷去现场**：仓库根的 `.env` 含凭据；交付产物里的 `.env` 由打包器生成（模板见 §3.3）。

> 一句话边界：一仓双 venv 解决"同一份代码、两套依赖、一个入口"，但**不能让 Win7 机器跑最新依赖栈**（pydantic-core 2.46 的 Rust 扩展是硬红线）。Win7 上"最新"的天花板 = 最新代码 + 冻结依赖（exe 或跑源码）。

> **布局变更（2026-09-14）**：Win7 相关脚本统一收到 **`scripts/win7/`** —— `install_win7_env.py`、`build_win7_exe.py`、`check_dist_win7.py`、`check_pyd_imports.py`、`patch_mcp_win7.py`、`diag_win7_exe.py`、`setup_win7_env.bat`（本文档全部命令已按新路径写）。
> 留在 `scripts/` 的是**两边共用**的：`edi_mcp_server.spec`（打包 spec，开发构建也用）、`run.bat`（产物启动脚本来源）、`Logo.ico`、`build.ps1`、`smoke_test_exe.py`。
> 现场旧副本如果是扁平布局（`scripts\build_win7_exe.py` 等）**照旧能用**（副本是自包含的）；下次同步时把整个 `scripts\` 重拷一遍即可切到新布局。

---

## 2. 为什么是这条路：全栈冻结到 Rust < 1.78 时代

### 2.1 现场报错与根因（两类错误别混）

| 现象 | 错误码 | 含义 | 处置 |
|---|---|---|---|
| `DLL load failed ... 找不到指定的模块` | WinError **126** | DLL 或其依赖**没找到** | 装运行库有效：UCRT（KB3118401）、VC++ 2015-2022 x64 |
| `DLL load failed ... 找不到指定的程序` | WinError **127** | DLL 找到了，但**依赖里没有它要的导出函数** | **装补丁无效**，这是编译期定死的 API 下限 → 换版本 |

现场实测（2026-09-11）：Win7 上跑官方 `uv.lock` 依赖，第一次 `import mcp` 就崩在 `_pydantic_core`，报 **127**。

### 2.2 硬阻塞机制

`pydantic_core` 是 **Rust** 写的，而 **Rust 自 1.78（2024-05）起 std 已不支持 Win7**。该版本 wheel 的导入表里硬导入三条 Win7 根本没有的符号：

| 导入 DLL | 符号 | 何时才有 |
|---|---|---|
| `bcryptprimitives.dll` | `ProcessPrng` | Windows 10 1809+ |
| `api-ms-win-core-synch-l1-2-0.dll` | `WaitOnAddress` / `WakeByAddressAll` / `WakeByAddressSingle` | Win8+（Win7 无此 API set） |
| `KERNEL32.dll` | `GetSystemTimePreciseAsFileTime` | Win8+ |

同批被牵连：`rpds-py`（mcp → jsonschema → referencing 硬依赖）、`cryptography`（Rust；本项目运行时不依赖，**不装就行**）。

### 2.3 版本分水岭（逐版本扫导入表实测）

| 包 | 干净的最后一个版本 | 起始红线 |
|---|---|---|
| pydantic-core | **2.27.2**（= pydantic 2.10.x） | 2.28.0 |
| rpds-py | **0.18.1** | 0.20.1 |

mcp 侧反推：`1.28.1` 要求 `pydantic>=2.11`（→ 必炸）；`1.13.0` 起下限就抬到 2.11；`1.12.4` 仍允许 `pydantic>=2.8`。⇒ **可用窗口 = mcp ≤ 1.12.4**。

**结论（原先那份《WIN7降级可行性评估》的取舍，该文已并入本文并删除）**：路线①（降 Python 3.9 + 自研传输层 ~300 行 + 14 个依赖全降）、② VM、③ vendor SDK 打补丁 —— 全部否掉；采纳 **路线⑤：PythonWin7 跑 3.10 + 全栈冻结到 Rust<1.78 时代（mcp 1.12.4 / pydantic 2.10.6 / pydantic-core 2.27.2 / rpds-py 0.18.1）+ 1 处 SDK 补丁（§5）**。

### 2.4 六关实测（开发机 CPython 3.10.20）

| # | 关卡 | 判据 | 结果 |
|---|---|---|---|
| 1 | 依赖能 import | `import mcp, grpc, google.protobuf, pydantic, numpy, win32com, matplotlib, psutil` | 通过 |
| 2 | 全栈 Win7 静态体检 | `python scripts/win7/check_pyd_imports.py` | **98 个扩展 0 命中红线** |
| 3 | 工具注册 | `import servers.registry_server` | **90 工具**（需 §5 补丁） |
| 4 | 测试套件 | `python -m pytest tests -q` | 全绿 |
| 5 | 服务启动 | `/ready` `/health` | `tool_count=90`、`tools_hash=ae6cd062` |
| 6 | 新旧协议互通 | mcp 1.28.1 客户端 连 1.12.4 服务端 | 握手 `protocol 2025-06-18`，90 工具 / 8 资源 / 9 prompt |

真机加载与实机运行两关已于 **2026-09-14 在 Win7 真机验证通过**（见 §6.4、§10）。

---

## 3. 路径 A：部署已打包好的产物（现场主线）

### 3.1 目标机前置检查

| 项 | 怎么查（每行可单独粘贴） | 通过标准 |
|---|---|---|
| Windows 版本 | `winver` | Windows 7 **SP1**（不带 SP1 装不上下面的补丁） |
| 补丁 KB4474419（SHA-2） | `wmic qfe list brief \| findstr /i "4474419"` | 有输出行 |
| 补丁 KB3118401（UCRT） | `wmic qfe list brief \| findstr /i "3118401"`，或 `dir C:\Windows\System32\ucrtbase.dll` | 有输出行 / 文件存在 |
| 磁盘空间 | `fsutil volume diskfree c:` | 可用 ≥ 300 MB（目录 87.5 MB） |
| 端口空闲 | `netstat -ano \| findstr ":50026"` | 无输出（有输出见 §9） |

**补丁怎么装**（顺序不能乱：SP1 → KB4474419 → KB3118401，**每装完一项重启**）：

1. 先看「计算机 → 属性」里 Windows 版本是否带 **Service Pack 1**；没有就先装 `windows6.1-KB976932-X64.exe`（KB976932 就是 SP1 编号）。
2. 打开 [Microsoft Update Catalog](https://www.catalog.update.microsoft.com/) → 搜 `KB4474419` → 选 **Windows 7 x64**、版本号最大的那个 `.msu` → 下载 → 双击安装 → 重启。
3. 同样方式搜 `KB3118401` → 选 **Windows 7（Catalog 里叫 Windows 6.1）x64** 的 `Windows6.1-KB3118401-x64.msu` → 装 → 重启。
   > ⚠️ Win7 的 UCRT 是 **KB3118401**，**不是** KB2999226（那个只覆盖 Vista / Server 2008）。
4. 验证：`dir C:\Windows\System32\api-ms-win-core-path-l1-1-0.dll` 应存在。

**VC++ 运行库**：本产物已自带 `vcruntime140.dll` / `vcruntime140_1.dll` / `msvcp140*.dll`（spec 里显式捆绑），**通常不用**单独装 VC++ 2015-2022。只有出现 §9 的 WinError 126 时才补装。
> 别把两件事搞混：VC++ 运行库（`msvcp140` / `vcruntime140`）**已随包走**；UCRT（`api-ms-win-crt-*`、`api-ms-win-core-path-l1-1-0.dll`）**仍要装 KB3118401**。

### 3.2 传输与完整性校验

打包机上压成一个文件再传（有 Python 的那台）：

```cmd
python -m zipfile -c edi-mcp-win7.zip dist\edi-mcp
```

目标机上核对传输完整性（`certutil` 是 Win7 自带）：

```cmd
certutil -hashfile edi-mcp-win7.zip MD5
```

与打包机同一条命令的结果一致 → 传输无损，解压到例如 `C:\edi-mcp\`。

解压后**缺一不可**的 4 样：

| 文件/目录 | 说明 |
|---|---|
| `edi_mcp_server.exe` | 主程序（窗口模式，约 9.0 MB） |
| `start_server.bat` | 双击入口（起服务 + 开 UI） |
| `.env` | 全部运行配置（§3.3） |
| `_internal\` | 运行时依赖（Python / gRPC / 冻结栈…… 几千个文件） |

PE 文件数自检（真机基线 **115**）：

```cmd
dir /s /b C:\edi-mcp\*.dll C:\edi-mcp\*.pyd C:\edi-mcp\*.exe | find /c /v ""
```

> ⚠️ **不要拿开发机的 exe md5 去核目标机的 exe**：两次构建收进去的文件集本来就不同（Win7 上打包会额外收本机 UCRT 垫片，PE 数 74 → 115），exe 不可能逐字节相同。**md5 只在"同一次交付"内做传输校验**（zip 级别最稳），exe 的 md5 属交付记录（附录 B）。

### 3.3 首次配置：`.env`（与 exe 同目录，改完不用重打包）

| 变量 | 默认值 | 说明 / 什么时候改 |
|---|---|---|
| `EDA_GRPC_SERVER` | `127.0.0.1:50055` | EDI gRPC 地址；同机就保持默认 |
| `EDI_PATH` | 空 | EDI 客户端 exe 路径。**留空=自动探测**（`EDI.exe` > `EDA-PMDS.exe` > `CAIS.exe`）；装在非默认位置或探测报错时才填绝对路径 |
| `TURBOCHARTS_PATH` | 空 | 同上，自动探测 `turbocharts_app.exe` > `TurboCharts.exe` |
| `MCP_TRANSPORT` | `streamable-http` | 保持默认；`stdio` 用于被别的程序当子进程拉起的用法 |
| `MCP_PORT` | `50026` | 服务端口（`start_server.bat` 里提示的 UI/MCP 地址写死 50026，改端口后请手动访问新端口） |
| `MCP_ALLOWED_PROCESSES` | `edi-agent-service.exe` | **进程白名单**：只有进程名含这些子串（逗号分隔）的进程能调 `/mcp`。**留空=关闭白名单**；客户端换名字后不同步改 → 调用被拒 |
| `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL` | 空 | 三个都填才启用图片视觉分析 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 空 | 填了才启用 `/chat` 多轮工具调用 |
| `REPORT_RENDER_URL` | `http://127.0.0.1:17867/...` | 仿真报告渲染服务地址 |

`.env` 是 UTF-8/ASCII 文本，记事本可改，但**别存成 UTF-16**（另存为选 ANSI / UTF-8）。**改完重启服务生效。**

### 3.4 启动服务

**双击（推荐）**：双击 `start_server.bat` → 它 `cd` 到自己所在目录 → 用默认浏览器打开 UI（`http://127.0.0.1:50026/ui`）→ 前台运行 `edi_mcp_server.exe`（自动读同目录 `.env`）。**关闭窗口 / Ctrl+C = 停服务。**

> ⚠️ exe 是**窗口模式**（spec `console=False`）：**双击后没有任何日志输出，这是设计如此、不是故障**。要日志走下面命令行方式，或用 `/ready`、`/health` 探活。

**命令行（排错用）**：

```cmd
cd /d C:\edi-mcp && edi_mcp_server.exe --transport streamable-http --port 50026
```

| 参数 | 取值 | 说明 |
|---|---|---|
| `--transport` | `streamable-http`（默认）/ `stdio` | 一般保持默认 |
| `--port` | 1–65535 | 覆盖 `.env` 的 `MCP_PORT`；超范围会直接报错退出 |

Host 固定 `127.0.0.1`（代码硬编码，不可配置）。

**确认已就绪**：浏览器打开 `http://127.0.0.1:50026/ready`（启动中返回 503，等几秒刷新）。真机实测返回：

```json
{"status":"ready","transport":"streamable-http","stateless":true,"version":"0.1.9",
 "grpc":"online","tool_count":90,"tools_hash":"ae6cd062","started_at":1789351514.9886186}
```

| 字段 | 含义 |
|---|---|
| `status` | `ready` = 初始化完成可服务 |
| `transport` | 当前通信方式 |
| `version` | 服务版本（基线 0.1.9） |
| `grpc` | `online` = `EDA_GRPC_SERVER` 已连通；`offline` = EDI 没起/地址不对 |
| `tool_count` | 注册工具数（基线 **90**） |
| `tools_hash` | 工具集指纹（基线 `ae6cd062`）；与客户端不一致 = 两边不是同一版 |
| `started_at` | 启动时间戳（秒） |

`grpc:offline` 不影响服务自身启动，但**业务工具会失败** —— 先确保 EDI 已启动。

### 3.5 接口一览（都在 `MCP_PORT`，默认 50026）

| 路径 | 方法 | 用途 |
|---|---|---|
| `/ready` | GET | 就绪探针：启动中 503，就绪 200 + JSON |
| `/ui`、`/` | GET | Web UI（`start_server.bat` 默认打开） |
| `/health` | GET | 健康检查 |
| `/tools/list` | GET | 工具清单 |
| `/metrics` | GET | 指标 |
| `/mcp` | — | **MCP 协议入口**（streamable-http），客户端填这个 |
| `/chat` | POST | Chat（需先配 `LLM_*`） |
| `/images/{token}`、`/documents/{token}` | GET | 工具产出的图片/文档 |
| `/upload` | POST | 上传 |

服务固定只监听 `127.0.0.1`（host 硬编码，不可配置）。

### 3.6 接入客户端

1. **MCP 地址**填 `http://127.0.0.1:50026/mcp`（同机；服务固定只监听本机）。
2. **进程白名单**：客户端进程名要能匹配 `MCP_ALLOWED_PROCESSES`（默认 `edi-agent-service.exe`）。被拒时服务端日志有 `[process-probe]` 记录。**"客户端连不上 / 403" 先查这条。**
3. **一致性自检**：客户端看到的工具数应为 **90**、`tools_hash` 与 `/ready` 相同（基线 `ae6cd062`）。对不上 = 客户端连的不是这版服务。

### 3.7 停止 / 卸载

```cmd
taskkill /f /im edi_mcp_server.exe
netstat -ano | findstr ":50026"
```

第二条无输出 = 端口已释放。**卸载 = 删目录 + 结束进程**：本产物**不写注册表、不注册系统服务、不改环境变量**（前提是你没手工加启动项/计划任务）。

### 3.8 常驻 / 开机自启（可选，**未在 Win7 上验证**）

- **方式 A（简单）**：把 `start_server.bat` 快捷方式放进启动文件夹（<kbd>Win</kbd>+<kbd>R</kbd> → `shell:startup`）。
- **方式 B（服务化）**：任务计划程序 → 触发器"计算机启动时" → 操作 `C:\edi-mcp\start_server.bat`、起始于 `C:\edi-mcp`；"不管用户是否登录"运行需勾"使用最高权限"，且**不能有交互式窗口**。

> ⚠️ 两条路径**都没有真机验证过**，现场用之前先手工验一轮。

---

## 4. 路径 B：在 Win7 上从源码建运行环境

> 只在"要改代码 / 要重新打包"时才需要。只要跑服务，用路径 A。

### 4.1 装 PythonWin7（非官方 Python 3.10）

主项目：[adang1345/PythonWin7](https://github.com/adang1345/PythonWin7)（3.9~3.11 支持 Win7）。

1. 打开仓库 → **Releases** → 在最新 Release 的 Assets 里认准**安装器**：
   - 要下：`python-3.10.x-amd64.exe`（x64；32 位系统选 `python-3.10.x-win32.exe`）
   - 不要下：`embeddable`（嵌入式 zip，装不了第三方包）、NuGet 包、help 文档
2. 版本选 **3.10.x**（覆盖全、稳定，且冻结栈按 3.10 实测验证，见 §7）。
3. 安装向导**底部勾上「Add Python 3.10 to PATH」**，其余默认。
4. 验证：`python -V` 应输出 3.10.x。

常见踩坑：

| 现象 | 原因 | 处置 |
|---|---|---|
| `python` 命令找不到 | 没勾「Add to PATH」 | 重装勾上，或把 `C:\Python310\` 与 `...\Scripts\` 加入系统 PATH |
| `python` 弹出应用商店 | 系统里有「假 python」占位符 | 装真 PythonWin7 并勾 PATH，或让真目录在 PATH 里更靠前 |
| 仍报 `api-ms-win-core-path-l1-1-0.dll 缺失` | UCRT 没装成功 | 回 §3.1 装 KB3118401 并重启 |

> PythonWin7 已内置 `api-ms-win-core-path-l1-1-0.dll` 修复（Wine 移植），UCRT 装好通常不用单独补 DLL。参考：[adang1345/api-ms-win-core-path](https://github.com/adang1345/api-ms-win-core-path)、[nalexandru/api-ms-win-core-path-HACK](https://github.com/nalexandru/api-ms-win-core-path-HACK)、[liudonghua123/windows-python-installer](https://github.com/liudonghua123/windows-python-installer)、[jixunmoe/py3-win7](https://github.com/jixunmoe/py3-win7)。

### 4.2 一键建环境（幂等）

依赖清单用仓库根目录的 **[`requirements-win7.txt`](../requirements-win7.txt)**，**不要**用主 `pyproject.toml` / `uv.lock`（那里 `mcp>=1.28.1` 与冻结栈冲突）。

```cmd
scripts\win7\setup_win7_env.bat                      :: 双击/命令行皆可（先体检 Python 3.10 与 UCRT）
.venv-win7\Scripts\python.exe scripts\win7\install_win7_env.py --check    :: 只体检，不装
.venv-win7\Scripts\python.exe scripts\win7\install_win7_env.py           :: 装到 .venv-win7（可重复执行）
.venv-win7\Scripts\python.exe scripts\win7\install_win7_env.py --recreate --no-smoke
```

脚本收尾四项自检（全部 PASS 才算成）：依赖 import 关 → 全栈 `.pyd` Win7 红线扫描（**0 命中**才过）→ 工具注册数（期望 **90**）→ 起服务探 `/ready`。

**离线安装**（现场不通网）：先在有网机器下载 wheel，再拷过去装：

```cmd
.venv-win7\Scripts\python.exe scripts\win7\install_win7_env.py --download D:\wheelhouse
.venv-win7\Scripts\python.exe scripts\win7\install_win7_env.py --offline  D:\wheelhouse
```

> ⚠️ **绝不从开发机拷 `.venv-win7`**：`pyvenv.cfg` 里 `home=` 指向开发机的 uv CPython 路径，跨机必然不可用。要在目标机上重建（`--recreate`）。

### 4.3 源码方式启动

```cmd
cd /d C:\edi-grpc-mcp
.venv-win7\Scripts\python.exe start_servers.py --transport streamable-http --port 50026
```

---

## 5. SDK 补丁（路径 B 必做）

`mcp 1.12.4` 的 `Tool.from_function` 把 `get_origin()` 为 `None` 的注解直接喂给 `issubclass()`：

```python
if get_origin(param.annotation) is not None: continue
if issubclass(param.annotation, Context):   # ← 字符串注解 → TypeError
```

本项目大量模块带 `from __future__ import annotations`（注解是字符串）→ **服务端导入即崩**：`TypeError: issubclass() arg 1 must be a class`。上游到 `mcp 1.14.1` 才修，而 1.14.1 要 `pydantic>=2.11`（红线）→ **补丁绕不开**。

```cmd
.venv-win7\Scripts\python.exe scripts\win7\patch_mcp_win7.py            :: 幂等
.venv-win7\Scripts\python.exe scripts\win7\patch_mcp_win7.py --check    :: 只检查状态
```

补丁 = 上游等价实现：先 `get_type_hints()` 解析字符串注解，再递归识别 `Context`（含 `Context | None`）。
> ⚠️ **`get_origin` 必须判在 `isinstance(..., type)` 之前**：Py3.10 的 `isinstance(list[str], type)` 为 `True`，顺序反了会把 GenericAlias 再送进 `issubclass` 崩一次。

---

## 6. 打包 exe（换代码后重打）

### 6.1 一条命令（不依赖 uv / PowerShell —— Win7 上都没有）

```cmd
cd /d C:\edi-grpc-mcp
.venv-win7\Scripts\python.exe -m pip install pyinstaller==6.22.3            :: 一次性
.venv-win7\Scripts\python.exe scripts\win7\build_win7_exe.py --smoke --dist dist-win7/edi-mcp
```

> 打包器会**拒绝非 3.10 解释器**（用 3.12 跑会直接报 `[FAIL] 必须用 Python 3.10 打包`）—— 防的是手滑用开发栈打出上不了 Win7 的 exe。
> `--dist` 缺省是 `dist/edi-mcp`；开发机的 3.12 构建也在用 `dist/`，所以给 Win7 打包建议显式给 `--dist dist-win7/edi-mcp`（见 §1.5）。

它做五件事：PyInstaller 打 spec → 生成 `dist\edi-mcp\.env` → 拷 `start_server.bat` → `scripts\win7\check_dist_win7.py` 产物体检 →（`--smoke`）起 exe 探 `/ready`。
`--no-build` 只补 `.env`/拷 bat/体检；`--port N` 指定写进 `.env` 的端口。

### 6.2 spec 关键点

- **必须用带 `binaries=_msvc_dlls` 的那份 spec**（显式捆绑 `msvcp140.dll` / `_1` / `_2` / `_atomic_wait`），否则没装 VC++ redist 的目标机会崩。
- `console=False`：exe 是**窗口模式**，双击无日志（现场排错要么走 `--smoke`，要么临时改 `console=True` 出诊断版）。
- 打包必须在 **Python 3.10（PythonWin7 或 3.10.x）** 下做，不能用 3.12；建议直接在 Win7 上打，避免 Win10 构建物在 Win7 上的 loader 差异。

### 6.3 产物体检判据（`scripts/win7/check_dist_win7.py`）

末行是纯 ASCII 契约行，便于脚本解析：`DIST_RESULT files=N redlines=M crt_missing=K`（**M=K=0 才算过**）。另有：

- `[OK] N 个 PE 文件 0 命中 Win7 红线`：逐个 PE 扫 Win8+/Win10 专用符号
- `[i] 包内 UCRT 与本机 System32 同源，降级为提示`：Win7 上打包会把**本机 UCRT**（`_internal\ucrtbase.dll` + 一批 `api-ms-win-*` 垫片）收进包，与本机 `System32\ucrtbase.dll` 逐字节相同时**不算红线**（本机 Python 此刻就在用同一文件）；只有**外来版本**才计入 redlines
- CRT 清单：`dist 自带` / `仍需系统提供` / UCRT API set 计数

### 6.4 实测基线（2026-09-14）

| 项 | 开发机（Windows 10） | Win7 真机 |
|---|---|---|
| PyInstaller | 6.22.3 | 6.22.3 |
| PE 文件数 | 74 | **115**（多出的是本机 UCRT 垫片） |
| 体检红线 | 0 | 0（包内 UCRT 与系统同源 → 提示） |
| 目录体积 | 86.6 MB | 87.5 MB |
| 冒烟 `/ready` | `tool_count=90`、`tools_hash=ae6cd062` | 同左（当时 `grpc:offline`：EDI 没起） |

> 判据速记：**74 PE = 带 msvcp140 捆绑的新 spec；70 PE = 旧 spec（CRT 没随包带）；115 PE = 在 Win7 上打包**。

### 6.5 现场实跑自检脚本（`scripts/win7/diag_win7_exe.py`）

在目标机上直接验证"产物能不能跑"：

```cmd
cd /d C:\edi-grpc-mcp && .venv-win7\Scripts\python.exe scripts\win7\diag_win7_exe.py
```

做 5 件事：产物/解释器检查 → 包内 UCRT 与系统 md5 比对 → 列 CRT 相关 DLL → **实起 exe 探 `/ready`** → 收尾杀进程。末行契约：`DIAG_RESULT checks=N failed=M`。
> 若屏幕弹出 `Failed to load Python DLL` 之类对话框，**先读弹窗正文**（它指名哪个 DLL 加载失败），再按"确定"。

---

## 7. 冻结栈与维护税（必须接受的约束）

1. **不能升** `mcp`（>1.12.4）/ `pydantic`（>2.10）/ `pydantic-core`（>2.27.2）/ `rpds-py`（>0.18.1）——升任一即回 Win7 死局。
2. 新增工具**不得**使用 pydantic 2.11+ 或新 mcp SDK 才有的 API —— 开发机跑得通 ≠ Win7 上线跑得通。
3. **cryptography 不要装**（Rust 编译，Win7 红线；本项目运行时不需要）。
4. 每次动依赖，先在目标环境跑 `scripts\win7\check_pyd_imports.py` 看有没有踩 Win7 红线。

---

## 8. 现场踩过的坑（均为真机实测，现象 → 根因 → 处置）

| # | 现象 | 根因 | 处置 / 现状 |
|---|---|---|---|
| 1 | `setup_win7_env.bat` 第 2 步 `UnicodeDecodeError: 'gbk' codec can't decode byte 0x80` | `requirements-win7.txt` 写了中文注释，pip 23 读 `-r` 文件**按本地代码页解码**（Win7=GBK）；开发机 `utf8_mode=1` 掩盖了它 | 文件改**纯 ASCII**；`install_win7_env.py` 加护栏（非 ASCII 打 `[WARN]`、命中该错直接提示换文件）。**已修（2026-09-14）** |
| 2 | 装机最后一步 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` | 脚本 `print` 里用了 GBK 没有的 `✓`（`✅`/`❌` 同理） | 全部改 GBK 安全字符（`[OK]`/`[!]`/`-`）+ 文件头 `sys.stdout.reconfigure(errors="replace")` 兜底；扫描器新增 ASCII 契约行 `SCAN_RESULT files=N hits=M`；已固化进 `tests/test_win7_frozen_env.py`。**已修** |
| 3 | Win7 打包后体检报 `_internal\ucrtbase.dll` 命中红线，开发机却不报 | 在目标机上打包，PyInstaller 会把这台机器自己的 UCRT 收进包（开发机那份 CPython 不带 ucrtbase） | 定案：与本机 `System32\ucrtbase.dll` 逐字节相同 → **误报**，检查器改为"降级为提示"；实机起 exe 探 `/ready` 通过（8 项全过）。**已修（2026-09-14）** |
| 4 | 现场把多行命令粘进 cmd，整段拼成一行 → `系统找不到指定的路径` | Win7 的 cmd 粘贴**会吃掉换行** | 现场命令**一律单行**（或用 `&&` 串联）/ 落成 `.py`、`.bat` 再跑。见附录 A |
| 5 | 从开发机整目录拷过去，脚本报 `pydantic_core ... 找不到指定的程序` | 拷了开发机的 `.venv-win7`（`pyvenv.cfg` 绑开发机路径 + 装的是新版包） | **不拷 venv**，目标机上重建（§4.2） |
| 6 | 打包出来的 exe 里工具描述是旧的 | 现场副本是"某天整体拷过去的"快照，往往正好 = **git 已提交基线**；主仓工作区的未提交改动（spec、工具描述）没跟过去 | 打包前**核副本对齐**：`md5sum` 关键文件 + `git show HEAD:<file> \| diff --strip-trailing-cr -` 判断副本是否 = 基线；整目录 md5 逐字节比会被 CRLF 洗成一片 DIFF，注意区分 |
| 7 | 双击 exe 没反应、看不到任何日志 | exe 是窗口模式（`console=False`） | **设计如此，不是故障**；用 `/ready` 探活，排错走命令行启动 |
| 8 | 重打好的 `dist-win7\` 过一会儿又不见了（`dist\` 同样不见） | `dist-win7/` **当时没进 `.gitignore`**（只有 `dist/`、`build/`）→ 任何 `git clean -fd` / IDE「清理未跟踪文件」都会把它当垃圾一并清掉；有忽略规则的 `dist/` 只能人工删 | `.gitignore` 已补 `dist-win7/`（2026-09-14）；产物随时用 §6.2 那条命令重打，丢了不影响交付 |

---

## 9. 排错速查

| 现象 | 最可能原因 | 处置 |
|---|---|---|
| 双击后没窗口、没反应 | 正常（窗口模式无日志） | `/ready` 探活；要日志走 §3.4 命令行 |
| 弹窗 `Failed to load Python DLL ... _internal\python310.dll` | 目录没拷全（只拷了 exe） | 整个目录一起拷（§3.2） |
| 弹窗/日志 `找不到指定的模块`（126） | 缺系统运行库 | 装 KB3118401；仍不行补 VC++ 2015-2022 x64 |
| 弹窗/日志 `找不到指定的程序`（127） | 依赖了 Win7 没有的 API（版本红线） | **不要自己升降依赖**；对照 §2 的冻结栈 |
| `/ready` 连不上 | 进程没起 / 端口被占 / 防火墙 | `tasklist \| findstr edi_mcp_server`、`netstat -ano \| findstr ":50026"`、临时关防火墙试 |
| 客户端 403 / 连不上 | 进程白名单不匹配 | 改 `MCP_ALLOWED_PROCESSES`（或留空关闭）后重启 |
| 工具能列出但一调就报错，`grpc:offline` | EDI 没启动 / gRPC 地址错 | 起 EDI；核对 `.env` 的 `EDA_GRPC_SERVER` |
| 同一份产物这台机器能跑、那台不行 | 两台机器 UCRT/补丁不同 | 以目标机 `/ready` 实测为准；按 §3.1 补补丁 |
| 改了 `.env` 没生效 | 没重启服务 / 存成了 UTF-16 | 重启；记事本另存为 ANSI 或 UTF-8 |

---

## 10. 尚未验证

1. **开机自启**：`scripts\run.bat` 没在 3.10/Win7 上跑过（§3.8 两种做法都待验证）。
2. **与 EDI 联动**：现场那次 `grpc:offline`（那台机器 EDI 服务没起），"EDI 起来后 exe 调 gRPC 是否正常"仍待现场确认。
3. **现场接线细节**：服务端口、客户端侧 MCP 配置、进程白名单放行客户端 exe（现场日志里看 `[process-probe]`）。

---

## 附录 A：单行命令速查（**每条都可整行粘贴**）

> Win7 的 cmd 粘贴多行会吃掉换行（§8 坑 4），所以每条都写成单行；要组合就用 `&&`。

```cmd
wmic qfe list brief | findstr /i "4474419 3118401"
dir C:\Windows\System32\ucrtbase.dll
netstat -ano | findstr ":50026"
certutil -hashfile C:\edi-mcp\edi_mcp_server.exe MD5
dir /s /b C:\edi-mcp\*.dll C:\edi-mcp\*.pyd C:\edi-mcp\*.exe | find /c /v ""
tasklist | findstr edi_mcp_server
taskkill /f /im edi_mcp_server.exe
```

就绪探活（Win7 没有 curl，用浏览器）：`http://127.0.0.1:50026/ready`

---

## 附录 B：交付记录表（每次交付填写）

| 项 | 值 |
|---|---|
| 打包机 / 打包日期 | |
| zip 文件名 + md5 | |
| `edi_mcp_server.exe` md5（打包机现算） | |
| PE 文件数 | 现场打包基线 **115** |
| 目录体积 | 现场打包基线 **87.5 MB** |
| `/ready` JSON（或截图） | |
| `tool_count` / `tools_hash` | 基线 **90** / **ae6cd062** |

---

## 附录 C：参考链接

- [adang1345/PythonWin7](https://github.com/adang1345/PythonWin7) —— Win7 可用的非官方 Python 3.9~3.11
- [adang1345/api-ms-win-core-path](https://github.com/adang1345/api-ms-win-core-path)、[nalexandru/api-ms-win-core-path-HACK](https://github.com/nalexandru/api-ms-win-core-path-HACK) —— 单独补 `api-ms-win-core-path` 的 DLL
- [liudonghua123/windows-python-installer](https://github.com/liudonghua123/windows-python-installer)、[jixunmoe/py3-win7](https://github.com/jixunmoe/py3-win7) —— PythonWin7 的备选/镜像
- [Microsoft Update Catalog](https://www.catalog.update.microsoft.com/) —— 下 KB4474419 / KB3118401
