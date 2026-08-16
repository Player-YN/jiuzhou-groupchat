@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  ONLY launch entry (ASCII). Double-click = silent start.
rem  Optional args: debug | rebuild
rem  Log: desktop-electron\launch.log
rem  Close the window = auto kill backend/frontend
rem ============================================================

if /I "%~1"=="debug" goto debug
if /I "%~1"=="rebuild" goto rebuild

rem Silent: minimized host, no stuck empty cmd from start /B failures
start "Jiuzhou" /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-electron.ps1" -Silent
exit /b 0

:debug
title Jiuzhou start (debug)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-electron.ps1"
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo [FAIL] exit %EC%
  echo See desktop-electron\launch.log
  pause
)
exit /b %EC%

:rebuild
title Jiuzhou rebuild + start
echo [INFO] rebuild frontend then start...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\rebuild-frontend.ps1"
if errorlevel 1 (
  echo [FAIL] rebuild
  echo See desktop-electron\rebuild.log
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-electron.ps1" -ForceRestart
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo [FAIL] exit %EC%
  pause
)
exit /b %EC%
