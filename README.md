# Fishao Seller

Automatický prodej ryb ve hře **FISHAO – Reborn** podle cen z [fishao-tracker.cz](https://fishao-tracker.cz/#market).

Bot projde obchod „NAŠE DNEŠNÍ CENY“, u každé ryby přečte název, počet a dnešní cenu, porovná ji
s rozmezím známých cen z trackeru a výhodné ryby prodá (všechny kusy).

> ⚠️ Automatizace hry může být proti pravidlům hry – používáš na vlastní riziko.

## Použití

Stáhni `FishaoSeller.exe` a postupuj podle [NAVOD.txt](NAVOD.txt). Ve zkratce:

1. Spusť hru (v češtině) a otevři obchod „NAŠE DNEŠNÍ CENY“.
2. Spusť `FishaoSeller.exe` → **Test detekce** → **START** v režimu NANEČISTO.
3. Když to sedí, vypni NANEČISTO a spusť naostro.

Nouzové zastavení: **F12** nebo myš do levého horního rohu obrazovky.

**Požadavky:** Windows 10/11 s češtinou (Windows OCR), Edge nebo Chrome (stažení cen), WebView2 (součást Windows).

## Jak to funguje

| Část | Soubor |
|---|---|
| Ceny z trackeru (headless Edge/Chrome přes DevTools protokol) | `fishao_seller/tracker.py` |
| Čtení obrazovky: tlačítka, názvy, ceny, dialogy | `fishao_seller/vision.py` |
| Windows OCR (čeština) | `fishao_seller/ocr.py` |
| Párování názvů a pravidla prodeje | `fishao_seller/pricing.py` |
| Prodejní smyčka s kontrolami | `fishao_seller/bot.py` |
| GUI (WebView2, design Nocturne) / záložní Tk GUI | `fishao_seller/webview_app.py`, `fishao_seller/ui/` / `fishao_seller/app.py` |

Pojistky: název musí jednoznačně odpovídat rybě z trackeru, cena se čte 9× a všechna čtení se musí
shodnout, před kliknutím se ověří, že se seznam nepohnul, po prodeji se kontroluje hláška
„Úspěšně jsi prodal …“ a nárůst peněz. Cokoli nečekaného = zastavení.

## Vývoj

```bat
pip install -r requirements.txt
python run.py            :: GUI
python run.py --tk       :: záložní jednoduché GUI
python tests\test_offline.py --fetch   :: test čtení na screenshotech v tests\screens
build.bat                :: sestaví dist\FishaoSeller.exe
```

Neoficiální fanouškovský projekt, nijak nespojený s GamoVation ani s fishao-tracker.cz.
