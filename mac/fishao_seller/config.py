"""Settings stored as JSON (~/Library/Application Support/Fishao Seller for the built .app)."""
import json
import os
import sys
from dataclasses import dataclass, field, asdict, fields


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        # inside a .app bundle the program folder is read-only / signed
        d = os.path.expanduser("~/Library/Application Support/Fishao Seller")
        os.makedirs(d, exist_ok=True)
        return d
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")
CACHE_PATH = os.path.join(app_dir(), "tracker_cache.json")
LOG_DIR = os.path.join(app_dir(), "logs")


@dataclass
class Settings:
    # --- hra ---
    window_title: str = "FISHAO - Reborn"
    # --- pravidla ---
    mode: str = "range"            # range | pct_max | above_avg | percentile
    pct_in_range: float = 80.0     # range: prodej, když cena leží v rozmezí min–max aspoň na X %
    pct_of_max: float = 90.0       # pct_max: prodej, když cena >= X % maxima
    pct_above_avg: float = 10.0    # above_avg: prodej, když cena >= průměr + X %
    percentile: float = 75.0       # percentile: prodej, když cena >= X. percentil
    history_days: int = 30
    min_samples: int = 3           # min. počet dní v historii
    no_data_action: str = "skip"   # skip | estimate | sell
    min_total: int = 0             # neprodávat, když celkem (cena*kusy) < X
    verify_with_tracker: bool = False
    never_sell: list = field(default_factory=list)
    always_sell: list = field(default_factory=list)
    # --- bezpečnost ---
    dry_run: bool = True
    action_delay_ms: int = 600
    max_sales: int = 200
    strict_money_check: bool = True
    tracker_max_age_min: int = 30
    name_min_score: int = 88

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(s, k, v)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return s

    def save(self) -> None:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
