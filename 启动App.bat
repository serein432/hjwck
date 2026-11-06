@echo off
chcp 65001 >nul
title Voice Broadcast System
cd /d "%~dp0"

echo =================================================
echo   Voice Broadcast System - starting...
echo   Backend: http://127.0.0.1:9090
echo   Account: admin/admin123 (admin) · user/user123
echo =================================================
echo.

REM ---- 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)
echo Python: & python --version
echo.

REM ---- 检查依赖（缺失则自动安装）----
python -c "import fastapi, uvicorn, webview" >nul 2>nul
if errorlevel 1 (
    echo First run, installing dependencies (about 1-2 min)...
    echo.
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo.
    echo Dependencies installed, launching desktop app...
    echo.
)

REM ---- 启动 App（失败时把错误写入日志并停住）----
python app\desktop\app_desktop.py
set RC=%errorlevel%
if not "%RC%"=="0" (
    echo.
    echo [ERROR] App exited with code %RC%. Error logged to startup_error.txt
    pause
    exit /b 1
)

echo.
echo Desktop app closed.
pause
