@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "APP_VERSION="
set /p APP_VERSION=<VERSION
if not defined APP_VERSION set "APP_VERSION=dev"
set "APP_NAME=自动化控制台_v%APP_VERSION%"
echo Version: %APP_VERSION%
echo Installing build dependencies...
python -m pip install --upgrade pyinstaller pillow --disable-pip-version-check
if errorlevel 1 goto error
echo Building %APP_NAME%.exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "%APP_NAME%" --icon "%cd%\assets\app.ico" --add-data "%cd%\VERSION;." --distpath "." --workpath "build" --specpath "build" gui.py
if errorlevel 1 goto error
echo %APP_NAME%.exe>last_build.txt
echo.
echo Build complete: %cd%\%APP_NAME%.exe
start "" "%cd%\%APP_NAME%.exe"
pause
exit /b 0
:error
echo.
echo Build failed.
pause
exit /b 1
