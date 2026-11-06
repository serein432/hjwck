# 智能语音播报系统 — 一键启动脚本
# 双击或在 PowerShell 中运行：powershell -ExecutionPolicy Bypass -File .\启动App.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  智能语音播报系统 - 启动中..." -ForegroundColor Cyan
Write-Host "  后端: http://127.0.0.1:9090" -ForegroundColor Cyan
Write-Host "  接口文档: http://127.0.0.1:9090/docs" -ForegroundColor Cyan
Write-Host "  账号: admin/admin123 (管理员) · user/user123" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan

# 检查 Python
try {
    python --version | Out-Null
} catch {
    Write-Host "错误：未找到 Python，请先安装 Python 3.10+ 并加入 PATH。" -ForegroundColor Red
    Read-Host "按回车退出"
    exit 1
}

# 检查依赖（缺失则提示安装）
try {
    python -c "import fastapi, uvicorn, webview" | Out-Null
} catch {
    Write-Host "首次运行，正在安装依赖（约 1-2 分钟）..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host "依赖安装失败，请手动执行：pip install -r requirements.txt" -ForegroundColor Red
        Read-Host "按回车退出"
        exit 1
    }
}

Write-Host "启动桌面 App..." -ForegroundColor Green
python app/desktop/app_desktop.py
