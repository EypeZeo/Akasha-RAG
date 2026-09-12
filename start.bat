@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Akasha-RAG Launcher
cd /d "%~dp0"

if /I not "%OS%"=="Windows_NT" (
  echo Unsupported operating system. Akasha-RAG supports Windows only; Linux and macOS are not supported.
  set "EXIT_CODE=2"
  goto preflight_failed
)

if not exist "%~dp0scripts\bootstrap.ps1" (
  echo Startup preflight script is missing: %~dp0scripts\bootstrap.ps1
  set "EXIT_CODE=1"
  goto preflight_failed
)

echo Akasha-RAG is starting...
echo ========================================================
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" -ProjectRoot "%~dp0."
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto preflight_failed

set "BACKEND_PY=backend\.venv\Scripts\python.exe"
if not exist "%BACKEND_PY%" (
  echo Startup preflight completed without creating the backend virtual environment.
  set "EXIT_CODE=1"
  goto preflight_failed
)

:start_app
REM ---------------------------------------------------------------------------
REM bootstrap.ps1 records the Node directory it actually selected (system Node,
REM or the project-local one under .runtime\).  launcher.py finds node/npm via
REM shutil.which(), so without this the project-local Node would be invisible and
REM the frontend would fail to start on a machine with no system Node.
REM Process-local only: nothing touches the persistent user or system PATH.
REM ---------------------------------------------------------------------------
set "NODE_DIR_FILE=%~dp0.runtime\node-dir.txt"
if exist "%NODE_DIR_FILE%" (
  for /f "usebackq delims=" %%D in ("%NODE_DIR_FILE%") do (
    if exist "%%~D\node.exe" set "PATH=%%~D;%PATH%"
  )
)

echo.
echo Launching unified manager launcher.py...
"%BACKEND_PY%" launcher.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto launcher_failed
endlocal & exit /b 0

:preflight_failed
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
echo.
echo Startup preflight failed. Review the error above and press any key to close.
pause >nul
endlocal & exit /b %EXIT_CODE%

:launcher_failed
echo.
echo The launcher stopped with exit code %EXIT_CODE%. Review the error above and press any key to close.
pause >nul
endlocal & exit /b %EXIT_CODE%
