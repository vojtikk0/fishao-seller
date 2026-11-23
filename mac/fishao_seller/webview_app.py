"""GUI from the claude.ai/design "Fishao Seller" design (Nocturne), rendered with WKWebView (macOS)."""
import base64
import json
import os
import queue
import sys
import threading
import time
from dataclasses import asdict, fields

import cv2
import numpy as np
import webview

from . import __version__, errlog, macutil, tracker
from .config import LOG_DIR, Settings


def ui_path(*p):
    base = getattr(sys, "_MEIPASS", None)
    root = os.path.join(base, "fishao_seller") if base else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, "ui", *p)


class Api:
    """Methods callable from JS as pywebview.api.<name>(). Private attrs (_x) are not exposed."""

    def __init__(self):
        self._s = Settings.load()
        self._data = tracker.load_cache()
        self._stop = threading.Event()
        self._worker = None
        self._running = False
        self._q = queue.Queue()
        self._history = []
        self._window = None
        self._maxed = False

    # ---- plumbing --------------------------------------------------------------
    def _log(self, msg):
        t = time.strftime("%H:%M:%S")
        self._history.append((t, msg))
        self._history = self._history[-500:]
        self._q.put(("log", (t, msg)))

    def _pump(self):
        while True:
            kind, val = self._q.get()
            if self._window is None:
                continue
            try:
                if kind == "log":
                    js = f"window.onLog({json.dumps(val[0])},{json.dumps(val[1])})"
                elif kind == "status":
                    js = f"window.onStatus({json.dumps(self._status())})"
                else:
                    js = f"window.onPreview({json.dumps(val)})"
                self._window.evaluate_js(js)
            except Exception:
                pass

    def _status(self):
        busy = bool(self._worker and self._worker.is_alive())
        if self._running:
            text = "Běží nanečisto…" if self._s.dry_run else "Prodává…"
        elif busy:
            text = "Pracuji…"
        else:
            text = "Připraveno"
        if self._data:
            age = tracker.cache_age_min(self._data)
            data = f"Ceny z trackeru: {len(self._data.get('fish', []))} ryb, staženo před {age:.0f} min."
        else:
            data = "Ceny z trackeru zatím nestaženy."
        return {"running": self._running, "busy": busy, "statusText": text, "dataText": data}

    def _push_status(self):
        self._q.put(("status", None))

    def _run_bg(self, fn, running=False):
        if self._worker and self._worker.is_alive():
            self._log("Už něco běží – počkej, nebo dej STOP.")
            return False
        self._stop.clear()
        self._running = running

        def wrap():
            try:
                fn()
            except Exception as e:
                msg = errlog.user_message(e) or errlog.record(e, "webview")
                self._log(f"✖ {msg}" if errlog.user_message(e) else f"✖ Chyba: {msg}")
            finally:
                self._running = False
                self._push_status()

        self._worker = threading.Thread(target=wrap, daemon=True)
        self._worker.start()
        self._push_status()
        return True

    def _ensure_data(self, force=False):
        if force or not self._data or tracker.cache_age_min(self._data) > self._s.tracker_max_age_min:
            self._data = tracker.fetch(log=self._log)
            self._push_status()
        return self._data

    # ---- JS API ----------------------------------------------------------------
    def get_state(self):
        return {"settings": asdict(self._s), "version": __version__, "status": self._status(),
                "logs": self._history}

    def save_settings(self, d):
        if not isinstance(d, dict):
            return False
        defaults = Settings()
        for f in fields(Settings):
            if f.name not in d:
                continue
            v, ref = d[f.name], getattr(defaults, f.name)
            try:
                if isinstance(ref, bool):
                    v = bool(v)
                elif isinstance(ref, int):
                    v = int(float(v))
                elif isinstance(ref, float):
                    v = float(v)
                elif isinstance(ref, list):
                    v = [str(x).strip() for x in (v or []) if str(x).strip()]
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            setattr(self._s, f.name, v)
        self._s.window_title = self._s.window_title.strip() or "FISHAO - Reborn"
        self._s.percentile = min(100.0, self._s.percentile)
        self._s.pct_in_range = min(100.0, self._s.pct_in_range)
        self._s.save()
        return True

    def find_window(self):
        hwnd = macutil.find_window(self._s.window_title)
        if not hwnd:
            return {"found": False}
        _, _, w, h = macutil.client_rect(hwnd)
        return {"found": True, "w": w, "h": h}

    def fetch(self):
        self._run_bg(lambda: self._ensure_data(force=True))

    def test(self):
        def job():
            from .bot import Bot
            data = self._ensure_data()
            self._log("— Test detekce —")
            p = Bot(self._s, data, self._log, self._stop).preview()
            self._log(f"Náhled uložen: {p}")
            img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
            k = min(1.0, 1400 / img.shape[1])
            if k < 1:
                img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self._q.put(("preview", "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()))
        self._run_bg(job)

    def start(self):
        def job():
            from .bot import Bot
            data = self._ensure_data()
            self._log("— START —  (F12 = stop)")
            Bot(self._s, data, self._log, self._stop).run()
        self._run_bg(job, running=True)

    def stop(self):
        self._stop.set()
        self._log("Zastavuji…")

    def open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        macutil.open_folder(LOG_DIR)

    # ---- custom title bar ------------------------------------------------------
    def win_minimize(self):
        self._window.minimize()

    def win_toggle_max(self):
        if self._maxed:
            self._window.restore()
        else:
            self._window.maximize()
        self._maxed = not self._maxed

    def win_close(self):
        self._stop.set()
        self._window.destroy()


def main():
    api = Api()
    api._log("Připraveno." + ("" if api._data else " Ceny z trackeru zatím nestaženy."))
    win = webview.create_window(
        f"Fishao Seller {__version__}", url=ui_path("index.html"), js_api=api,
        width=1000, height=900, min_size=(880, 640), frameless=True, easy_drag=False,
        background_color="#161826")
    api._window = win
    threading.Thread(target=api._pump, daemon=True).start()
    webview.start(private_mode=True)
