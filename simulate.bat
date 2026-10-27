@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\simulate_test.py %*
pause
