"""Price data from https://fishao-tracker.cz/#market.

The site protects its database (Firebase App Check / reCAPTCHA), so we open the
page in a real browser window placed off-screen (Edge / Chrome / Brave already installed)
via the DevTools protocol and read the data the page itself loads.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

import websocket

from .config import CACHE_PATH

URL = "https://fishao-tracker.cz/?lang=cs#market"

BROWSER_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "~/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]

_READY_JS = ("(typeof allFishes!=='undefined' && allFishes.length>100 && typeof priceSummary==='object' "
             "&& Object.keys(priceSummary).length>100) ? Object.keys(priceSummary).length : 0")

_EXTRACT_JS = r"""JSON.stringify({
  fish: (function(){ var seen={}, out=[];
    allFishes.concat(typeof clubFishes!=='undefined'?clubFishes:[]).forEach(function(f){
      if(!seen[f.id]){ seen[f.id]=1; out.push({id:f.id, name:f.name, club:!!f.is_club, rating:f.rating}); } });
    return out; })(),
  summary: priceSummary,
  history: (typeof priceHistory==='object') ? priceHistory : {},
  estimated: (typeof estimatedPrices!=='undefined') ? estimatedPrices : {}
})"""


def find_browser():
    for c in BROWSER_CANDIDATES:
        p = os.path.expanduser(c)
        if os.path.isfile(p):
            return p
    return None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _CDP:
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.i = 0

    def call(self, method, **params):
        self.i += 1
        my = self.i
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message"))
                return msg.get("result", {})

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True)
        if "exceptionDetails" in r:
            return None
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def fetch(log=print, timeout=90):
    exe = find_browser()
    if not exe:
        raise RuntimeError("Nenašel jsem Chrome, Edge ani Brave. Nainstaluj jeden z nich (stačí mít nainstalovaný).")
    port = _free_port()
    prof = tempfile.mkdtemp(prefix="fishao_seller_")
    # Not --headless: reCAPTCHA Enterprise (Firebase App Check) rejects headless browsers with 403
    # and the prices never load. A normal window parked off-screen passes.
    args = [exe, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
            f"--user-data-dir={prof}", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--mute-audio", "--window-position=-32000,-32000",
            # macOS throttles windows it considers hidden/occluded -> the page would never finish
            "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--window-size=1280,800", "about:blank"]
    log(f"Načítám ceny z trackeru ({os.path.basename(exe)})…")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cdp = None
    try:
        t0 = time.time()
        ws_url = None
        while time.time() - t0 < 20 and not ws_url:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                    for t in json.load(r):
                        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                            ws_url = t["webSocketDebuggerUrl"]
                            break
            except Exception:
                time.sleep(0.3)
        if not ws_url:
            raise RuntimeError("Prohlížeč se nespustil.")
        cdp = _CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Page.navigate", url=URL)
        last, stable = 0, 0
        while time.time() - t0 < timeout:
            time.sleep(1.0)
            n = cdp.eval(_READY_JS) or 0
            if n and n == last:
                stable += 1
                if stable >= 2:
                    break
            else:
                stable = 0
            last = n
        else:
            state = cdp.eval("document.readyState + ' | ' + location.href + ' | allFishes=' + "
                             "(typeof allFishes!=='undefined' ? allFishes.length : '-') + ' | prices=' + "
                             "(typeof priceSummary==='object' ? Object.keys(priceSummary).length : '-')")
            raise RuntimeError(f"Tracker se nenačetl včas (zkontroluj internet). [{state}]")
        raw = cdp.eval(_EXTRACT_JS)
        if not raw:
            raise RuntimeError("Z trackeru se nepodařilo přečíst data.")
        data = _normalize(json.loads(raw))
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        log(f"Tracker: {len(data['fish'])} ryb, ceny u {sum(1 for v in data['prices'].values() if v)} ryb.")
        return data
    finally:
        if cdp:
            cdp.close()
        try:
            proc.kill()
            proc.wait(5)
        except Exception:
            pass
        shutil.rmtree(prof, ignore_errors=True)


def _yymmdd(s):
    s = str(s)
    return f"20{s[0:2]}-{s[2:4]}-{s[4:6]}" if len(s) == 6 else s


def _normalize(d):
    """-> {fetched_at, fish:[...], prices:{id:[[date,price],...]}, estimated:{id:{min,max}}}"""
    prices = {}
    for fid, entries in (d.get("history") or {}).items():
        lst = []
        for e in entries or []:
            try:
                lst.append([str(e["date"])[:10], int(e["price"])])
            except Exception:
                pass
        prices[str(fid)] = lst
    # fill from compact summary ("price:YYMMDD|...") where history is missing
    for fid, s in (d.get("summary") or {}).items():
        if prices.get(str(fid)):
            continue
        lst = []
        for part in str((s or {}).get("p", "")).split("|"):
            if ":" in part:
                pr, dt = part.split(":", 1)
                try:
                    lst.append([_yymmdd(dt), int(pr)])
                except ValueError:
                    pass
        prices[str(fid)] = lst
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fish": d.get("fish") or [],
        "prices": prices,
        "estimated": {str(k): v for k, v in (d.get("estimated") or {}).items()},
    }


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cache_age_min(data):
    try:
        t = datetime.fromisoformat(data["fetched_at"])
        return (datetime.now(timezone.utc) - t).total_seconds() / 60
    except Exception:
        return 1e9
