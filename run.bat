@echo off
REM DeepCode New UI - Windows Launcher
REM 深度代码新UI - Windows启动脚本

echo.
echo ========================================
echo   DeepCode New UI - Windows Launcher
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.9+
    pause
    exit /b 1
)

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Please install Node.js 18+
    echo Download from: https://nodejs.org/
    pause
    exit /b 1
)

echo [OK] Python found
echo [OK] Node.js found
echo.

REM Run the Python launcher
python "%~dp0deepcode.py"

pause
