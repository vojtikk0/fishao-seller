"""OCR diagnostics for fish names on cards: which preprocessing / Vision settings read them right.

The names are yellow text with a brown outline on a cream background, which Apple Vision reads
worse than Windows OCR (counts in parentheses get lost). This tries variants on every card of
tests/screens at 1x and 2x (Retina) and prints a score table.

    python3 mac/tests/ocr_diag.py [--out DIR]
"""
import glob
import os
import re
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.dirname(HERE)
REPO = os.path.dirname(MAC)
sys.path.insert(0, MAC)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from fishao_seller import vision  # noqa: E402

EXPECTED = {
    "192411": [("Afrotetra Forskållova", 2), ("Chrochtal modropásý", 7), ("Havýš plástevný", 1)],
    "192435": [("Ostenec sargasový", 4), ("Sardinka oblá", 41), ("Vranka mnohoostná", 1)],
}


def prep(crop, kind):
    """Return a BGR image for OCR."""
    if kind == "raw":
        return crop
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if kind == "sat":       # saturated (yellow fill + brown outline) -> dark, cream -> light
        g = 255 - hsv[:, :, 1]
    elif kind == "fill":    # yellow fill only, black on white
        m = cv2.inRange(hsv, (10, 110, 150), (40, 255, 255))
        g = 255 - m
    elif kind == "fillb":   # yellow fill, slightly bolder
        m = cv2.inRange(hsv, (10, 110, 150), (40, 255, 255))
        k = max(2, round(crop.shape[0] / 28))
        g = 255 - cv2.dilate(m, np.ones((k, k), np.uint8))
    elif kind == "glyph":   # fill + outline, black on white
        m = cv2.inRange(hsv, (10, 110, 150), (40, 255, 255)) | cv2.inRange(hsv, (0, 0, 0), (180, 255, 110))
        g = 255 - m
    else:
        raise ValueError(kind)
    g = cv2.copyMakeBorder(g, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def make_reader():
    if sys.platform != "darwin":
        from fishao_seller import ocr

        def read(img, mode):
            return [l.text for l in ocr.read_lines(img)]
        return read, ["default"]
    import objc
    import Vision
    from Foundation import NSData

    def read(img, mode):
        ok, png = cv2.imencode(".png", img)
        with objc.autorelease_pool():
            data = NSData.dataWithBytes_length_(png.tobytes(), len(png))
            req = Vision.VNRecognizeTextRequest.alloc().init()
            fast = mode.startswith("fast")
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelFast if fast
                                     else Vision.VNRequestTextRecognitionLevelAccurate)
            req.setUsesLanguageCorrection_(mode.endswith("+corr"))
            req.setMinimumTextHeight_(min(1.0, 8.0 / img.shape[0]))
            req.setRecognitionLanguages_(["en-US"] if fast else ["cs-CZ", "en-US"])
            h = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
            h.performRequests_error_([req], None)
            return [str(o.topCandidates_(1)[0].string()) for o in (req.results() or []) if o.topCandidates_(1)]
    return read, ["acc", "acc+corr", "fast"]


def parse(texts):
    for t in texts:
        m = vision._NAME_RE.match(t.strip())
        if m and len(m.group(1)) >= 3:
            return m.group(1).strip(" -—–.,:"), vision._parse_count(m.group(2))
    return None, None


def main():
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(MAC, "logs")
    os.makedirs(out_dir, exist_ok=True)
    read, modes = make_reader()
    score = defaultdict(lambda: [0, 0, 0.0])   # variant -> [name ok, name+count ok, seconds]
    rows = []
    total = 0
    for f in sorted(glob.glob(os.path.join(REPO, "tests", "screens", "*.png"))):
        key = os.path.basename(f)[-10:-4]
        if key not in EXPECTED:
            continue
        img1 = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
        for base in (1, 2):
            img = img1 if base == 1 else cv2.resize(img1, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            btns = vision.find_sell_buttons(img)
            for b, (ename, ecount) in zip(btns, EXPECTED[key]):
                total += 1
                x0, y0, x1, y1 = vision.name_region(img, b)
                crop = img[y0:y1, x0:x1]
                for kind in ("raw", "sat", "fill", "fillb", "glyph"):
                    for sc in (1.0, 1.5, 2.0, 3.0):
                        c = crop if sc == 1 else cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
                        p = prep(c, kind)
                        for mode in modes:
                            t0 = time.time()
                            texts = read(p, mode)
                            dt = time.time() - t0
                            name, count = parse(texts)
                            nok = bool(name) and fuzz.ratio(vision.deaccent(name).lower(),
                                                            vision.deaccent(ename).lower()) >= 88
                            v = f"{kind:<5} x{sc:<3} {mode}"
                            s = score[v]
                            s[0] += nok
                            s[1] += nok and count == ecount
                            s[2] += dt
                            rows.append(f"{key}@{base}x {ename} ({ecount}) | {v} -> {texts}")
    rep = [f"{'variant':<22} names  name+count  avg s   (of {total} cards)"]
    for v, (a, b, t) in sorted(score.items(), key=lambda kv: (-kv[1][1], -kv[1][0], kv[1][2])):
        rep.append(f"{v:<22} {a:>5}  {b:>10}  {t / total:5.2f}")
    print("\n".join(rep))
    with open(os.path.join(out_dir, "ocr_diag.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n\n" + "\n".join(rows))


if __name__ == "__main__":
    main()
