@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo =========================================
echo   XiaoYu AI Assistant - Starting...
echo =========================================
echo.

REM ---- Detect Python ----
set "PY="

if exist "C:\Python314\python.exe" (
    set "PY=C:\Python314\python.exe"
    goto :py_found
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    py -c "import sys" >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY=py"
        goto :py_found
    )
)

python --version >nul 2>&1
if %errorlevel% equ 0 (
    python -c "import sys" >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY=python"
        goto :py_found
    )
)

echo [ERROR] Python not found! Please install Python 3.10+.
pause
exit /b 1

:py_found
echo [OK] Python: %PY%

REM ---- Start NeteaseCloudMusicApi (optional, needs Node.js) ----
where npx >nul 2>&1
if %errorlevel% equ 0 (
    echo [1/2] Starting NeteaseCloudMusicApi...
    start /B npx -y NeteaseCloudMusicApi >nul 2>&1
    echo       Netease API started in background.
) else (
    echo [SKIP] Node.js not found, skipping NeteaseCloudMusicApi.
)

REM ---- Start backend server, then open browser after delay ----
echo [2/2] Starting backend server...
start /B cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"

echo.
echo =========================================
echo   Server: http://127.0.0.1:8000
echo   Press Ctrl+C to stop all services.
echo =========================================
echo.

REM ---- Run uvicorn in foreground (logs visible, Ctrl+C to stop) ----
%PY% -m uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload --timeout-keep-alive 300