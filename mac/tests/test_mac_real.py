"""Test of the Mac version on a real Mac (GitHub Actions macos runner or your own Mac).

1. Real Apple Vision OCR + screen reading on tests/screens, at 1x and Retina 2x, compared with
   what the Windows version reads from the same screenshots.
2. Real Quartz / AppKit calls: window list, activation, capture, mouse click delivery, F12 state,
   permission checks - against a stand-in "game" window opened in a separate process.
3. Tracker download through Chrome/Edge (informational).

    python3 mac/tests/test_mac_real.py [--out DIR]

FAIL = the code is wrong. WARN = could not be checked here (e.g. a permission the CI machine
does not grant); it does not fail the run.
"""
import glob
import json
import os
import subprocess
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.dirname(HERE)
REPO = os.path.dirname(MAC)
sys.path.insert(0, MAC)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from fishao_seller import macutil, ocr, tracker, vision  # noqa: E402
from fishao_seller.pricing import Catalog  # noqa: E402

OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(MAC, "logs", "real_test")
os.makedirs(OUT, exist_ok=True)
results = []


def check(name, cond, detail="", warn=False):
    status = "OK" if cond else ("WARN" if warn else "FAIL")
    results.append((name, status))
    print(f"  {status:<5}{name}" + ("" if cond else f"   [{detail}]"), flush=True)


# What the Windows version reads from tests/screens (1x).
EXPECTED = {
    "192411": {"fb": 2239, "cards": [("Afrotetra Forskållova", 2, 46), ("Chrochtal modropásý", 7, 4),
                                     ("Havýš plástevný", 1, 30)], "thumb": True},
    "192435": {"fb": 2239, "cards": [("Ostenec sargasový", 4, 33), ("Sardinka oblá", 41, 4),
                                     ("Vranka mnohoostná", 1, 31)], "thumb": True},
    "192459": {"fb": 2239, "qty": 2},
    "192513": {"sold": "Klaun očkatý"},
}
NAMES = ["Afrotetra Forskållova", "Chrochtal modropásý", "Havýš plástevný", "Ostenec sargasový",
         "Sardinka oblá", "Vranka mnohoostná", "Klaun očkatý",
         # look-alikes so matching has to be precise
         "Sardinka obecná", "Vranka obecná", "Klaun oranžový", "Afrotetra zlatá", "Chrochtal skvrnitý"]


def catalog():
    try:
        with open(os.path.join(REPO, "tracker_cache.json"), encoding="utf-8") as f:
            return Catalog(json.load(f)), "tracker_cache.json"
    except Exception:
        fish = [{"id": i, "name": n, "club": False, "rating": 1} for i, n in enumerate(NAMES)]
        return Catalog({"fish": fish, "prices": {}, "estimated": {}}), "built-in list of names"


def read_screen(img, cat):
    lines = ocr.read_lines(img)
    r = {"fb": vision.read_fishbucks(img), "cards": [], "lines": [l.text for l in lines]}
    btns = vision.find_sell_buttons(img)
    for b in btns:
        c = vision.read_card(img, b)
        m = cat.match(c.name)
        r["cards"].append((m.fish["name"] if m.fish else f"?{c.name_raw}", c.count, c.price))
    r["thumb"] = bool(btns and vision.find_scroll_thumb(img, btns))
    blues = vision.find_blue_buttons(img)
    if blues:
        t = vision.find_line(lines, "prodat", "kus")
        r["qty"] = vision.read_qty_dialog(img, t, blues[0]) if t else None
        r["sold"] = vision.parse_sold_name(lines)
    return r


def test_vision_ocr():
    print("Apple Vision OCR + screen reading", flush=True)
    ocr.check_available()
    langs = ocr._languages()
    check("Czech OCR model available on this macOS", bool(langs and langs[0].startswith("cs")),
          f"macOS {os.uname().release}, languages {langs} - will read without diacritics", warn=True)
    cat, src = catalog()
    print(f"  (names from {src})")
    for f in sorted(glob.glob(os.path.join(REPO, "tests", "screens", "*.png"))):
        key = os.path.basename(f)[-10:-4]
        exp = EXPECTED[key]
        img1 = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
        for scale in (1, 2):
            img = img1 if scale == 1 else cv2.resize(img1, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            t0 = time.time()
            r = read_screen(img, cat)
            tag = f"{key} @{scale}x"
            with open(os.path.join(OUT, f"ocr_{key}_{scale}x.txt"), "w", encoding="utf-8") as fh:
                fh.write("\n".join(r["lines"]))
            if "fb" in exp:
                check(f"{tag}: Fishbucks {exp['fb']}", r["fb"] == exp["fb"], r["fb"])
            if "cards" in exp:
                check(f"{tag}: fish names/counts/prices", r["cards"] == exp["cards"], r["cards"])
                check(f"{tag}: scrollbar found", r["thumb"] == exp["thumb"])
            if "qty" in exp:
                check(f"{tag}: quantity dialog = {exp['qty']}", r.get("qty") == exp["qty"], r.get("qty"))
            if "sold" in exp:
                check(f"{tag}: sale message names {exp['sold']}", r.get("sold") is not None and
                      cat.names_match(r["sold"], exp["sold"]), r.get("sold"))
            print(f"         ({time.time() - t0:.1f}s)", flush=True)


CHILD = r"""
import sys, tkinter as tk
from PIL import Image, ImageTk
img = Image.open(sys.argv[1])
root = tk.Tk(); root.title("FISHAO - Reborn")
sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
k = min(1.0, (sw - 160) / img.width, (sh - 160) / img.height)
img = img.resize((int(img.width * k), int(img.height * k)))
ph = ImageTk.PhotoImage(img)
c = tk.Canvas(root, width=img.width, height=img.height, highlightthickness=0, bd=0); c.pack()
c.create_image(0, 0, image=ph, anchor="nw")
c.bind("<Button-1>", lambda e: print("CLICK", e.x, e.y, flush=True))
c.bind("<MouseWheel>", lambda e: print("WHEEL", e.delta, flush=True))
root.geometry(f"+80+80")
root.after(800, lambda: print("READY", img.width, img.height, c.winfo_rootx(), c.winfo_rooty(), sw, sh, flush=True))
root.lift(); root.attributes("-topmost", True); root.after(1500, lambda: root.attributes("-topmost", False))
root.mainloop()
"""


def _readline(p, prefix, timeout=10):
    import select
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([p.stdout], [], [], 0.2)
        if r:
            line = p.stdout.readline().strip()
            if line.startswith(prefix):
                return line.split()[1:]
    return None


def test_quartz():
    print("Quartz / AppKit against a stand-in game window", flush=True)
    import Quartz
    scr = Quartz.CGPreflightScreenCaptureAccess()
    import ApplicationServices as AS
    ax = AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: False})
    print(f"  (Screen Recording granted: {bool(scr)}, Accessibility granted: {bool(ax)})")
    try:
        macutil.check_permissions()
        check("check_permissions passes", True)
    except RuntimeError as e:
        check("check_permissions explains what is missing", "Soukromí a zabezpečení" in str(e), str(e))

    check("F12 state readable", macutil.stop_key_pressed() in (False, True))
    shot = sorted(glob.glob(os.path.join(REPO, "tests", "screens", "*.png")))[0]
    p = subprocess.Popen([sys.executable, "-c", CHILD, shot], stdout=subprocess.PIPE, text=True)
    try:
        ready = _readline(p, "READY", 20)
        check("stand-in game window opened", ready is not None)
        if not ready:
            return
        iw, ih, rx, ry, sw, sh = map(int, ready)
        print(f"  (screen {sw}x{sh} pt, window content {iw}x{ih} at {rx},{ry})")
        wid = None
        for w in macutil._all_windows():
            if int(w.get(Quartz.kCGWindowOwnerPID, -1)) == p.pid and w.get(Quartz.kCGWindowLayer) == 0:
                wid = int(w[Quartz.kCGWindowNumber])
        check("window listed by CGWindowListCopyWindowInfo", wid is not None)
        if wid is None:
            return
        info = macutil._info(wid)
        title = str(info.get(Quartz.kCGWindowName) or "")
        check("window title readable", title == "FISHAO - Reborn", f"got {title!r}", warn=not scr)
        x, y, w, h = macutil.client_rect(wid)
        check("client_rect matches the window", abs(x - rx) <= 2 and w >= iw and h >= ih and y <= ry,
              (x, y, w, h, rx, ry, iw, ih))
        check("not minimized", not macutil.is_minimized(wid))
        ok = macutil.activate(wid)
        check("activate -> foreground", ok and macutil.is_foreground(wid), ok, warn=True)

        cap = macutil.Capturer(wid)
        img, origin = cap.grab()
        cap.close()
        cv2.imwrite(os.path.join(OUT, "capture.png"), img)
        k = img.shape[1] / w
        print(f"  (capture {img.shape[1]}x{img.shape[0]} px = scale {k:.2f})")
        check("capture size = window size x scale", abs(img.shape[0] - h * k) <= 2, (img.shape, w, h, k))
        check("origin scaled to pixels", origin == (round(x * k), round(y * k)), (origin, x, y, k))
        src = cv2.imread(shot)
        src = cv2.resize(src, (int(iw * k), int(ih * k)), interpolation=cv2.INTER_AREA)
        oy, ox = int(round((ry - y) * k)), int(round((rx - x) * k))
        crop = img[oy:oy + src.shape[0], ox:ox + src.shape[1]]
        diff = float(np.mean(cv2.absdiff(crop, src[:crop.shape[0], :crop.shape[1]]))) if crop.size else 255
        check("captured pixels = what the window shows", diff < 12, f"mean diff {diff:.1f}", warn=not scr)

        if diff < 12:
            btns = vision.find_sell_buttons(img)
            check("sell buttons found in the live capture", len(btns) == 3, len(btns), warn=True)

        # click 1/3 into the content -> Tk must report the same content coordinate
        tx, ty = iw // 3, ih // 2
        macutil.click(origin[0] + ((rx - x) + tx) * k, origin[1] + ((ry - y) + ty) * k)
        got = _readline(p, "CLICK", 3)
        check("click delivered to the right spot", got is not None and abs(int(got[0]) - tx) <= 2
              and abs(int(got[1]) - ty) <= 2, f"sent {tx},{ty} got {got}", warn=not ax)
        macutil.wheel(origin[0] + ((rx - x) + tx) * k, origin[1] + ((ry - y) + ty) * k, -2)
        got = _readline(p, "WHEEL", 3)
        check("mouse wheel delivered (down = negative)", got is not None and int(got[0]) < 0, got, warn=not ax)
    finally:
        p.kill()


def test_tracker():
    print("Tracker download (Chrome/Edge)", flush=True)
    exe = tracker.find_browser()
    check("browser found", exe is not None, "no Chrome/Edge/Brave", warn=True)
    if not exe:
        return
    try:
        d = tracker.fetch(log=lambda m: print("   ", m, flush=True), timeout=90)
        check("tracker prices downloaded", len(d["fish"]) > 100, len(d["fish"]))
    except Exception as e:
        check("tracker prices downloaded", False, e, warn=True)


if __name__ == "__main__":
    for t in (test_vision_ocr, test_quartz, test_tracker):
        try:
            t()
        except Exception:
            traceback.print_exc()
            check(t.__name__ + " crashed", False)
    fails = [n for n, s in results if s == "FAIL"]
    warns = [n for n, s in results if s == "WARN"]
    print(f"\nOK {sum(s == 'OK' for _, s in results)}, WARN {len(warns)}, FAIL {len(fails)}")
    with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    sys.exit(1 if fails else 0)
