# edi_mcp_server 打包脚本
$ErrorActionPreference = "Stop"
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
uv run pytest -q -p no:cacheprovider
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
# 从开发机 .env 读取 MCP_API_KEY（不写死在脚本里，避免 token 进 git 历史）
$apiKey = ""
$srcEnv = Join-Path $root ".env"
if (Test-Path $srcEnv) {
    $line = Get-Content $srcEnv | Where-Object { $_ -match '^MCP_API_KEY=' } | Select-Object -First 1
    if ($line) {
        $apiKey = ($line -split '=', 2)[1].Trim()
    }
}
$envContent = @"
# EDI gRPC MCP configuration - edit paths for this computer
EDA_GRPC_SERVER=127.0.0.1:50055
# EDI exe path: leave empty to auto-detect (EDI.exe > EDA-PMDS.exe > CAIS.exe)
EDI_PATH=
# TurboCharts path: leave empty to auto-detect (turbocharts_app.exe > TurboCharts.exe)
TURBOCHARTS_PATH=
# Optional OpenClaw workspace. Leave empty to auto-detect, or set path manually.
OPENCLAW_WORKSPACE=
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=50026
# Access token: /mcp /ui /chat require ?token= to match this value (leave empty to disable auth)
MCP_API_KEY=$apiKey
# Optional: image vision analysis (enabled when all three are configured)
VISION_API_KEY=
VISION_BASE_URL=
VISION_MODEL=
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
Copy-Item -Force scripts/run.bat "$distDir\start_server.bat"
Remove-Item -Recurse -Force build -ErrorAction SilentlyContinue
Remove-Item -Force "$root\dist\edi_mcp_server.exe" -ErrorAction SilentlyContinue

# ── [7/7] 打包 ZIP ──
Write-Host "[7/7] Creating archive..." -ForegroundColor Yellow
Compress-Archive -Path "$distDir\*" -DestinationPath $zipPath -Force

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
