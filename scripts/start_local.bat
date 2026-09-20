@echo off
rem ===== EDI gRPC MCP - LOCAL mode, this machine only =====
title EDI gRPC MCP - local
echo ====================================================
echo   EDI gRPC MCP  -  LOCAL mode
echo   Started : %date% %time%
echo.
echo   Listen : 127.0.0.1:50026   this machine only
echo   MCP    : http://127.0.0.1:50026/mcp
echo   UI     : http://127.0.0.1:50026/ui
echo.
echo   Close this window or press Ctrl+C to stop.
echo ====================================================
echo.
edi_mcp_server.exe --host 127.0.0.1
