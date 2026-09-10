@echo off
setlocal
cd /d "%~dp0"

set "UI_URL=http://127.0.0.1:50026/ui"
set "MCP_URL=http://127.0.0.1:50026/mcp"

title EDI gRPC MCP
echo =================================================
echo   EDI gRPC MCP v0.1.8
echo   UI:   %UI_URL%
echo   MCP:  %MCP_URL%
echo   Close this window or press Ctrl+C to stop
echo =================================================
echo.
start "" "%UI_URL%"
edi_mcp_server.exe
