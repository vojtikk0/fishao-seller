"""Matching OCR names to tracker fish and deciding whether today's price is good."""
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from rapidfuzz import fuzz, process

from .config import Settings
from .vision import deaccent


def today_utc() -> str:
    # Fish Market day follows UTC (it is closed on Sunday 00:00-24:00 UTC)
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def market_closed_today() -> bool:
    return datetime.now(timezone.utc).weekday() == 6


@dataclass
class Match:
    fish: Optional[dict]
    score: float
    note: str = ""


class Catalog:
    def __init__(self, data: dict):
        self.data = data
        self.fish = data.get("fish", [])
        self.by_id = {str(f["id"]): f for f in self.fish}
        self._keys = [deaccent(f["name"]) for f in self.fish]
        self._exact = {f["name"].lower(): f for f in self.fish}

    def match(self, ocr_name: str, min_score: int = 88) -> Match:
        if not ocr_name:
            return Match(None, 0, "prázdný název")
        if ocr_name.lower() in self._exact:
            return Match(self._exact[ocr_name.lower()], 100)
        q = deaccent(ocr_name)
        res = process.extract(q, self._keys, scorer=fuzz.ratio, limit=3)
        if not res:
            return Match(None, 0, "žádná shoda")
        best_key, best_score, best_i = res[0]
        best = self.fish[best_i]
        # second best that is a *different* fish
        second = next((s for k, s, i in res[1:] if self.fish[i]["id"] != best["id"] and k != best_key), 0)
        if best_score < min_score:
            return Match(None, best_score, f"nejistá shoda ({best['name']} {best_score:.0f} %)")
        if second and best_score - second < 6:
            return Match(None, best_score, f"dvojznačné ({best['name']} vs. {second:.0f} %)")
        return Match(best, best_score)

    def names_match(self, a: str, b: str) -> bool:
        return fuzz.ratio(deaccent(a), deaccent(b)) >= 85

    def history(self, fish_id, days: int, today: str):
        """Known prices per day (median per day) within the last `days` days, today included –
        the same data the web uses for its "Známé ceny: min–max" range."""
        start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        per_day = {}
        for d, p in self.data.get("prices", {}).get(str(fish_id), []):
            if start <= d <= today and p > 0:
                per_day.setdefault(d, []).append(p)
        return {d: statistics.median(v) for d, v in per_day.items()}

    def today_prices(self, fish_id, today: str):
        return sorted({p for d, p in self.data.get("prices", {}).get(str(fish_id), []) if d == today})

    def estimate(self, fish_id):
        return self.data.get("estimated", {}).get(str(fish_id))


@dataclass
class Decision:
    sell: bool
    reason: str
    threshold: Optional[float] = None
    stats: dict = field(default_factory=dict)


def _in_list(name, lst):
    d = deaccent(name)
    return any(deaccent(x) == d for x in lst if x.strip())


def decide(cat: Catalog, fish: dict, price: int, count: int, s: Settings, today: Optional[str] = None) -> Decision:
    today = today or today_utc()
    name = fish["name"]
    if _in_list(name, s.never_sell):
        return Decision(False, "na seznamu NIKDY neprodávat")

    tp = cat.today_prices(fish["id"], today)
    if s.verify_with_tracker and tp and price not in tp:
        return Decision(False, f"přečtená cena {price} ≠ dnešní cena v trackeru {tp} → raději neprodám")

    if count is not None and price * count < s.min_total:
        return Decision(False, f"celkem jen {price * count} FB (< {s.min_total})")

    if _in_list(name, s.always_sell):
        return Decision(True, "na seznamu VŽDY prodat")

    hist = cat.history(fish["id"], s.history_days, today)
    vals = list(hist.values())
    stats = {"dní": len(vals)}
    if vals:
        stats.update({"min": min(vals), "max": max(vals), "průměr": round(statistics.mean(vals), 1)})

    if len(vals) < s.min_samples:
        if s.no_data_action == "sell":
            return Decision(True, f"málo dat ({len(vals)} dní) → prodávám dle nastavení", stats=stats)
        if s.no_data_action == "estimate":
            est = cat.estimate(fish["id"])
            if est and est.get("max"):
                lo = est.get("min") or 0
                thr = lo + (est["max"] - lo) * s.pct_in_range / 100 if s.mode == "range" else est["max"] * s.pct_of_max / 100
                ok = price >= thr
                return Decision(ok, f"málo dat, odhad trackeru max {est['max']} → práh {thr:.0f}", thr, stats)
        return Decision(False, f"málo dat v trackeru ({len(vals)} dní, potřeba {s.min_samples})", stats=stats)

    if s.mode == "range":
        lo, hi = stats["min"], stats["max"]
        thr = lo + (hi - lo) * s.pct_in_range / 100
        pos = 100.0 if hi == lo else (price - lo) / (hi - lo) * 100
        stats["pozice"] = round(pos)
        label = f"rozmezí {lo:g}–{hi:g}, cena je na {pos:.0f} %, chci ≥ {s.pct_in_range:g} %"
    elif s.mode == "above_avg":
        thr = stats["průměr"] * (1 + s.pct_above_avg / 100)
        label = f"průměr {stats['průměr']} + {s.pct_above_avg:g} %"
    elif s.mode == "percentile":
        sv = sorted(vals)
        k = (len(sv) - 1) * s.percentile / 100
        lo, hi = int(k), min(int(k) + 1, len(sv) - 1)
        thr = sv[lo] + (sv[hi] - sv[lo]) * (k - lo)
        label = f"{s.percentile:g}. percentil"
    else:
        thr = stats["max"] * s.pct_of_max / 100
        label = f"{s.pct_of_max:g} % z maxima {stats['max']}"

    ok = price >= thr - 1e-9
    return Decision(ok, f"{'VÝHODNÉ' if ok else 'nevýhodné'}: {price} {'≥' if ok else '<'} {thr:.0f} ({label})", thr, stats)
