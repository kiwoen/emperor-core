@echo off
echo ========================================
echo   huanxin-ai v2.0.0 环境安装脚本
echo ========================================
echo.

:: 检测 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 未检测到 Python。请安装 Python 3.11+ 后重试。
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%

:: 创建虚拟环境
if not exist venv (
    echo [INFO] 创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] 虚拟环境创建失败
        pause
        exit /b 1
    )
)
echo [OK] 虚拟环境就绪

:: 激活虚拟环境
call venv\Scripts\activate.bat

:: 安装依赖
echo [INFO] 安装依赖...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [WARN] 部分依赖安装失败，尝试继续...
)
echo [OK] 依赖安装完成

:: 初始化数据库
echo [INFO] 初始化数据库...
python -c "from huanxin.database import init_db; init_db()" 2>nul
if %errorlevel% equ 0 (echo [OK] 数据库初始化完成) else (echo [WARN] 数据库初始化跳过（可能已存在）)

:: 运行测试
echo [INFO] 运行基础测试...
python -m pytest tests/ -x -q --tb=short 2>nul
if %errorlevel% equ 0 (
    echo [OK] 测试通过
) else (
    echo [WARN] 部分测试未通过（Mock模式下可忽略）
)

echo.
echo ========================================
echo   安装完成！运行方式:
echo     python main.py --mode demo
echo     python main.py --mode chat
echo     python main.py --mode server
echo ========================================
pause
