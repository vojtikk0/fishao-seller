"""Offline check of screen reading on saved screenshots (python -m tests.test_offline [--fetch])."""
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fishao_seller import ocr, tracker, vision  # noqa: E402
from fishao_seller.config import Settings  # noqa: E402
from fishao_seller.pricing import Catalog, decide  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# own screenshots in mac/tests/screens, otherwise the shared ones from the repo root
shots = sorted(glob.glob(os.path.join(HERE, "tests", "screens", "*.png"))) or \
    sorted(glob.glob(os.path.join(HERE, "..", "tests", "screens", "*.png")))
data = tracker.fetch() if "--fetch" in sys.argv else tracker.load_cache()
cat = Catalog(data) if data else None
s = Settings()

for f in shots:
    img = cv2.imdecode(np.fromfile(f, np.uint8), cv2.IMREAD_COLOR)
    print("==", os.path.basename(f), img.shape[1], "x", img.shape[0])
    lines = ocr.read_lines(img)
    print("   shop open:", vision.shop_is_open(lines), "| top numbers:", vision.top_bar_numbers(img), "| FB:", vision.read_fishbucks(img))
    for b in vision.find_sell_buttons(img):
        c = vision.read_card(img, b)
        line = f"   card y={b.y}: name={c.name!r} count={c.count} price={c.price} ({c.price_detail})"
        if cat:
            m = cat.match(c.name)
            line += f" -> {m.fish['name'] if m.fish else None} [{m.score:.0f}] {m.note}"
            if m.fish and c.price is not None:
                line += " | " + decide(cat, m.fish, c.price, c.count or 0, s).reason
        print(line)
    blues = vision.find_blue_buttons(img)
    if blues:
        t = vision.find_line(lines, "prodat", "kus")
        if t:
            print("   QTY dialog value:", vision.read_qty_dialog(img, t, blues[0]))
        print("   sold name:", vision.parse_sold_name(lines))
