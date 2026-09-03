@echo off
setlocal
cd /d "%~dp0"

REM Read MCP_API_KEY from .env (if configured) to build tokenized URLs
set "MCP_TOKEN="
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if /i "%%a"=="MCP_API_KEY" set "MCP_TOKEN=%%b"
    )
)

set "UI_URL=http://127.0.0.1:50026/ui"
set "MCP_URL=http://127.0.0.1:50026/mcp"
if defined MCP_TOKEN (
    set "UI_URL=%UI_URL%?token=%MCP_TOKEN%"
    set "MCP_URL=%MCP_URL%?token=%MCP_TOKEN%"
)

title EDI gRPC MCP
echo =================================================
echo   EDI gRPC MCP v0.1.7
echo   UI:   %UI_URL%
echo   MCP:  %MCP_URL%
if defined MCP_TOKEN echo   Token: %MCP_TOKEN%
echo   Close this window or press Ctrl+C to stop
echo =================================================
echo.
start "" "%UI_URL%"
edi_mcp_server.exe
