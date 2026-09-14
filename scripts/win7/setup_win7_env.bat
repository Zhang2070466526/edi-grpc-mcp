@echo off
rem ==================================================================
rem  Win7 environment installer (route E: mcp<=1.12.4 / pydantic<=2.10)
rem  Double click to run. Args are passed through to install_win7_env.py
rem    setup_win7_env.bat --check
rem    setup_win7_env.bat --offline D:\wheelhouse
rem    setup_win7_env.bat --recreate --no-smoke
rem
rem  NOTE: this file must stay ASCII-only and CRLF-terminated:
rem  cmd.exe on Windows 7 mis-parses UTF-8 / LF-only .bat files.
rem ==================================================================
setlocal
cd /d "%~dp0..\.."

set "PY="
where py >nul 2>nul && set "PY=py -3.10"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [ERROR] python not found. Install PythonWin7 3.10 and check "Add Python to PATH".
  pause
  exit /b 1
)

%PY% -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] This python is not 3.10:
  %PY% -V
  echo         Route-E wheels are all cp310 - use PythonWin7 3.10.
  echo         Host check only: python scripts\win7\install_win7_env.py --check --allow-any-python
  pause
  exit /b 1
)

%PY% scripts\win7\install_win7_env.py %*
if errorlevel 1 (
  echo.
  echo [FAILED] see FAIL lines above; fix and rerun - this script is idempotent.
  pause
  exit /b 1
)
pause
endlocal
