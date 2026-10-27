@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name '自动化控制台*','自动化调试器*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue"
set "LATEST="
if exist "last_build.txt" set /p LATEST=<last_build.txt
if not defined LATEST (
    echo last_build.txt 为空，请先运行 build_exe.bat
    pause
    exit /b 1
)
if not exist "%LATEST%" (
    echo 找不到 %LATEST%，请先运行 build_exe.bat
    pause
    exit /b 1
)
echo 启动: %LATEST%
start "" "%LATEST%"
exit /b 0
