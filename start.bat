@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PROJECT_ROOT=%~dp0"
cd /d "%PROJECT_ROOT%"

set "APP_VERSION=unknown"
if exist "%PROJECT_ROOT%version.txt" set /p APP_VERSION=<"%PROJECT_ROOT%version.txt"
title Akasha-RAG %APP_VERSION%

if /I not "%OS%"=="Windows_NT" (
  echo Unsupported operating system. Akasha-RAG supports Windows only; Linux and macOS are not supported.
  set "EXIT_CODE=2"
  goto preflight_failed
)

if not exist "%PROJECT_ROOT%scripts\bootstrap.ps1" (
  echo Startup preflight script is missing: %PROJECT_ROOT%scripts\bootstrap.ps1
  set "EXIT_CODE=1"
  goto preflight_failed
)

echo [START] Akasha-RAG %APP_VERSION%
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\bootstrap.ps1" -ProjectRoot "%PROJECT_ROOT%."
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto preflight_failed

set "BACKEND_PY=%PROJECT_ROOT%backend\.venv\Scripts\python.exe"
if not exist "%BACKEND_PY%" (
  echo Startup preflight completed without creating the backend virtual environment.
  set "EXIT_CODE=1"
  goto preflight_failed
)

REM bootstrap.ps1 records the selected Node directory for this process.
set "NODE_DIR_FILE=%PROJECT_ROOT%.runtime\node-dir.txt"
if exist "%NODE_DIR_FILE%" (
  for /f "usebackq delims=" %%D in ("%NODE_DIR_FILE%") do (
    if exist "%%~D\node.exe" set "PATH=%%~D;%PATH%"
  )
)

"%BACKEND_PY%" "%PROJECT_ROOT%launcher.py"
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
