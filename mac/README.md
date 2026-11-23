# Fishao Seller – macOS

Samostatná verze pro macOS. Verze pro Windows je v kořeni repa a má vlastní releasy (`win-v…`).
Tahle verze má releasy `mac-v…`.

Funguje stejně jako Windows verze. Rozdíly:

| | Windows | macOS |
|---|---|---|
| Čtení textu | Windows OCR | Apple Vision (`fishao_seller/ocr.py`) |
| Okno hry, snímání, myš, F12 | `winutil.py` | `fishao_seller/macutil.py` (Quartz) |
| GUI | WebView2 | WKWebView |
| Ceny z trackeru | Edge / Chrome | Chrome / Edge / Brave / Chromium |
| Nastavení a logy | vedle .exe | `~/Library/Application Support/Fishao Seller` |

Potřebná oprávnění: **Nahrávání obrazovky** a **Zpřístupnění**. Návod pro uživatele je v [NAVOD_MAC.txt](NAVOD_MAC.txt).

## Vývoj (na Macu)

```sh
cd mac
pip3 install -r requirements.txt
python3 run.py                 # GUI
python3 run.py --tk            # záložní jednoduché GUI
python3 tests/test_offline.py  # test čtení na screenshotech (mac/tests/screens, jinak ../tests/screens)
./build_mac.sh                 # dist/Fishao Seller.app + dist/FishaoSeller-mac.zip
```

## Release

1. Zvyš `__version__` v `mac/fishao_seller/__init__.py` (např. na `1.0.1`) a commitni.
2. `git tag mac-v1.0.1 && git push origin mac-v1.0.1`
3. GitHub Action **Release macOS** sestaví aplikaci na Macu, spustí selftest a vytvoří release
   „Fishao Seller Mac 1.0.1“ se souborem `FishaoSeller-mac.zip`.

Bez releasu jde build vyzkoušet přes Actions → Release macOS → Run workflow, zip je pak v artefaktech běhu.
