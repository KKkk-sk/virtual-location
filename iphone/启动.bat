@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "APPGUI="
if exist "fakegps_gui.py" set "APPGUI=fakegps_gui.py"
if exist "android_gui.py" set "APPGUI=android_gui.py"

if "%APPGUI%"=="" (
    echo 找不到界面程序，请确认压缩包已完整解压。
    pause
    exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo 还没有安装，请先双击运行「安装.bat」。
    echo.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "%APPGUI%"
