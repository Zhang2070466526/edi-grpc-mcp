@echo off
rem ===== EDI gRPC MCP - REMOTE mode, all interfaces =====
title EDI gRPC MCP - remote

rem Detect the first IPv4 address (works regardless of Windows locale)
set "IP="
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do if not defined IP set "IP=%%a"
set "IP=%IP: =%"
if not defined IP set "IP=THIS-MACHINE-IP"

echo ====================================================
echo   EDI gRPC MCP  -  REMOTE mode
echo   Started : %date% %time%
echo.
echo   Listen : 0.0.0.0:50026   all interfaces
echo   MCP    : http://%IP%:50026/mcp
echo   UI     : http://%IP%:50026/ui
echo.
echo   Whitelist disabled - remote clients cannot be identified.
echo   Close this window or press Ctrl+C to stop.
echo ====================================================
echo.
edi_mcp_server.exe --host 0.0.0.0
