@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
py -m PyInstaller --noconfirm --clean --onefile --windowed --name SantaMariaHelper --icon helper.ico --add-data "helper.ico;." --add-data "helper_logo.png;." --add-data "palm_ui.png;." --add-data "commands.json;." --add-data "rules.json;." --add-data "forum_rules.json;." --hidden-import pynput.keyboard._win32 main.py
if errorlevel 1 (pause & exit /b 1)
echo Done! dist\SantaMariaHelper.exe
pause
