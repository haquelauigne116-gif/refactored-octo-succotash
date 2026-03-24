@echo off
echo =========================================
echo   XiaoYu AI Assistant - Starting...
echo =========================================
echo.

echo [1/3] Starting NeteaseCloudMusicApi...
start /B npx NeteaseCloudMusicApi >nul 2>&1

echo [2/3] Starting backend server...
start /B python -m uvicorn backend.server:app --host 0.0.0.0 --reload

echo [3/3] Waiting for server to start...
timeout /t 3 /nobreak >nul

echo Opening browser...
start http://127.0.0.1:8000

echo.
echo Server is running at http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo.

pause