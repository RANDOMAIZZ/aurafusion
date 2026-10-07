@echo off
chcp 65001 >nul
echo === AuraFusion -> one .exe ===
pip install -r "%~dp0requirements.txt"
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name AuraFusion "%~dp0main.py"
echo.
echo Done: dist\AuraFusion.exe
echo fusion_config.json will be created next to exe on first run.
pause
