"""Windows built-in OCR (Windows.Media.Ocr) with Czech, returning lines + boxes."""
import asyncio
import threading
from dataclasses import dataclass

import cv2

from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream


@dataclass
class Line:
    text: str
    x: int
    y: int
    w: int
    h: int


_tls = threading.local()


def _engine():
    eng = getattr(_tls, "engine", None)
    if eng is None:
        eng = None
        for tag in ("cs", "cs-CZ"):
            try:
                eng = OcrEngine.try_create_from_language(Language(tag))
            except Exception:
                eng = None
            if eng is not None:
                break
        if eng is None:
            raise RuntimeError(
                "Windows OCR pro češtinu není k dispozici. Nainstaluj v Nastavení → Čas a jazyk → "
                "Jazyk → Čeština → Možnosti jazyka → Optické rozpoznávání znaků.")
        _tls.engine = eng
    return eng


def _loop():
    lp = getattr(_tls, "loop", None)
    if lp is None:
        lp = asyncio.new_event_loop()
        _tls.loop = lp
    return lp


async def _recognize(img):
    ok, png = cv2.imencode(".png", img)
    if not ok:
        return []
    stream = InMemoryRandomAccessStream()
    w = DataWriter(stream)
    w.write_bytes(png.tobytes())
    await w.store_async()
    stream.seek(0)
    dec = await BitmapDecoder.create_async(stream)
    bmp = await dec.get_software_bitmap_async()
    res = await _engine().recognize_async(bmp)
    out = []
    for ln in res.lines:
        rs = [wd.bounding_rect for wd in ln.words]
        if not rs:
            continue
        x0 = min(r.x for r in rs)
        y0 = min(r.y for r in rs)
        x1 = max(r.x + r.width for r in rs)
        y1 = max(r.y + r.height for r in rs)
        out.append(Line(ln.text, int(x0), int(y0), int(x1 - x0), int(y1 - y0)))
    return out


def check_available() -> None:
    _engine()


def read_lines(img) -> list:
    """OCR a BGR (or gray) numpy image. Max side is limited by the engine, so scale down if needed."""
    if img is None or img.size == 0:
        return []
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    mx = OcrEngine.max_image_dimension
    scale = 1.0
    if max(h, w) > mx:
        scale = mx / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    lines = _loop().run_until_complete(_recognize(img))
    if scale != 1.0:
        lines = [Line(l.text, int(l.x / scale), int(l.y / scale), int(l.w / scale), int(l.h / scale)) for l in lines]
    return lines
