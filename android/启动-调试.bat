@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "APPGUI="
if exist "fakegps_gui.py" set "APPGUI=fakegps_gui.py"
if exist "android_gui.py" set "APPGUI=android_gui.py"

if not exist ".venv\Scripts\python.exe" (
    echo 还没有安装，请先双击运行「安装.bat」。
    pause
    exit /b 1
)

echo 调试模式：窗口会停留并显示报错信息。
echo.
".venv\Scripts\python.exe" "%APPGUI%"
echo.
echo 程序已退出。把上面的报错截图发给分享者即可定位问题。
pause
