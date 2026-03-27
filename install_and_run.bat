@echo off
setlocal

echo [1/3] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH. Please install Python 3.10+ first.
    pause
    exit /b
)

echo [2/3] Setting up Virtual Environment (venv)...
if not exist venv (
    python -m venv venv
    echo Created new virtual environment 'venv'.
) else (
    echo Using existing virtual environment 'venv'.
)

echo [3/3] Activating venv and Installing Dependencies...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo ===========================================
echo [SUCCESS] Environment is fully configured!
echo ===========================================
echo Press any key to start the server now...
pause >nul

echo Starting NeteaseCloudMusicApi (Music Search Service)...
WHERE npx >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start /B npx -y NeteaseCloudMusicApi >nul 2>&1
    echo   - Netease API started in background.
) else (
    echo   [Warning] Node.js is not installed. Music search might be unavailable.
)

echo Starting Backend Server...
start http://127.0.0.1:8000
python -m uvicorn backend.server:app --host 0.0.0.0 --port 8000
