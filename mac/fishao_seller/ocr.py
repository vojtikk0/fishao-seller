"""macOS OCR via Apple Vision (VNRecognizeTextRequest), returning lines + boxes."""
from dataclasses import dataclass

import cv2
import objc
import Vision
from Foundation import NSData


@dataclass
class Line:
    text: str
    x: int
    y: int
    w: int
    h: int


_langs = None


def _languages():
    """Czech when this macOS version's Vision knows it, otherwise the default model
    (diacritics may be lost; names are matched without accents anyway)."""
    global _langs
    if _langs is None:
        _langs = []
        try:
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            sup, _ = req.supportedRecognitionLanguagesAndReturnError_(None)
            sup = [str(x) for x in (sup or [])]
            for tag in ("cs-CZ", "cs"):
                if tag in sup:
                    _langs = [tag, "en-US"]
                    break
        except Exception:
            pass
    return _langs


def check_available() -> None:
    if not hasattr(Vision, "VNRecognizeTextRequest"):
        raise RuntimeError("Rozpoznávání textu (Apple Vision) vyžaduje macOS 10.15 nebo novější.")
    _languages()


def read_lines(img) -> list:
    """OCR a BGR (or gray) numpy image."""
    if img is None or img.size == 0:
        return []
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    H, W = img.shape[:2]
    ok, png = cv2.imencode(".png", img)
    if not ok:
        return []
    out = []
    with objc.autorelease_pool():
        data = NSData.dataWithBytes_length_(png.tobytes(), len(png))
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)  # would "fix" fish names and numbers
        req.setMinimumTextHeight_(min(1.0, 8.0 / H))  # default skips text < 1/32 of the image
        langs = _languages()
        if langs:
            req.setRecognitionLanguages_(langs)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {})
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Apple Vision OCR selhalo: {err}")
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            text = str(cands[0].string())
            bb = obs.boundingBox()  # normalized, origin bottom-left
            x = bb.origin.x * W
            w = bb.size.width * W
            h = bb.size.height * H
            y = (1.0 - bb.origin.y - bb.size.height) * H
            out.append(Line(text, int(round(x)), int(round(y)), int(round(w)), int(round(h))))
    out.sort(key=lambda l: (l.y, l.x))
    return out
