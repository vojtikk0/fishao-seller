#!/bin/bash
# Sestaví "Fishao Seller.app" do složky dist/ (spouštět na Macu)
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --clean --windowed --name "Fishao Seller" \
  --icon fishao_seller/ui/icon.ico --osx-bundle-identifier cz.fishao.seller \
  --collect-all webview --hidden-import PIL.ImageTk \
  --add-data "fishao_seller/ui:fishao_seller/ui" run.py
cp NAVOD_MAC.txt dist/NAVOD_MAC.txt
(cd dist && ditto -c -k --keepParent "Fishao Seller.app" FishaoSeller-mac.zip)
echo
echo "Hotovo: dist/Fishao Seller.app (+ dist/FishaoSeller-mac.zip)"
