"""Fake macOS frameworks (objc, Foundation, Vision, Quartz, AppKit, ApplicationServices, mss)
so the Mac version can be exercised on Windows/Linux.

Each fake follows the documented behaviour of the real API, which is what the test checks
the Mac code against:
  - Vision: boundingBox is normalized 0..1 with the origin in the BOTTOM-left corner.
  - Quartz: window bounds and mouse events are in points (top-left origin); Retina screen
    captures come back at 2x pixels.
  - pyobjc: methods with an NSError** out-parameter return (result, error).
Text recognition inside the fake Vision is done by Windows OCR (from the Windows version), so
on Windows the recognized text is real.
"""
import contextlib
import importlib.util
import os
import sys
import types

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _win_ocr():
    spec = importlib.util.spec_from_file_location("_win_ocr", os.path.join(REPO, "fishao_seller", "ocr.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ---- state the tests can drive ---------------------------------------------------
class World:
    windows = []          # dicts like CGWindowListCopyWindowInfo returns, front-to-back
    events = []           # posted CGEvents
    cursor = (500.0, 500.0)
    keys_down = set()
    screen_access = True
    ax_trusted = True
    prompts = []
    retina = 2.0
    screen = None         # callable () -> BGR image of the game in POINTS (1x)
    on_event = None       # callable (event) for the game simulation
    activated = []
    vision_settings = []
    macos27 = False       # mimic macOS 27: Vision rejects image data (NSInvalidArgumentException
                          # "key does not exist"), single-window query lacks kCGWindowIsOnscreen


def install():
    W = World

    # objc / Foundation
    objc = types.ModuleType("objc")
    objc.autorelease_pool = contextlib.nullcontext
    Foundation = types.ModuleType("Foundation")
    Foundation.NSData = _Obj(dataWithBytes_length_=lambda b, n: bytes(b[:n]))
    Foundation.NSDictionary = _Obj(dictionary=lambda: {})
    Foundation.NSURL = _Obj(fileURLWithPath_=lambda p: ("url", p))

    # Vision
    winocr = _win_ocr()
    Vision = types.ModuleType("Vision")
    Vision.VNRequestTextRecognitionLevelAccurate = 0

    class VNRecognizeTextRequest:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            self._res, self.cfg = [], {}
            return self

        def setRecognitionLevel_(self, v): self.cfg["level"] = v
        def setUsesLanguageCorrection_(self, v): self.cfg["correction"] = v
        def setMinimumTextHeight_(self, v): self.cfg["min_h"] = v
        def setRecognitionLanguages_(self, v): self.cfg["langs"] = list(v)

        def supportedRecognitionLanguagesAndReturnError_(self, err):
            return (["en-US", "fr-FR", "cs-CZ"], None)

        def results(self):
            return self._res

    class VNImageRequestHandler:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithData_options_(self, data, opts):
            if W.macos27:
                raise ValueError("NSInvalidArgumentException - key does not exist")
            assert isinstance(opts, dict)
            self.img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            return self

        def initWithURL_options_(self, url, opts):
            assert url[0] == "url" and isinstance(opts, dict)
            self.img = cv2.imdecode(np.fromfile(url[1], np.uint8), cv2.IMREAD_COLOR)
            return self

        def performRequests_error_(self, reqs, err):
            H, W_ = self.img.shape[:2]
            for r in reqs:
                W.vision_settings.append(dict(r.cfg, img=(W_, H)))
                r._res = []
                for l in winocr.read_lines(self.img):
                    bb = _Obj(origin=_Obj(x=l.x / W_, y=1.0 - (l.y + l.h) / H),
                              size=_Obj(width=l.w / W_, height=l.h / H))
                    cand = _Obj(string=lambda t=l.text: t)
                    r._res.append(_Obj(topCandidates_=lambda n, c=cand: [c][:n], boundingBox=lambda b=bb: b))
            return (True, None)

    Vision.VNRecognizeTextRequest = VNRecognizeTextRequest
    Vision.VNImageRequestHandler = VNImageRequestHandler

    # Quartz
    Q = types.ModuleType("Quartz")
    names = ["kCGWindowListOptionAll", "kCGWindowListOptionOnScreenOnly", "kCGWindowListExcludeDesktopElements",
             "kCGWindowListOptionIncludingWindow"]
    for i, n in enumerate(names):
        setattr(Q, n, 1 << i)
    Q.kCGNullWindowID = 0
    for n in ("kCGWindowNumber", "kCGWindowLayer", "kCGWindowOwnerPID", "kCGWindowBounds", "kCGWindowName",
              "kCGWindowOwnerName", "kCGWindowIsOnscreen"):
        setattr(Q, n, n)
    for n in ("kCGEventMouseMoved", "kCGEventLeftMouseDown", "kCGEventLeftMouseUp", "kCGEventLeftMouseDragged",
              "kCGMouseButtonLeft", "kCGHIDEventTap", "kCGScrollEventUnitLine",
              "kCGEventSourceStateCombinedSessionState"):
        setattr(Q, n, n)

    def CGWindowListCopyWindowInfo(opts, wid):
        if opts & Q.kCGWindowListOptionIncludingWindow:
            found = [w for w in W.windows if w["kCGWindowNumber"] == wid]
            if W.macos27:
                found = [{k: v for k, v in w.items() if k != "kCGWindowIsOnscreen"} for w in found]
            return found
        if opts & Q.kCGWindowListOptionOnScreenOnly:
            return [w for w in W.windows if w.get("kCGWindowIsOnscreen")]
        return list(W.windows)

    def _post(tap, ev):
        assert tap == "kCGHIDEventTap"
        W.events.append(ev)
        if W.on_event:
            W.on_event(ev)

    def _point(p):
        assert isinstance(p, tuple) and len(p) == 2 and all(isinstance(v, float) for v in p), p
        return p

    def CGWarpMouseCursorPosition(p):
        W.cursor = _point(p)

    def CGEventCreateMouseEvent(src, kind, p, btn):
        assert btn == "kCGMouseButtonLeft"
        return ("mouse", kind, _point(p))

    def CGEventCreateScrollWheelEvent(src, unit, count, *wheels):
        assert unit == "kCGScrollEventUnitLine" and count == len(wheels) == 1
        return ("wheel", wheels[0], W.cursor)

    Q.CGWindowListCopyWindowInfo = CGWindowListCopyWindowInfo
    Q.CGEventPost = _post
    Q.CGWarpMouseCursorPosition = CGWarpMouseCursorPosition
    Q.CGEventCreateMouseEvent = CGEventCreateMouseEvent
    Q.CGEventCreateScrollWheelEvent = CGEventCreateScrollWheelEvent
    Q.CGEventCreateKeyboardEvent = lambda src, code, down: ("key", code, down)
    Q.CGEventSourceKeyState = lambda state, code: code in W.keys_down
    Q.CGEventCreate = lambda src: "now"
    Q.CGEventGetLocation = lambda ev: _Obj(x=W.cursor[0], y=W.cursor[1])
    Q.CGPreflightScreenCaptureAccess = lambda: W.screen_access
    Q.CGRequestScreenCaptureAccess = lambda: W.prompts.append("screen")

    # AppKit
    AppKit = types.ModuleType("AppKit")
    AppKit.NSApplicationActivateIgnoringOtherApps = 2

    class NSRunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid):
            if not any(w["kCGWindowOwnerPID"] == pid for w in W.windows):
                return None

            def activate(opts):
                W.activated.append(pid)
                mine = [w for w in W.windows if w["kCGWindowOwnerPID"] == pid]
                for w in mine:
                    w["kCGWindowIsOnscreen"] = True
                W.windows[:] = [w for w in W.windows if w["kCGWindowLayer"] != 0 or w in mine] + \
                    [w for w in W.windows if w["kCGWindowLayer"] == 0 and w not in mine]
                return True
            return _Obj(unhide=lambda: True, activateWithOptions_=activate)

    AppKit.NSRunningApplication = NSRunningApplication

    # ApplicationServices
    AS = types.ModuleType("ApplicationServices")
    AS.kAXTrustedCheckOptionPrompt = "AXTrustedCheckOptionPrompt"

    def AXIsProcessTrustedWithOptions(opts):
        if opts.get(AS.kAXTrustedCheckOptionPrompt):
            W.prompts.append("ax")
        return W.ax_trusted
    AS.AXIsProcessTrustedWithOptions = AXIsProcessTrustedWithOptions

    # mss: grabs at Retina resolution like the real one on a 2x display
    mss = types.ModuleType("mss")

    class _Sct:
        def grab(self, mon):
            win = next(w for w in W.windows if w["kCGWindowLayer"] == 0 and w.get("_game"))
            b = win["kCGWindowBounds"]
            assert (mon["left"], mon["top"], mon["width"], mon["height"]) == \
                (b["X"], b["Y"], b["Width"], b["Height"]), mon
            img = W.screen()
            assert img.shape[1] == b["Width"] and img.shape[0] == b["Height"]
            big = cv2.resize(img, None, fx=W.retina, fy=W.retina, interpolation=cv2.INTER_CUBIC)
            return np.dstack([big, np.full(big.shape[:2], 255, np.uint8)])

        def close(self):
            pass

    mss.mss = _Sct

    for name, mod in (("objc", objc), ("Foundation", Foundation), ("Vision", Vision), ("Quartz", Q),
                      ("AppKit", AppKit), ("ApplicationServices", AS), ("mss", mss)):
        sys.modules[name] = mod
    return W
