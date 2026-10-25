@echo off
rem ============================================================================
rem  BuildNow GG - start the local overlay server (proxy.py)
rem
rem  Double-click this file.  The server hosts the patched lobby / skin / icon
rem  assets on http://127.0.0.1:8123 for the userscript to pick up.
rem  If it is already running, this does nothing.
rem ============================================================================
setlocal
cd /d "%~dp0"

netstat -an | findstr "127.0.0.1:8123" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo Proxy is already running on http://127.0.0.1:8123
  timeout /t 5 >nul
  exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0proxy.py"
) else (
  start "bng-proxy" /min python "%~dp0proxy.py"
)

timeout /t 2 >nul
netstat -an | findstr "127.0.0.1:8123" | findstr "LISTENING" >nul
if errorlevel 1 (
  echo FAILED to start. Is Python installed and on PATH?
) else (
  echo Proxy running on http://127.0.0.1:8123  -  you can close this window.
)
timeout /t 6 >nul
