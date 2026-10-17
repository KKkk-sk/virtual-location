@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 虚拟定位工具 - 安装

set "INSTALLER="
if exist "install_ios.ps1" set "INSTALLER=install_ios.ps1"
if exist "install_android.ps1" set "INSTALLER=install_android.ps1"

if "%INSTALLER%"=="" (
    echo 找不到安装脚本 install_ios.ps1 / install_android.ps1
    echo 请确认压缩包已完整解压，而不是直接在压缩包里双击运行。
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0%INSTALLER%"

echo.
echo 按任意键关闭窗口...
pause >nul
