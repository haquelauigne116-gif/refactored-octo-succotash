@echo off
echo =========================================
echo   XiaoYu AI Assistant - Starting...
echo =========================================
echo.

echo [1/2] Starting backend server...
start /B python -m uvicorn backend.server:app --host 0.0.0.0 --reload

echo [2/2] Waiting for server to start...
timeout /t 3 /nobreak >nul

echo Opening browser...
start http://127.0.0.1:8000

echo.
echo Server is running at http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo.

pause