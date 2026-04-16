@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal

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
echo [1/3] Python found: %PY%

echo [2/3] Installing/upgrading dependencies...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt

echo ===========================================
echo [SUCCESS] Environment is fully configured!
echo ===========================================
echo Press any key to start the server now...
pause >nul

echo Starting NeteaseCloudMusicApi (Music Search Service)...
where npx >nul 2>&1
if %errorlevel% equ 0 (
    start /B npx -y NeteaseCloudMusicApi >nul 2>&1
    echo   - Netease API started in background.
) else (
    echo   [Warning] Node.js not installed. Music search might be unavailable.
)

echo Starting Backend Server...
start /B cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"
%PY% -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
