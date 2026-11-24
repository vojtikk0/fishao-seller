"""macOS helpers: finding the game window, capture, mouse input, stop hotkey.

A "window handle" here is the CoreGraphics window number (int). Coordinates handed to the
bot are in captured-image pixels (Retina = 2× points); mouse functions convert back to points.
"""
import os
import subprocess
import time

import mss
import numpy as np
import Quartz
from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

_scale = 1.0  # image pixels per screen point, set by Capturer.grab()

_NOT_GAME = ("discord", "google chrome", "chrome", "microsoft edge", "safari", "firefox", "opera",
             "brave", "arc", "code", "visual studio code", "finder", "terminal", "iterm", "python",
             "fishao seller", "fishaoseller", "telegram", "whatsapp", "messages", "notes")


def check_permissions() -> None:
    """Screen Recording (capture + window titles) and Accessibility (clicking) must be granted."""
    missing = []
    try:
        if not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
            missing.append("Nahrávání obrazovky")
    except AttributeError:
        pass  # macOS < 10.15
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        if not AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}):
            missing.append("Zpřístupnění")
    except ImportError:
        pass
    if missing:
        raise RuntimeError(
            "Chybí oprávnění: " + " a ".join(missing) + ". Povol Fishao Seller v Nastavení systému → "
            "Soukromí a zabezpečení → " + " / ".join(missing) + ", pak program zavři a spusť znovu.")


def _all_windows():
    opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
    return Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []


def _info(wid):
    lst = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionIncludingWindow, int(wid)) or []
    for w in lst:
        if int(w.get(Quartz.kCGWindowNumber, -1)) == int(wid):
            return w
    # on some macOS versions the single-window query comes back empty -> search the full list
    for w in _all_windows():
        if int(w.get(Quartz.kCGWindowNumber, -1)) == int(wid):
            return w
    return None


def find_window(title: str):
    """Find the game window: title or app name containing `title`, preferring the Fishao app;
    never Discord / browsers / editors that merely mention the title."""
    want = title.strip().lower()
    me = os.getpid()
    cands = []
    for w in _all_windows():
        if w.get(Quartz.kCGWindowLayer, 1) != 0 or int(w.get(Quartz.kCGWindowOwnerPID, -1)) == me:
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        if b.get("Width", 0) < 200 or b.get("Height", 0) < 200:
            continue
        t = str(w.get(Quartz.kCGWindowName) or "").strip().lower()
        owner = str(w.get(Quartz.kCGWindowOwnerName) or "").strip().lower()
        if not want or (want not in t and want not in owner):
            continue
        if any(owner.startswith(x) for x in _NOT_GAME):
            continue
        score = (2 if owner.startswith("fishao") else 0) + (1 if t == want else 0) \
            + (1 if w.get(Quartz.kCGWindowIsOnscreen) else 0)
        cands.append((score, int(w[Quartz.kCGWindowNumber])))
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    return cands[0][1]


def client_rect(hwnd):
    """Window area in screen points: (left, top, width, height). Includes the title bar."""
    w = _info(hwnd)
    if not w:
        return 0, 0, 0, 0
    b = w.get(Quartz.kCGWindowBounds) or {}
    return int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0))


def _pid(hwnd):
    w = _info(hwnd)
    return int(w.get(Quartz.kCGWindowOwnerPID, -1)) if w else -1


def _on_screen(hwnd) -> bool:
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if int(w.get(Quartz.kCGWindowNumber, -1)) == int(hwnd):
            return True
    return False


def is_minimized(hwnd) -> bool:
    # Newer macOS (27) may leave kCGWindowIsOnscreen out of the single-window query even for a
    # visible window -> the on-screen window list decides.
    if _on_screen(hwnd):
        return False
    w = _info(hwnd)
    return not (w and w.get(Quartz.kCGWindowIsOnscreen))


def is_foreground(hwnd) -> bool:
    """True when the frontmost normal window on screen belongs to the game."""
    pid = _pid(hwnd)
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
        if w.get(Quartz.kCGWindowLayer, 1) != 0:
            continue
        b = w.get(Quartz.kCGWindowBounds) or {}
        if b.get("Width", 0) < 50 or b.get("Height", 0) < 50:
            continue
        return int(w.get(Quartz.kCGWindowOwnerPID, -2)) == pid
    return False


def activate(hwnd) -> bool:
    pid = _pid(hwnd)
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid > 0 else None
    if app is None:
        return False
    try:
        app.unhide()
    except Exception:
        pass
    try:
        app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception:
        # option deprecated since macOS 14 - newer systems may reject it
        try:
            app.activateWithOptions_(0)
        except Exception:
            subprocess.run(["osascript", "-e",
                            f'tell application "System Events" to set frontmost of '
                            f'(first process whose unix id is {pid}) to true'],
                           capture_output=True, timeout=5)
    for _ in range(10):
        time.sleep(0.1)
        if is_foreground(hwnd):
            return True
    return is_foreground(hwnd)


class Capturer:
    """Per-thread screen grabber of the game window (BGR numpy, Retina resolution)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._sct = mss.mss()

    def grab(self):
        global _scale
        x, y, w, h = client_rect(self.hwnd)
        if w == 0 and h == 0:
            raise RuntimeError("Okno hry zmizelo (hra se zavřela nebo okno vytvořila znovu).")
        if w < 200 or h < 200:
            raise RuntimeError(f"Okno hry je příliš malé ({w}×{h}). Zvětši ho.")
        if is_minimized(self.hwnd):
            raise RuntimeError("Okno hry je minimalizované nebo na jiné ploše.")
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3].copy()
        _scale = img.shape[1] / float(w)
        return img, (int(round(x * _scale)), int(round(y * _scale)))

    def close(self):
        try:
            self._sct.close()
        except Exception:
            pass


# ---- mouse / keyboard ------------------------------------------------------------
def _pt(x, y):
    return (float(x) / _scale, float(y) / _scale)


def _mouse(kind, p):
    ev = Quartz.CGEventCreateMouseEvent(None, kind, p, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def move_to(x: int, y: int):
    p = _pt(x, y)
    Quartz.CGWarpMouseCursorPosition(p)
    _mouse(Quartz.kCGEventMouseMoved, p)


def click(x: int, y: int):
    move_to(x, y)
    time.sleep(0.08)
    p = _pt(x, y)
    _mouse(Quartz.kCGEventLeftMouseDown, p)
    time.sleep(0.07)
    _mouse(Quartz.kCGEventLeftMouseUp, p)


def wheel(x: int, y: int, notches: int):
    """notches < 0 = scroll down."""
    move_to(x, y)
    time.sleep(0.05)
    for _ in range(abs(notches)):
        ev = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1,
                                                  3 if notches > 0 else -3)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.06)


def drag(x0, y0, x1, y1, steps=12):
    move_to(x0, y0)
    time.sleep(0.08)
    _mouse(Quartz.kCGEventLeftMouseDown, _pt(x0, y0))
    time.sleep(0.08)
    for i in range(1, steps + 1):
        p = _pt(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        Quartz.CGWarpMouseCursorPosition(p)
        _mouse(Quartz.kCGEventLeftMouseDragged, p)
        time.sleep(0.02)
    time.sleep(0.08)
    _mouse(Quartz.kCGEventLeftMouseUp, _pt(x1, y1))


def _key(code):
    for down in (True, False):
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, code, down))
        time.sleep(0.05)


def press_escape():
    _key(53)


def stop_key_pressed() -> bool:
    """F12 pressed (on a MacBook usually fn+F12), or mouse pushed into the top-left corner."""
    try:
        if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 111):
            return True
    except Exception:
        pass
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return loc.x <= 2 and loc.y <= 2


def open_folder(path: str) -> None:
    subprocess.Popen(["open", path])
