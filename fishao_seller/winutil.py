"""Windows helpers: DPI, finding the game window, capture, mouse input, hotkeys."""
import ctypes
import ctypes.wintypes as wt
import os
import time

import mss
import numpy as np

user32 = ctypes.windll.user32


def set_dpi_aware() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


_NOT_GAME = ("discord", "chrome", "msedge", "firefox", "opera", "brave", "code", "explorer",
             "python", "fishaoseller", "steamwebhelper", "telegram", "whatsapp")


def process_exe(hwnd) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k = ctypes.windll.kernel32
    hp = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        sz = wt.DWORD(1024)
        if k.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(sz)):
            return buf.value
        return ""
    finally:
        k.CloseHandle(hp)


def find_window(title: str):
    """Find the game window. Prefers the game process (Fishao*.exe), then an exact
    title match; never picks Discord / browsers / editors that merely mention the title."""
    want = title.strip().lower()
    cands = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                t = buf.value.strip().lower()
                if want and want in t:
                    exe = os.path.basename(process_exe(hwnd)).lower()
                    if any(exe.startswith(x) for x in _NOT_GAME):
                        return True
                    score = (2 if exe.startswith("fishao") else 0) + (1 if t == want else 0)
                    cands.append((score, hwnd))
        return True

    user32.EnumWindows(cb, 0)
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    return cands[0][1]


def client_rect(hwnd):
    """Client area in screen pixels: (left, top, width, height)."""
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, r.right - r.left, r.bottom - r.top


def is_minimized(hwnd) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_foreground(hwnd) -> bool:
    return user32.GetForegroundWindow() == hwnd


def activate(hwnd) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # ALT tap lets SetForegroundWindow work from a background thread
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    return is_foreground(hwnd)


class Capturer:
    """Per-thread screen grabber of the game's client area (BGR numpy)."""

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._sct = mss.mss()

    def grab(self):
        x, y, w, h = client_rect(self.hwnd)
        if w < 200 or h < 200:
            raise RuntimeError("Okno hry je minimalizované nebo příliš malé.")
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3].copy()
        return img, (x, y)

    def close(self):
        try:
            self._sct.close()
        except Exception:
            pass


# ---- mouse -----------------------------------------------------------------
class _MI(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MI), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


MOVE, LDOWN, LUP, WHEEL, ABSOLUTE, VIRTUALDESK = 0x1, 0x2, 0x4, 0x800, 0x8000, 0x4000


def _send(flags, dx=0, dy=0, data=0):
    inp = _INPUT(type=0)
    inp.mi = _MI(dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def move_to(x: int, y: int):
    user32.SetCursorPos(int(x), int(y))
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    nx = int((x - vx) * 65535 / max(1, vw - 1))
    ny = int((y - vy) * 65535 / max(1, vh - 1))
    _send(MOVE | ABSOLUTE | VIRTUALDESK, nx, ny)


def click(x: int, y: int):
    move_to(x, y)
    time.sleep(0.08)
    _send(LDOWN)
    time.sleep(0.07)
    _send(LUP)


def wheel(x: int, y: int, notches: int):
    """notches < 0 = scroll down."""
    move_to(x, y)
    time.sleep(0.05)
    for _ in range(abs(notches)):
        _send(WHEEL, data=120 if notches > 0 else -120)
        time.sleep(0.06)


def drag(x0, y0, x1, y1, steps=12):
    move_to(x0, y0)
    time.sleep(0.08)
    _send(LDOWN)
    time.sleep(0.08)
    for i in range(1, steps + 1):
        move_to(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(0.02)
    time.sleep(0.08)
    _send(LUP)


def press_escape():
    user32.keybd_event(0x1B, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(0x1B, 0, 2, 0)


def stop_key_pressed() -> bool:
    """F12 pressed, or mouse pushed into the top-left screen corner."""
    if user32.GetAsyncKeyState(0x7B) & 0x8000:
        return True
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    return pt.x <= vx + 2 and pt.y <= vy + 2
