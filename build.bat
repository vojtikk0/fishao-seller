@echo off
rem Sestavi FishaoSeller.exe do slozky dist\
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || exit /b 1
python -m PyInstaller --noconfirm --clean --onefile --windowed --name FishaoSeller --icon fishao_seller\ui\icon.ico ^
  --collect-all winrt --collect-all webview --hidden-import PIL.ImageTk ^
  --add-data "fishao_seller\ui;fishao_seller\ui" run.py || exit /b 1
copy /y NAVOD.txt dist\NAVOD.txt >nul
echo.
echo Hotovo: dist\FishaoSeller.exe
