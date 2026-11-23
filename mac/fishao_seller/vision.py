"""Reading the shop window ("NAŠE DNEŠNÍ CENY") from a screenshot.

Everything is relative to the size of detected elements, so it works in any
resolution / window size.
"""
import base64
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from . import ocr

# Glyph "3" in the game's button font (binarised, black on white). It is put in
# front of a price before OCR, because Windows OCR can't read a lone digit.
_GLYPH3_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAxCAAAAACem/QWAAAA5klEQVQ4EXXBgQGCMADAsPb/oyvCBkMhMe6MG2OSS0zGQe7iYOzkV+yMjTyI"
    "LwPkUWwM5EWAgbwJDHkVGDLFTqbAkCEGGQJDDnGSIQzZxUWGMOQrFjKEAUKsZAjjn0xh/JMpjD8yBcYvGWJj3MglNsZCFvFlXGQRO+MilzgY"
    "J1nEwTjJIg7GSX4EGAu5C4xfcgnjgUwZT2TIeCSD8UgG45EMBgJxJ4MhX3Ejg8khFjKZDHGRSU5xkpNcYpKTIVMc5GLIImQRhrwKDORNYIA8"
    "CzA28iQ2xpf8iZ1xkLs4GJMsYjAWEj8+NFhkC5lJynMAAAAASUVORK5CYII="
)
_GLYPH3 = cv2.imdecode(np.frombuffer(base64.b64decode(_GLYPH3_B64), np.uint8), cv2.IMREAD_GRAYSCALE)


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self):
        return self.x + self.w // 2

    @property
    def cy(self):
        return self.y + self.h // 2


@dataclass
class Card:
    button: Rect
    name_raw: str = ""
    name: str = ""
    count: Optional[int] = None
    price: Optional[int] = None
    price_detail: str = ""


def deaccent(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---- element detection -------------------------------------------------------
def _rects(mask, img_h, min_ar, max_ar, min_fill):
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if h < img_h * 0.025 or h > img_h * 0.10:
            continue
        if min_ar < w / h < max_ar and a / (w * h) > min_fill:
            out.append(Rect(x, y, w, h))
    return sorted(out, key=lambda r: (r.y, r.x))


def find_sell_buttons(img):
    """Yellow-orange 'Prodat' buttons (only bright = not dimmed by a dialog)."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (10, 170, 215), (28, 255, 255))
    btns = _rects(m, img.shape[0], 1.5, 3.5, 0.55)
    if len(btns) < 2:
        return btns
    # all sell buttons share a column and size; drop outliers
    med_x = int(np.median([b.x for b in btns]))
    med_h = int(np.median([b.h for b in btns]))
    return [b for b in btns if abs(b.x - med_x) <= med_h * 0.5 and abs(b.h - med_h) <= med_h * 0.25]


def find_blue_buttons(img):
    """Blue 'OK' buttons of popup dialogs."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (95, 150, 150), (115, 255, 255))
    return _rects(m, img.shape[0], 1.6, 4.0, 0.6)


# ---- numbers -------------------------------------------------------------------
def _binarize(crop, scale, dark: bool):
    c = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if dark:
        g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        m = cv2.inRange(g, 0, 110)
    else:
        hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
        m = cv2.inRange(hsv, (0, 0, 200), (180, 110, 255))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    H, W = m.shape
    keep = np.zeros_like(m)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if x == 0 or y == 0 or x + w >= W or y + h >= H:
            continue  # touches crop border -> frame / highlight, not a digit
        if h < H * 0.3:
            continue
        keep[lab == i] = 255
    ys, xs = np.where(keep > 0)
    if len(xs) == 0:
        return None
    keep = keep[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return 255 - keep


def _ocr_number_once(b):
    """b: black-on-white digits. Returns int or None. Uses '3'-prefix and 3x repetition."""
    H = b.shape[0]
    if H < 8:
        return None, "malé"
    p = cv2.resize(_GLYPH3, (max(1, round(_GLYPH3.shape[1] * H / _GLYPH3.shape[0])), H), interpolation=cv2.INTER_AREA)
    p = np.where(p > 127, 255, 0).astype(np.uint8)
    sp = np.full((H, max(2, H // 8)), 255, np.uint8)
    gap = np.full((H, H), 255, np.uint8)
    unit = np.hstack([p, sp, b])
    comp = np.hstack([unit, gap, unit, gap, unit])
    comp = cv2.copyMakeBorder(comp, H, H, H, H, cv2.BORDER_CONSTANT, value=255)
    lines = ocr.read_lines(comp)
    txt = " ".join(l.text for l in lines)
    toks = txt.split()
    if len(toks) != 3 or len(set(toks)) != 1:
        return None, txt
    t = toks[0]
    if not re.fullmatch(r"3\d+", t):
        return None, txt
    return int(t[1:]), txt


def read_number(crop, dark=False):
    """Read an integer; every scale must agree, otherwise None (= unreliable)."""
    vals, details = [], []
    for s in (2, 3, 4):
        b = _binarize(crop, s, dark)
        if b is None:
            return None, "žádné číslice"
        v, d = _ocr_number_once(b)
        details.append(d)
        if v is None:
            return None, " | ".join(details)
        vals.append(v)
    if len(set(vals)) != 1:
        return None, f"nesouhlasí {vals}"
    return vals[0], "ok"


# ---- cards ---------------------------------------------------------------------
_NAME_RE = re.compile(r"^(.*?)\s*[\(\[{]\s*([0-9lIi|!oO]+)\s*[\)\]}]?\s*$")


def _parse_count(s):
    s = s.translate(str.maketrans({"l": "1", "I": "1", "i": "1", "|": "1", "!": "1", "o": "0", "O": "0"}))
    return int(s) if s.isdigit() else None


def name_region(img, b: Rect):
    H, W = img.shape[:2]
    x0 = max(0, b.x - int(9.5 * b.h))
    x1 = min(W, b.x + b.w + int(0.3 * b.h))
    y0 = max(0, b.y - 2 * b.h)
    y1 = max(0, b.y - int(0.7 * b.h))
    return x0, y0, x1, y1


def _name_fill(crop, scale=1.0, bold=False):
    """Fish names are yellow with a brown outline; Apple Vision reads them much better as plain
    black text on white (measured on a real Mac: 12/12 cards vs 5/12 from the raw crop)."""
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    m = cv2.inRange(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV), (10, 110, 150), (40, 255, 255))
    if bold:
        k = max(2, round(crop.shape[0] / 28))
        m = cv2.dilate(m, np.ones((k, k), np.uint8))
    g = cv2.copyMakeBorder(255 - m, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def _name_attempts(crop):
    yield _name_fill(crop, 1.5)
    yield _name_fill(crop, 2.0)
    yield _name_fill(crop, 1.0, bold=True)
    yield _name_fill(crop, 3.0)
    yield crop
    yield cv2.resize(crop, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)


def read_card(img, b: Rect) -> Card:
    card = Card(button=b)
    x0, y0, x1, y1 = name_region(img, b)
    if y1 - y0 < b.h * 0.8:
        return card
    crop = img[y0:y1, x0:x1]
    for attempt in _name_attempts(crop):
        lines = ocr.read_lines(attempt)
        for ln in lines:
            m = _NAME_RE.match(ln.text.strip())
            if m and len(m.group(1)) >= 3:
                card.name_raw = ln.text.strip()
                card.name = m.group(1).strip(" -—–.,:")
                card.count = _parse_count(m.group(2))
                break
        if card.name and card.count:
            break
    pcrop = img[b.y:b.y + b.h, b.x:b.x + int(b.w * 0.58)]
    card.price, card.price_detail = read_number(pcrop, dark=False)
    return card


def card_signature(img, b: Rect):
    x0, y0, x1, y1 = name_region(img, b)
    return cv2.cvtColor(img[y0:b.y + b.h, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16)


def same_region(a, b, tol=6.0) -> bool:
    if a is None or b is None or a.shape != b.shape:
        return False
    return float(np.mean(np.abs(a - b))) < tol


def list_signature(img, buttons):
    """Grayscale crop of the list column, used to detect whether scrolling moved anything."""
    if not buttons:
        return None
    b = buttons[0]
    H = img.shape[0]
    x0 = max(0, b.x - int(9.5 * b.h))
    x1 = b.x + b.w
    y0 = max(0, min(bb.y for bb in buttons) - 3 * b.h)
    y1 = min(H, max(bb.y for bb in buttons) + 2 * b.h)
    return cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.int16), (x0, y0, x1, y1)


def find_scroll_thumb(img, buttons) -> Optional[Rect]:
    """Bright vertical scrollbar thumb right of the sell buttons.

    Just right of the track runs a 1 px bright highlight of the panel border, as tall as
    a short thumb – it must never win, or the drag grabs empty space next to the bar.
    So the thumb has to be several px wide, and the nearest such bar to the list wins."""
    if not buttons:
        return None
    b = buttons[0]
    H, W = img.shape[:2]
    x0 = min(W - 1, b.x + b.w + int(0.1 * b.h))
    x1 = min(W, b.x + b.w + int(1.3 * b.h))
    # the thumb can sit at the very top/bottom of the track, beyond the visible buttons
    y0 = max(0, min(bb.y for bb in buttons) - 5 * b.h)
    y1 = min(H, max(bb.y for bb in buttons) + 4 * b.h)
    hsv = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 0, 235), (180, 45, 255))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    min_w = max(3, int(0.08 * b.h))
    best = None
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in st[i])
        if h < 1.0 * b.h or not (min_w <= w < 0.5 * b.h) or a < 0.6 * w * h:
            continue
        if x == 0:  # cut-off right edge of a fish card, not the scrollbar
            continue
        r = Rect(x0 + x, y0 + y, w, h)
        if best is None or r.x < best.x or (r.x == best.x and r.h > best.h):
            best = r
    return best


# ---- screen-level reads --------------------------------------------------------
def shop_is_open(lines) -> bool:
    for l in lines:
        d = deaccent(l.text)
        if "dnesni" in d or ("nase" in d and "eny" in d):
            return True
    return False


def find_line(lines, *needles):
    for l in lines:
        d = deaccent(l.text)
        if all(n in d for n in needles):
            return l
    return None


def read_fishbucks(img) -> Optional[int]:
    """Fishbucks from the top HUD: the light pill with the green banknote icon on its left.

    Only the digits inside that pill are read, so neither the icon nor the coins
    pill can leak into the number. Returns None when it can't be read reliably.
    """
    H = img.shape[0]
    top = img[0:int(H * 0.1), :].copy()
    # the HUD is dimmed while a dialog is open / fading out -> stretch brightness
    hi = float(np.percentile(cv2.cvtColor(top, cv2.COLOR_BGR2HSV)[:, :, 2], 99.5))
    if 20 < hi < 200:
        top = np.clip(top.astype(np.float32) * (235.0 / hi), 0, 255).astype(np.uint8)
    hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (5, 25, 120), (30, 170, 255))  # light tan pill fill
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 9), np.uint8))
    _, _, st, _ = cv2.connectedComponentsWithStats(m)
    best, best_green = None, 30
    for x, y, w, h, a in st[1:]:
        if h < 12 or not (2.5 * h <= w <= 12 * h) or a < 0.5 * w * h:
            continue
        icon = hsv[max(0, y - h // 2):y + h + h // 2, max(0, x - 2 * h):x + h // 2]
        green = cv2.countNonZero(cv2.inRange(icon, (40, 80, 60), (85, 255, 255)))
        if green > best_green:
            best, best_green = (x, y, w, h), green
    if not best:
        return None
    x, y, w, h = best
    v, _ = read_number(top[y:y + h, x + h // 2:x + w - 2], dark=True)
    return v


def top_bar_numbers(img):
    """All integers in the top HUD strip (money is one of them)."""
    H = img.shape[0]
    strip = img[0:int(H * 0.1), :]
    strip = cv2.resize(strip, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    nums = []
    for l in ocr.read_lines(strip):
        for t in re.findall(r"\d[\d ]{2,}\d|\d{3,}", l.text):
            t = t.replace(" ", "")
            if t.isdigit():
                nums.append(int(t))
    return nums


def read_qty_dialog(img, title_line, ok_btn: Rect) -> Optional[int]:
    """Number in the 'Prodat kusů' input field (dark digits on light field)."""
    y0 = title_line.y + title_line.h + 2
    y1 = ok_btn.y - 2
    x0 = max(0, ok_btn.x - int(1.6 * ok_btn.w))
    x1 = min(img.shape[1], ok_btn.x + int(2.6 * ok_btn.w))
    if y1 - y0 < 10:
        return None
    crop = img[y0:y1, x0:x1]
    # restrict to the light input box: digits are inside it, frame touches border
    v, _ = read_number(crop, dark=True)
    return v


def _continuation_lines(lines, first, stop_y=None):
    """Lines that continue `first` when the game wraps a long text onto more rows:
    directly below it (gap under ~1 line height) and horizontally overlapping it."""
    out, prev = [], first
    for l in sorted((l for l in lines if l.y > first.y), key=lambda l: l.y):
        if stop_y is not None and l.y + l.h > stop_y:
            break
        h = max(prev.h, l.h, 1)
        if l.y - (prev.y + prev.h) > h * 1.2:
            break
        cx = l.x + l.w / 2
        if not (first.x - h * 2 <= cx <= first.x + first.w + h * 2):
            continue
        out.append(l)
        prev = l
    return out


def parse_sold_name(lines, stop_y=None) -> Optional[str]:
    """Fish name from the "Úspěšně jsi prodal ..." message. Long names wrap onto a second row,
    so rows right below the message are joined too (up to `stop_y`, e.g. the OK button)."""
    for l in lines:
        d = deaccent(l.text)
        if "prodal" in d:
            t = " ".join([l.text] + [c.text for c in _continuation_lines(lines, l, stop_y)])
            i = t.lower().find("prodal")
            rest = t[i + len("prodal"):]
            rest = re.sub(r"^[a-záé]*\s+", "", rest, count=1) if rest[:1] not in (" ",) else rest
            return re.sub(r"\s+", " ", rest).strip(" !.: ")
    return None
