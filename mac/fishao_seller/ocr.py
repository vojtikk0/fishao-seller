"""macOS OCR via Apple Vision (VNRecognizeTextRequest), returning lines + boxes."""
import os
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
                    _langs = [tag] + [t for t in ("en-US", "en") if t in sup][:1]
                    break
        except Exception:
            pass
    return _langs


def check_available() -> None:
    if not hasattr(Vision, "VNRecognizeTextRequest"):
        raise RuntimeError("Rozpoznávání textu (Apple Vision) vyžaduje macOS 10.15 nebo novější.")
    _languages()


_plain = False   # True once this macOS rejected our request options
_handler_way = None  # index into _HANDLERS of the way that worked on this Mac


def _h_data_dict(data, png):
    return Vision.VNImageRequestHandler.alloc().initWithData_options_(data, {}), None


def _h_data_none(data, png):
    return Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None), None


def _h_data_nsdict(data, png):
    from Foundation import NSDictionary
    return Vision.VNImageRequestHandler.alloc().initWithData_options_(data, NSDictionary.dictionary()), None


def _h_cgimage(data, png):
    import Quartz
    src = Quartz.CGImageSourceCreateWithData(data, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None) if src is not None else None
    if cg is None:
        raise RuntimeError("CGImage se nepodařilo vytvořit")
    from Foundation import NSDictionary
    return Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, NSDictionary.dictionary()), None


def _h_url(data, png):
    import tempfile
    from Foundation import NSURL, NSDictionary
    fd, path = tempfile.mkstemp(suffix=".png", prefix="fishao_ocr_")
    with os.fdopen(fd, "wb") as f:
        f.write(png.tobytes())
    h = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(path), NSDictionary.dictionary())
    return h, path  # the temp file is deleted after performRequests


# Newer macOS versions (27+) reject some of these with NSInvalidArgumentException
# ("key does not exist"), so try them in turn and remember the first that works.
_HANDLERS = [("data+{}", _h_data_dict), ("data+None", _h_data_none), ("data+NSDictionary", _h_data_nsdict),
             ("CGImage", _h_cgimage), ("soubor", _h_url)]


def _perform(way, data, png, req):
    h, tmp = way(data, png)
    try:
        if h is None:
            raise RuntimeError("VNImageRequestHandler je None")
        ok, err = h.performRequests_error_([req], None)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    if not ok:
        raise RuntimeError(f"performRequests selhalo: {err}")


def _recognize(png, W, H, plain=False):
    global _handler_way
    out = []
    with objc.autorelease_pool():
        data = NSData.dataWithBytes_length_(png.tobytes(), len(png))
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        if not plain:
            req.setUsesLanguageCorrection_(False)  # would "fix" fish names and numbers
            req.setMinimumTextHeight_(min(1.0, 8.0 / H))  # default skips text < 1/32 of the image
            langs = _languages()
            if langs:
                req.setRecognitionLanguages_(langs)
        order = list(range(len(_HANDLERS)))
        if _handler_way is not None:
            order.remove(_handler_way)
            order.insert(0, _handler_way)
        errors = []
        for i in order:
            name, way = _HANDLERS[i]
            try:
                _perform(way, data, png, req)
                _handler_way = i
                break
            except Exception as e:  # noqa: BLE001 - try the next way, report all at the end
                errors.append(f"{name}: {e!r}")
        else:
            raise ValueError("Apple Vision OCR selhalo všemi způsoby -> " + " | ".join(errors))
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
    return out


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
    global _plain
    try:
        out = _recognize(png, W, H, plain=_plain)
    except ValueError:
        # PyObjC turns an Objective-C NSInvalidArgumentException into ValueError. Some macOS
        # versions reject one of the request options -> retry with Vision's defaults only.
        if _plain:
            raise
        out = _recognize(png, W, H, plain=True)
        _plain = True
    out.sort(key=lambda l: (l.y, l.x))
    return out
