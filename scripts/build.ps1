# edi_mcp_server 打包脚本
$ErrorActionPreference = "Stop"
# 清除可能残留的错误 VIRTUAL_ENV（如指向旧目录 mcp-grpc），避免 uv 输出 "does not match" 警告
Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
Push-Location $PSScriptRoot\..
$root = Get-Location

Write-Host "=== EDI gRPC MCP Build ===" -ForegroundColor Cyan

# ── 阈值 ──
$MAX_DIR_MB  = 250
$MAX_ZIP_MB  = 100
$MAX_EXE_MB  = 80   # 超过此值提示可能重复打包

# ── [1/7] 清理 ──
Write-Host "[1/7] Cleaning..." -ForegroundColor Yellow
Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

# ── [2/7] 测试 ──
Write-Host "[2/7] Running tests..." -ForegroundColor Yellow
# Fresh basetemp each run: default %TEMP%\pytest-of-JGL's pytest-current junction
# gets locked by a leftover process -> cleanup PermissionError (false TESTS FAILED).
# Forward slashes required: uv run swallows backslash paths to pytest (empty basetemp).
$pytestTmp = (Join-Path $env:TEMP ("edi-pytest-" + (Get-Date -Format "yyyyMMdd-HHmmss-fff"))).Replace("\", "/")
uv run pytest -q -p no:cacheprovider --basetemp="$pytestTmp"
if ($LASTEXITCODE -ne 0) { Write-Host "TESTS FAILED" -ForegroundColor Red; Pop-Location; exit $LASTEXITCODE }

# ── [3/7] 构建 ──
Write-Host "[3/7] Building with PyInstaller..." -ForegroundColor Yellow
uv run pyinstaller --clean --noconfirm scripts/edi_mcp_server.spec

# ── [4/7] 验证产物 ──
Write-Host "[4/7] Verifying artifacts..." -ForegroundColor Yellow
$distDir = "$root\dist\edi-mcp"
$exePath = "$distDir\edi_mcp_server.exe"
$zipPath = "$root\dist\edi-mcp.zip"

if (-not (Test-Path $exePath)) {
    Write-Host "FAIL: $exePath not found" -ForegroundColor Red
    Pop-Location; exit 1
}

# ── [5/7] 统计 ──
Write-Host "[5/7] Collecting stats..." -ForegroundColor Yellow

$exeSize  = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
$dirSize  = [math]::Round((Get-ChildItem $distDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
$fileCount = (Get-ChildItem $distDir -Recurse -File).Count

# 最大的 10 个文件
$topFiles = @(Get-ChildItem $distDir -Recurse -File | Sort-Object Length -Descending | Select-Object -First 10 | ForEach-Object { "$([math]::Round($_.Length/1MB,1)) MB  $($_.Directory.Name)\$($_.Name)" })

# ── [6/7] 生成配置和启动脚本 ──
Write-Host "[6/7] Generating config + launcher..." -ForegroundColor Yellow
$envContent = @"
# EDI gRPC MCP configuration - edit paths for this computer
EDA_GRPC_SERVER=127.0.0.1:50055
# EDI exe path: leave empty to auto-detect (EDI.exe > EDA-PMDS.exe > CAIS.exe)
EDI_PATH=
# TurboCharts path: leave empty to auto-detect (turbocharts_app.exe > TurboCharts.exe)
TURBOCHARTS_PATH=
MCP_TRANSPORT=streamable-http
MCP_PORT=50026
# Listen address: 127.0.0.1 = this machine only; 0.0.0.0 = all interfaces (remote).
# NOTE: the .bat launchers pass --host explicitly (start_local=127.0.0.1, start_remote=0.0.0.0), which overrides this.
MCP_BIND_HOST=127.0.0.1
# Extra allowed Host headers (comma-separated) for addresses auto-detection misses (e.g. reverse-proxy domain)
MCP_EXTRA_ALLOWED_HOSTS=
# Process whitelist (local mode only): comma-separated EXACT-match entries (cmd token / exe basename / full path).
# NOTE: ignored automatically in remote mode (--host not loopback), since remote clients cannot be identified.
MCP_ALLOWED_PROCESSES=edi-agent-service.exe
# Source-process probe when whitelist is off (local empty / remote auto-ignored). Keep 1 = only audit trail when no auth.
MCP_PROBE_ENABLED=1
# Optional: image vision analysis (enabled when all three are configured)
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
# Optional: Chat AI (LLM multi-round tool calling). Leave empty to disable.
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
# Optional: simulation report rendering
REPORT_RENDER_URL=http://127.0.0.1:17867/api/v1/reports/render
"@
$envPath = Join-Path $distDir ".env"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $envPath,
    $envContent.TrimStart("`r", "`n") + [Environment]::NewLine,
    $utf8NoBom
)
Copy-Item -Force scripts/start_local.bat "$distDir\start_local.bat"
Copy-Item -Force scripts/start_remote.bat "$distDir\start_remote.bat"
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
Remove-Item -Force "$root\dist\edi_mcp_server.exe" -ErrorAction SilentlyContinue

# ── [7/7] 打包 ZIP ──
Write-Host "[7/7] Creating archive..." -ForegroundColor Yellow
# 重试 3 次：PyInstaller 刚写完 _internal 时，杀毒软件（Defender）常瞬时锁住 DLL 导致 ZIP 失败
$zipDone = $false
for ($i = 1; $i -le 3 -and -not $zipDone; $i++) {
    try {
        Compress-Archive -Path "$distDir\*" -DestinationPath $zipPath -Force -ErrorAction Stop
        $zipDone = $true
    } catch {
        Write-Host "  ZIP attempt $i failed: $($_.Exception.Message)" -ForegroundColor Yellow
        if ($i -lt 3) { Write-Host "  retrying in 3s..." -ForegroundColor Yellow; Start-Sleep -Seconds 3 }
    }
}
if (-not $zipDone) { Write-Host "ZIP FAILED after 3 attempts" -ForegroundColor Red; Pop-Location; exit 1 }

$zipSize = 0
if (Test-Path $zipPath) {
    $zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
}

# ── 汇总 ──
Write-Host ""
Write-Host "Build summary" -ForegroundColor Cyan
Write-Host "--------------------------------" -ForegroundColor DarkGray
Write-Host ("EXE:        {0,7} MB" -f $exeSize)
Write-Host ("Directory:  {0,7} MB" -f $dirSize)
Write-Host ("ZIP:        {0,7} MB" -f $zipSize)
Write-Host ("Files:      {0,7}"    -f $fileCount)
Write-Host ("Tools:            dynamic (count determined at startup)")
Write-Host "--------------------------------" -ForegroundColor DarkGray

if ($topFiles) {
    Write-Host "Top 10 largest files:" -ForegroundColor DarkGray
    $topFiles | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    Write-Host "--------------------------------" -ForegroundColor DarkGray
}

# ── 阈值检查 ──
$errors = 0
if ($dirSize -gt $MAX_DIR_MB) {
    Write-Host "ERROR: Directory size $dirSize MB exceeds limit $MAX_DIR_MB MB" -ForegroundColor Red
    $errors++
}
if ($zipSize -gt $MAX_ZIP_MB) {
    Write-Host "ERROR: ZIP size $zipSize MB exceeds limit $MAX_ZIP_MB MB" -ForegroundColor Red
    $errors++
}
if ($exeSize -gt $MAX_EXE_MB) {
    Write-Host "WARNING: EXE size $exeSize MB > $MAX_EXE_MB MB — possible duplicate binaries" -ForegroundColor Yellow
}

if ($errors -gt 0) {
    Write-Host "Build FAILED: $errors threshold(s) exceeded" -ForegroundColor Red
    Pop-Location; exit 1
}

# ── [8/8] 冒烟测试 ──
Write-Host "[8/8] Smoke testing exe..." -ForegroundColor Yellow
uv run python scripts/smoke_test_exe.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build FAILED: exe smoke test failed" -ForegroundColor Red
    Pop-Location; exit $LASTEXITCODE
}
Write-Host "Smoke test passed." -ForegroundColor Green

Write-Host "Done. Output: dist/edi-mcp/ + dist/edi-mcp.zip" -ForegroundColor Green
Pop-Location
