"""Simulated macOS test of the Mac version (runs on Windows, needs Windows OCR for the text part).

A fake game window shows the screenshots from tests/screens on a Retina (2x) "screen" and reacts
to the bot's mouse events: click on a sell button -> quantity dialog, click OK -> sale message,
wheel over the list -> next part of the list. Misplaced clicks are reported.

    python mac/tests/test_mac_sim.py
"""
import glob
import json
import os
import sys
import threading
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.dirname(HERE)
REPO = os.path.dirname(MAC)
sys.path.insert(0, HERE)
sys.path.insert(0, MAC)

import fake_macos  # noqa: E402

W = fake_macos.install()

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from fishao_seller import bot as botmod  # noqa: E402
from fishao_seller import config, macutil, ocr, tracker, vision  # noqa: E402
from fishao_seller.config import Settings  # noqa: E402

assert os.path.dirname(macutil.__file__).startswith(MAC), "imported the wrong package"

_win_ocr = fake_macos._win_ocr()
SHOTS = [cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
         for f in sorted(glob.glob(os.path.join(REPO, "tests", "screens", "*.png")))]


def _fit(img, h, w):
    """Screenshots differ by a few px; make them one window size (crop/pad right & bottom)."""
    img = img[:h, :w]
    return cv2.copyMakeBorder(img, 0, h - img.shape[0], 0, w - img.shape[1], cv2.BORDER_REPLICATE)


SHOTS = [_fit(x, *SHOTS[0].shape[:2]) for x in SHOTS]
LIST1, LIST2, QTY, SOLD = SHOTS
GAME_PID, MY_PID = 4242, os.getpid()
GX, GY = 120, 60                      # game window position in points
GH, GW = LIST1.shape[:2]

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   [{detail}]"))


def win(num, owner, title, pid, x=0, y=0, w=800, h=600, layer=0, onscreen=True, game=False):
    d = {"kCGWindowNumber": num, "kCGWindowOwnerName": owner, "kCGWindowOwnerPID": pid,
         "kCGWindowLayer": layer, "kCGWindowIsOnscreen": onscreen,
         "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}
    if title is not None:
        d["kCGWindowName"] = title
    if game:
        d["_game"] = True
    return d


def desktop(game_title="FISHAO - Reborn", game_in_front=False):
    """Menu bar on top, then our GUI, Discord + Chrome mentioning the game, the game itself."""
    game = win(77, "FISHAO - Reborn", game_title, GAME_PID, GX, GY, GW, GH, game=True)
    others = [
        win(1, "Window Server", "Menubar", 50, 0, 0, 1440, 24, layer=25),
        win(10, "Python", "Fishao Seller 1.0.0", MY_PID, 900, 100, 1000, 900),
        win(11, "Discord", "FISHAO - Reborn | #obchod", 300, 0, 0, 1200, 800),
        win(12, "Google Chrome", "FISHAO - Reborn – Google Chrome", 301, 0, 0, 1200, 800),
        win(13, "FISHAO - Reborn", "FISHAO - Reborn", GAME_PID, 0, 0, 120, 40),  # tiny helper window
    ]
    W.windows[:] = [others[0], game] + others[1:] if game_in_front else others + [game]
    return game


# ---- game simulation ---------------------------------------------------------------
class Game:
    def __init__(self):
        self.state = "list1"
        self.down = None
        self.log = []
        self.misclicks = []
        self.scrolls = []

    def image(self):
        return {"list1": LIST1, "list2": LIST2, "qty": QTY, "sold": SOLD}[self.state]

    def local(self, p):
        return p[0] - GX, p[1] - GY

    def inside(self, rects, p):
        x, y = self.local(p)
        return any(r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h for r in rects)

    def on_event(self, ev):
        kind = ev[0]
        if kind == "wheel":
            self.scrolls.append((ev[1], self.local(ev[2])))
            btns = vision.find_sell_buttons(self.image())
            x, y = self.local(ev[2])
            if not btns or not (0 < x < btns[0].x and btns[0].y - 50 < y < btns[-1].y + btns[-1].h + 50):
                self.misclicks.append(("wheel outside list", (x, y)))
            elif ev[1] < 0 and self.state == "list1":
                self.state = "list2"
            return
        if kind != "mouse":
            return
        _, what, p = ev
        if what == "kCGEventLeftMouseDown":
            self.down = p
        elif what == "kCGEventLeftMouseUp":
            d, self.down = self.down, None
            if d is None:
                self.misclicks.append(("up without down", p))
            elif abs(d[0] - p[0]) + abs(d[1] - p[1]) > 3:
                self.log.append(("drag", self.local(d), self.local(p)))
            else:
                self.click(p)

    def click(self, p):
        img = self.image()
        self.log.append(("click", self.state, self.local(p)))
        if self.state in ("list1", "list2"):
            if self.inside(vision.find_sell_buttons(img), p):
                self.state = "qty"
            else:
                self.misclicks.append(("list click not on a sell button", self.local(p)))
        elif self.state == "qty":
            if self.inside(vision.find_blue_buttons(img)[:1], p):
                self.state = "sold"
            else:
                self.misclicks.append(("dialog click not on OK", self.local(p)))
        elif self.state == "sold":
            if self.inside(vision.find_blue_buttons(img)[:1], p):
                self.state = "list1"
            else:
                self.misclicks.append(("message click not on OK", self.local(p)))


def run_bot(settings, method="run"):
    logs = []
    with open(os.path.join(REPO, "tracker_cache.json"), encoding="utf-8") as f:
        data = json.load(f)  # prices downloaded by the Windows version
    stop = threading.Event()
    b = botmod.Bot(settings, data, logs.append, stop)
    res = getattr(b, method)()
    return logs, res


def fast_settings(**kw):
    s = Settings()
    s.action_delay_ms = 40
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ---- tests -----------------------------------------------------------------------------
def test_ocr():
    print("OCR (Apple Vision wrapper, boxes flipped from bottom-left origin)")
    worst = 0
    for img in SHOTS:
        a = _win_ocr.read_lines(img)
        W.vision_settings.clear()
        b = ocr.read_lines(img)
        ka = sorted((l.text, l.x, l.y, l.w, l.h) for l in a)
        kb = sorted((l.text, l.x, l.y, l.w, l.h) for l in b)
        same_text = [t[0] for t in ka] == [t[0] for t in kb]
        worst = max([worst] + [max(abs(p - q) for p, q in zip(t[1:], u[1:])) for t, u in zip(ka, kb)])
        if not same_text:
            break
    check("same text lines as Windows OCR", same_text)
    check("same boxes (max 1 px rounding)", worst <= 1, f"max diff {worst}")
    cfg = W.vision_settings[-1]
    check("Czech requested", cfg.get("langs", [None])[0] == "cs-CZ", cfg)
    check("language correction off", cfg.get("correction") is False, cfg)
    check("min text height = 8 px", abs(cfg["min_h"] * cfg["img"][1] - 8) < 1e-6, cfg)
    check("empty image -> []", ocr.read_lines(np.zeros((0, 0, 3), np.uint8)) == [])
    gray = cv2.cvtColor(LIST1[:200, :600], cv2.COLOR_BGR2GRAY)
    check("grayscale input works", isinstance(ocr.read_lines(gray), list))
    ocr.check_available()
    check("check_available passes", True)


def test_windows():
    print("Finding the game window")
    desktop()
    check("game found among Discord/Chrome/own window/tiny helper", macutil.find_window("FISHAO - Reborn") == 77)
    desktop(game_title=None)  # no Screen Recording permission -> no window titles
    check("found by app name when titles are hidden", macutil.find_window("FISHAO - Reborn") == 77)
    check("case/space insensitive", macutil.find_window("  fishao - reborn ") == 77)
    check("nothing for unknown title", macutil.find_window("Neexistuje") is None)
    W.windows[:] = [w for w in W.windows if w["kCGWindowNumber"] != 77]
    check("no game running -> None", macutil.find_window("FISHAO - Reborn") is None)
    desktop()
    check("client_rect in points", macutil.client_rect(77) == (GX, GY, GW, GH), macutil.client_rect(77))
    check("closed window -> zero rect", macutil.client_rect(999) == (0, 0, 0, 0))
    check("game behind our GUI -> not foreground", not macutil.is_foreground(77))
    check("activate brings it to front", macutil.activate(77) and macutil.is_foreground(77))
    check("menu bar (other layer) on top does not count", W.windows[0]["kCGWindowLayer"] == 25)
    W.windows[2]["kCGWindowIsOnscreen"] = False
    check("hidden window = minimized", macutil.is_minimized(77))
    macutil.activate(77)
    check("activate un-minimizes", not macutil.is_minimized(77))


def test_permissions():
    print("Permissions")
    W.screen_access, W.ax_trusted, W.prompts[:] = False, False, []
    try:
        macutil.check_permissions()
        check("missing permissions raise", False)
    except RuntimeError as e:
        msg = str(e)
        check("missing permissions raise", True)
        check("message names both permissions", "Nahrávání obrazovky" in msg and "Zpřístupnění" in msg, msg)
        check("system prompts requested", W.prompts == ["screen", "ax"], W.prompts)
    W.screen_access, W.ax_trusted = True, True
    macutil.check_permissions()
    check("granted -> ok", True)


def test_input():
    print("Retina capture + mouse coordinates")
    desktop(game_in_front=True)
    W.screen = lambda: LIST1
    cap = macutil.Capturer(77)
    img, origin = cap.grab()
    check("capture is 2x (Retina)", img.shape[:2] == (GH * 2, GW * 2), img.shape)
    check("BGR 3 channels", img.ndim == 3 and img.shape[2] == 3)
    check("origin in pixels", origin == (GX * 2, GY * 2), origin)
    W.events.clear()
    macutil.click(origin[0] + 200, origin[1] + 100)
    pts = [e[2] for e in W.events if e[0] == "mouse"]
    check("click lands on the same spot in points", pts and all(p == (GX + 100.0, GY + 50.0) for p in pts), pts)
    check("click = move, down, up", [e[1] for e in W.events] ==
          ["kCGEventMouseMoved", "kCGEventLeftMouseDown", "kCGEventLeftMouseUp"])
    W.events.clear()
    macutil.wheel(origin[0] + 10, origin[1] + 10, -2)
    wh = [e for e in W.events if e[0] == "wheel"]
    check("wheel down = 2 negative events", len(wh) == 2 and all(e[1] < 0 for e in wh), wh)
    W.events.clear()
    macutil.drag(origin[0], origin[1], origin[0], origin[1] + 40)
    kinds = [e[1] for e in W.events if e[0] == "mouse"]
    check("drag = down, dragged..., up", kinds[1] == "kCGEventLeftMouseDown" and kinds[-1] == "kCGEventLeftMouseUp"
          and "kCGEventLeftMouseDragged" in kinds)
    check("drag ends 20 pt lower", W.events[-1][2] == (float(GX), GY + 20.0), W.events[-1])
    W.events.clear()
    macutil.press_escape()
    check("escape key down+up", W.events == [("key", 53, True), ("key", 53, False)])
    W.cursor = (500.0, 500.0)
    check("no stop normally", not macutil.stop_key_pressed())
    W.keys_down.add(111)
    check("F12 stops", macutil.stop_key_pressed())
    W.keys_down.clear()
    W.cursor = (0.0, 0.0)
    check("mouse in top-left corner stops", macutil.stop_key_pressed())
    W.cursor = (500.0, 500.0)
    W.windows[1]["kCGWindowIsOnscreen"] = False
    try:
        cap.grab()
        check("minimized game -> clear error", False)
    except RuntimeError as e:
        check("minimized game -> clear error", "minimalizované" in str(e))
    W.windows[1]["kCGWindowIsOnscreen"] = True
    cap.close()


def test_bot_preview():
    print("Bot: Test detekce")
    desktop()
    g = Game()
    W.screen, W.on_event = g.image, g.on_event
    logs, p = run_bot(fast_settings(), "preview")
    check("preview image saved", os.path.isfile(p), p)
    check("game was activated", W.activated and W.activated[-1] == GAME_PID)
    rows = [l for l in logs if l.startswith("  • ")]
    check("3 fish read", len(rows) == 3, logs)
    check("Afrotetra ×2 @ 46", any("Afrotetra" in r and "×2 @ 46" in r for r in rows), rows)
    check("no clicks during preview", not any(e[0] == "mouse" and e[1] != "kCGEventMouseMoved" for e in W.events))


def test_bot_dry_run():
    print("Bot: START nanečisto (whole list, scrolling)")
    desktop()
    g = Game()
    W.screen, W.on_event = g.image, g.on_event
    botmod.market_closed_today = lambda: False
    logs, sold = run_bot(fast_settings(dry_run=True))
    decided = [l for l in logs if l[:1] in "✔·"]
    check("finished the list", any("Hotovo" in l for l in logs), logs[-5:])
    check("6 fish decided over two screens", len(decided) == 6, decided)
    check("scrolled down to the second screen", g.scrolls and g.scrolls[0][0] < 0)
    check("no misplaced clicks/scrolls", not g.misclicks, g.misclicks)
    check("nothing clicked in dry run", not [x for x in g.log if x[0] == "click"], g.log)
    check("no error in log", not any(l.startswith("✖") for l in logs), [l for l in logs if l.startswith("✖")])


def test_bot_sale():
    print("Bot: ostrý prodej (click sell -> dialog -> OK -> message)")
    desktop()
    g = Game()
    W.screen, W.on_event = g.image, g.on_event
    botmod.market_closed_today = lambda: False
    s = fast_settings(dry_run=False, always_sell=["Afrotetra Forskållova"])
    logs, _ = run_bot(s)
    clicks = [x for x in g.log if x[0] == "click"]
    check("clicked sell button, then OK in the dialog", [c[1] for c in clicks][:2] == ["list1", "qty"], g.log)
    check("all clicks on target", not g.misclicks, g.misclicks)
    check("read the quantity dialog", any("v dialogu" in l for l in logs) or len(clicks) >= 2, logs)
    # The saved sale-message screenshot is for a different fish (Klaun očkatý) -> the safety check must stop it.
    check("safety stop on wrong fish in the game message",
          any("Klaun" in l and "Zastavuji" in l for l in logs), [l for l in logs if l.startswith("✖")])


def test_misc():
    print("Config / tracker / GUI API")
    import importlib
    real = os.path.isfile
    os.path.isfile = lambda p: p == "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    try:
        check("finds Edge on macOS", tracker.find_browser() == "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    finally:
        os.path.isfile = real
    sys.frozen = True
    try:
        d = importlib.reload(config).app_dir()
        check("built app stores data in Application Support",
              d.replace("\\", "/").endswith("Library/Application Support/Fishao Seller"), d)
    finally:
        del sys.frozen
        importlib.reload(config)
        os.removedirs(d) if os.path.isdir(d) and not os.listdir(d) else None

    sys.modules["webview"] = types.SimpleNamespace(create_window=None, start=None)
    from fishao_seller import webview_app
    opened = []
    real_popen = macutil.subprocess.Popen
    macutil.subprocess.Popen = lambda args: opened.append(args)
    try:
        api = webview_app.Api()
        st = api.get_state()
        check("GUI state", st["version"] == webview_app.__version__ and "settings" in st)
        desktop()
        check("GUI find_window", api.find_window() == {"found": True, "w": GW, "h": GH})
        api.open_logs()
        check("open logs uses `open`", opened and opened[0][0] == "open", opened)
        check("ui files present", os.path.isfile(webview_app.ui_path("index.html")))
    finally:
        macutil.subprocess.Popen = real_popen


if __name__ == "__main__":
    for t in (test_ocr, test_windows, test_permissions, test_input, test_bot_preview, test_bot_dry_run,
              test_bot_sale, test_misc):
        try:
            t()
        except Exception:
            traceback.print_exc()
            check(t.__name__ + " crashed", False)
    bad = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} OK" + (f", FAILED: {bad}" if bad else ""))
    sys.exit(1 if bad else 0)
