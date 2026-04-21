@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║     XiaoYu AI Assistant - Launcher        ║
echo  ╚═══════════════════════════════════════════╝
echo.

REM ============================================================
REM  Phase 1: Detect Python
REM ============================================================
set "PY="

if exist "%~dp0Scripts\python.exe" (
    set "PY=%~dp0Scripts\python.exe"
    goto :py_found
)

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

echo  [FAIL] Python not found! Please install Python 3.10+.
pause
exit /b 1

:py_found
echo  [  OK  ] Python .................. %PY%

REM ============================================================
REM  Phase 2: Service Health Checks
REM ============================================================
echo.
echo  --- Service Health Check ---
echo.

set "ALL_OK=1"

REM ---- Check MinIO (port 9000) ----
set "MINIO_OK=0"
tasklist /FI "IMAGENAME eq minio.exe" 2>nul | find /I "minio.exe" >nul 2>&1
if %errorlevel% equ 0 (
    %PY% -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9000)); s.close(); print('ok')" >nul 2>&1
    if %errorlevel% equ 0 (
        set "MINIO_OK=1"
        echo  [  OK  ] MinIO .................. running on port 9000
    ) else (
        echo  [ WARN ] MinIO .................. process found but port 9000 unreachable
    )
) else (
    echo  [ WARN ] MinIO .................. NOT running
)

if "%MINIO_OK%"=="0" (
    set "ALL_OK=0"
    if exist "Z:\start.bat" (
        echo           - Auto-starting MinIO...
        start "MinIO" cmd /c "Z:\start.bat"
        echo           - Waiting for MinIO to initialize, about 10s...
        ping 127.0.0.1 -n 11 >nul
        %PY% -c "import socket; s=socket.socket(); s.settimeout(5); s.connect(('127.0.0.1',9000)); s.close()" >nul 2>&1
        if %errorlevel% equ 0 (
            echo           - MinIO started successfully!
            set "MINIO_OK=1"
        ) else (
            echo           - MinIO failed to start, file management may be unavailable.
        )
    ) else (
        echo           - Z:\start.bat not found, skipping auto-start.
    )
)

REM ---- Check NapCat (process + port 3000) ----
set "NAPCAT_OK=0"
tasklist /FI "IMAGENAME eq NapCatWinBootMain.exe" 2>nul | find /I "NapCatWinBootMain.exe" >nul 2>&1
if %errorlevel% equ 0 (
    %PY% -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',3000)); s.close()" >nul 2>&1
    if %errorlevel% equ 0 (
        set "NAPCAT_OK=1"
        echo  [  OK  ] NapCat QQ Bot .......... running on port 3000
    ) else (
        echo  [ WARN ] NapCat QQ Bot .......... process found but port 3000 unreachable
    )
) else (
    echo  [ WARN ] NapCat QQ Bot .......... NOT running
)

if "%NAPCAT_OK%"=="0" (
    set "ALL_OK=0"
    if exist "D:\NapCat\NapCat.44498.Shell\NapCatWinBootMain.exe" (
        echo           - Auto-starting NapCat [quick login: 3920800540]...
        start "NapCat" cmd /k "chcp 65001 >nul & cd /d D:\NapCat\NapCat.44498.Shell & NapCatWinBootMain.exe 3920800540"
        ping 127.0.0.1 -n 6 >nul
    ) else (
        echo           - NapCat executable not found, skipping auto-start.
    )
)
REM Check NapCat port outside the if block to avoid nested parenthesis issues
if "%NAPCAT_OK%"=="0" (
    %PY% -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1',3000)); s.close()" >nul 2>&1
    if %errorlevel% equ 0 (
        echo           - NapCat started successfully!
        set "NAPCAT_OK=1"
    ) else (
        echo           - NapCat started, but port 3000 not yet ready.
    )
)

REM ---- Check NeteaseCloudMusicApi (optional, Node.js) ----
set "NETEASE_OK=0"
%PY% -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('127.0.0.1',3400)); s.close()" >nul 2>&1
if %errorlevel% equ 0 (
    set "NETEASE_OK=1"
    echo  [  OK  ] NeteaseCloudMusicApi ... running on port 3400
)
if not %errorlevel% equ 0 call :try_start_netease

REM ============================================================
REM  Phase 3: Summary & Launch
REM ============================================================
echo.
echo  --- Status Summary ---
echo.

if "%MINIO_OK%"=="1" (
    echo   √ MinIO              : Ready
) else (
    echo   × MinIO              : Unavailable [file management disabled]
)

if "%NAPCAT_OK%"=="1" (
    echo   √ NapCat QQ          : Ready
) else (
    echo   × NapCat QQ          : Unavailable [messaging disabled]
)

if "%NETEASE_OK%"=="1" (
    echo   √ NeteaseCloudMusic  : Ready
) else (
    echo   × NeteaseCloudMusic  : Unavailable [music search disabled]
)

echo.
echo  ╔═══════════════════════════════════════════╗
echo  ║  Server: http://127.0.0.1:8000            ║
echo  ║  Press Ctrl+C to stop all services.       ║
echo  ╚═══════════════════════════════════════════╝
echo.

REM ---- Open browser after a short delay ----
start /B cmd /c "ping 127.0.0.1 -n 4 >nul & start http://127.0.0.1:8000"

REM ---- Run uvicorn in foreground (logs visible, Ctrl+C to stop) ----
%PY% -m uvicorn backend.server:app --host 0.0.0.0 --port 8000 --reload --timeout-keep-alive 300
goto :eof

REM ============================================================
REM  Subroutines
REM ============================================================
:try_start_netease
where npx >nul 2>&1
if %errorlevel% equ 0 (
    echo  [ INFO ] NeteaseCloudMusicApi ... starting in background...
    start "NeteaseAPI" cmd /c "set PORT=3400 && npx -y NeteaseCloudMusicApi@latest"
    set "NETEASE_OK=1"
    echo           - Netease API started.
) else (
    echo  [ SKIP ] NeteaseCloudMusicApi ... Node.js not found, skipping.
)
goto :eof
