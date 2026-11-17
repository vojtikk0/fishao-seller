import sys


def selftest():
    """Used to verify the packaged .exe: OCR + digit reading on a generated image."""
    import os
    import cv2
    import numpy as np
    from fishao_seller import ocr, vision, tracker
    from fishao_seller.webview_app import ui_path, webview2_available
    img = np.full((120, 700, 3), 255, np.uint8)
    cv2.putText(img, "Sardinka obla (41)", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 3)
    print("OCR:", [l.text for l in ocr.read_lines(img)])
    print("glyph:", vision._GLYPH3.shape)
    print("browser:", tracker.find_browser())
    print("ui:", os.path.isfile(ui_path("index.html")), os.path.isfile(ui_path("vendor", "Phosphor.woff2")))
    print("webview2:", webview2_available())


def main():
    if "--selftest" in sys.argv:
        return selftest()
    use_tk = "--tk" in sys.argv
    if not use_tk:
        try:
            from fishao_seller.webview_app import main as wv_main, webview2_available
            if webview2_available():
                return wv_main()
        except Exception:
            pass
    # fallback: classic window (no WebView2 runtime)
    from fishao_seller.app import main as tk_main
    tk_main()


if __name__ == "__main__":
    main()
