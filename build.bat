@echo off
chcp 65001 >nul
echo ============================
echo  CCMonitor 构建
echo ============================
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onefile --noconsole --icon app.ico --name CCMonitor --add-data "ui;ui" --add-data "app.ico;." app.py
echo.
echo 构建完成: dist\CCMonitor.exe
pause
