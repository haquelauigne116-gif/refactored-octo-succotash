@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal

REM ---- Detect Python ----
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

echo Handing off to start.bat for full service orchestration...
call "%~dp0start.bat"
