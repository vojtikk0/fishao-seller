"""Full tracebacks go to logs/chyby.log; the GUI log gets a one-liner with the failing place."""
import os
import platform
import sys
import time
import traceback

from . import __version__
from .config import LOG_DIR

LOG_FILE = os.path.join(LOG_DIR, "chyby.log")


def where(e: BaseException) -> str:
    """'file.py:123 func' of the deepest frame inside our package (else the deepest frame)."""
    frames = traceback.extract_tb(e.__traceback__)
    if not frames:
        return ""
    ours = [f for f in frames if "fishao_seller" in f.filename.replace("\\", "/")]
    f = (ours or frames)[-1]
    return f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"


def user_message(e: BaseException):
    """Expected, self-explaining errors (our Czech messages) -> their text; otherwise None."""
    if isinstance(e, RuntimeError) or type(e).__name__ in ("Abort", "Stopped"):
        return str(e)
    return None


def record(e: BaseException, context: str = "") -> str:
    """Append the traceback to chyby.log. Returns a short text for the GUI log."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} | Fishao Seller Mac {__version__} | "
                    f"macOS {platform.mac_ver()[0]} {platform.machine()} | Python {sys.version.split()[0]}"
                    f"{' | ' + context if context else ''}\n")
            f.write("".join(traceback.format_exception(type(e), e, e.__traceback__)))
            f.write("\n")
    except Exception:
        pass
    w = where(e)
    return f"{e!r}" + (f"  [{w}]" if w else "")
