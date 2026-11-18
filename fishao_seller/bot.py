"""The selling loop. Every step is verified on screen; anything unexpected = stop."""
import csv
import os
import threading
import time
from datetime import datetime

import cv2
import numpy as np

from . import ocr, vision, winutil
from .config import LOG_DIR, Settings
from .pricing import Catalog, decide, market_closed_today


class Stopped(Exception):
    pass


class Abort(Exception):
    pass


class Bot:
    def __init__(self, settings: Settings, data: dict, log, stop_event: threading.Event):
        self.s = settings
        self.cat = Catalog(data)
        self.log = log
        self.stop_event = stop_event
        self.hwnd = None
        self.cap = None
        self.origin = (0, 0)
        os.makedirs(LOG_DIR, exist_ok=True)
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- helpers ---------------------------------------------------------------
    def check_stop(self):
        if self.stop_event.is_set() or winutil.stop_key_pressed():
            self.stop_event.set()
            raise Stopped()

    def sleep(self, sec):
        end = time.time() + sec
        while time.time() < end:
            self.check_stop()
            time.sleep(0.03)

    def pause(self, factor=1.0):
        self.sleep(self.s.action_delay_ms / 1000 * factor)

    def grab(self):
        self.check_stop()
        self.ensure_foreground()  # otherwise we'd be reading whatever covers the game
        img, self.origin = self.cap.grab()
        return img

    def ensure_foreground(self):
        if not winutil.is_foreground(self.hwnd):
            raise Abort("Hra přestala být v popředí (přepnul ses jinam?). Zastavuji.")

    def click(self, x, y):
        self.check_stop()
        self.ensure_foreground()
        winutil.click(self.origin[0] + x, self.origin[1] + y)

    def save_shot(self, img, tag):
        p = os.path.join(LOG_DIR, f"{self.run_id}_{tag}.png")
        try:
            cv2.imencode(".png", img)[1].tofile(p)
        except Exception:
            pass
        return p

    def wait_for(self, fn, timeout=6.0, step=0.25):
        end = time.time() + timeout
        while time.time() < end:
            img = self.grab()
            r = fn(img)
            if r:
                return img, r
            self.sleep(step)
        return None, None

    # ---- setup -----------------------------------------------------------------
    def attach(self):
        ocr.check_available()
        self.hwnd = winutil.find_window(self.s.window_title)
        if not self.hwnd:
            raise Abort(f"Nenašel jsem okno hry „{self.s.window_title}“. Je hra spuštěná?")
        if winutil.is_minimized(self.hwnd):
            winutil.activate(self.hwnd)
        self.cap = winutil.Capturer(self.hwnd)
        x, y, w, h = winutil.client_rect(self.hwnd)
        self.log(f"Okno hry nalezeno: {w}×{h} px.")

    def scan(self, img):
        btns = vision.find_sell_buttons(img)
        return [vision.read_card(img, b) for b in btns], btns

    # ---- preview (no clicking) -------------------------------------------------
    def preview(self):
        self.attach()
        if not winutil.activate(self.hwnd):
            raise Abort("Nepodařilo se přepnout na okno hry.")
        self.sleep(0.4)
        img = self.grab()
        lines = ocr.read_lines(img)
        if not vision.shop_is_open(lines):
            self.log("⚠ Nevidím okno obchodu „NAŠE DNEŠNÍ CENY“. Otevři ho ve hře.")
        cards, _ = self.scan(img)
        if not cards:
            self.log("⚠ Nenašel jsem žádná tlačítka prodeje.")
        out = img.copy()
        for c in cards:
            m = self.cat.match(c.name, self.s.name_min_score)
            if m.fish and c.price is not None:
                d = decide(self.cat, m.fish, c.price, c.count or 0, self.s)
                txt, col = d.reason, ((0, 170, 0) if d.sell else (0, 0, 220))
                label = f"{m.fish['name']} ×{c.count} @ {c.price}"
            else:
                txt = m.note if not m.fish else f"cenu nelze přečíst ({c.price_detail})"
                col, label = (0, 140, 255), f"{c.name_raw or '???'} @ {c.price}"
            self.log(f"  • {label}: {txt}")
            b = c.button
            x0, y0, x1, _ = vision.name_region(img, b)
            cv2.rectangle(out, (x0, y0), (b.x + b.w, b.y + b.h), col, 2)
            cv2.rectangle(out, (b.x, b.y), (b.x + b.w, b.y + b.h), col, 3)
        p = self.save_shot(out, "nahled")
        self.cap.close()
        return p

    # ---- main loop -------------------------------------------------------------
    def run(self):
        sold, skipped = [], []
        try:
            if market_closed_today():
                raise Abort("Dnes je neděle (UTC) – Fish Market má zavřeno.")
            self.attach()
            if not winutil.activate(self.hwnd):
                raise Abort("Nepodařilo se přepnout na okno hry.")
            self.sleep(0.4)
            img = self.grab()
            if vision.find_blue_buttons(img):
                raise Abort("Ve hře je otevřený nějaký dialog. Zavři ho a otevři jen obchod.")
            if not vision.shop_is_open(ocr.read_lines(img)):
                raise Abort("Nevidím okno obchodu „NAŠE DNEŠNÍ CENY“. Otevři ho ve hře a spusť znovu.")
            self.log("Režim NANEČISTO – nic se neprodá." if self.s.dry_run else "Režim OSTRÝ – prodávám.")

            done = {}          # fish id -> decision text
            bad_reads = {}     # name_raw -> attempts
            idle_scrolls = 0
            for _ in range(2000):
                self.check_stop()
                img = self.grab()
                if vision.find_blue_buttons(img):
                    raise Abort("Objevil se nečekaný dialog. Zastavuji pro jistotu.")
                cards, btns = self.scan(img)
                if not btns:
                    raise Abort("Zmizel seznam ryb (zavřel se obchod?).")
                target = None
                for c in cards:
                    m = self.cat.match(c.name, self.s.name_min_score) if c.name else None
                    if not m or not m.fish:
                        key = c.name_raw or f"y{c.button.y}"
                        if c.name_raw and bad_reads.get(key, 0) == 0:
                            self.log(f"  ? „{c.name_raw}“ – {m.note if m else 'nečitelné'} → přeskakuji")
                            self.save_shot(img, f"necitelne_{len(bad_reads)}")
                        bad_reads[key] = bad_reads.get(key, 0) + 1
                        continue
                    if str(m.fish["id"]) in done:
                        continue
                    target = (c, m.fish)
                    break

                if target is None:
                    if self.scroll(img, btns):
                        idle_scrolls = 0
                        continue
                    idle_scrolls += 1
                    if idle_scrolls >= 2:
                        break
                    continue

                c, fish = target
                fid = str(fish["id"])
                if c.price is None:
                    # try once more with a fresh screenshot before giving up
                    if bad_reads.get("price_" + fid, 0) < 2:
                        bad_reads["price_" + fid] = bad_reads.get("price_" + fid, 0) + 1
                        self.pause(0.5)
                        continue
                    done[fid] = "cena nečitelná"
                    self.log(f"  ? {fish['name']}: cenu nejde spolehlivě přečíst ({c.price_detail}) → přeskakuji")
                    skipped.append(fish["name"])
                    continue
                if not c.count:
                    done[fid] = "počet nečitelný"
                    self.log(f"  ? {fish['name']}: počet kusů nejde přečíst → přeskakuji")
                    skipped.append(fish["name"])
                    continue

                d = decide(self.cat, fish, c.price, c.count, self.s)
                done[fid] = d.reason
                self.log(f"{'✔' if d.sell else '·'} {fish['name']} ×{c.count} @ {c.price} FB – {d.reason}")
                if not d.sell:
                    skipped.append(fish["name"])
                    continue
                if self.s.dry_run:
                    sold.append((fish["name"], c.count, c.price, "nanečisto"))
                    continue
                if len([x for x in sold if x[3] != "nanečisto"]) >= self.s.max_sales:
                    self.log("Dosažen limit prodejů na jedno spuštění.")
                    break
                res = self.sell(img, c, fish)
                if res == "retry":
                    done.pop(fid, None)
                    continue
                sold.append(res)
            self.log("Hotovo – prošel jsem celý seznam.")
        except Stopped:
            self.log("■ Zastaveno (STOP / F12 / myš v levém horním rohu).")
        except Abort as e:
            self.log(f"✖ {e}")
        except Exception as e:
            self.log(f"✖ Chyba: {e!r}")
            try:
                self.save_shot(self.cap.grab()[0], "chyba")
            except Exception:
                pass
        finally:
            if self.cap:
                self.cap.close()
        total = sum(q * p for _, q, p, _ in sold)
        word = "Prodal bych" if self.s.dry_run else "Prodáno"
        self.log(f"=== {word}: {len(sold)} druhů, celkem {total} FB. Neprodáno: {len(skipped)}. ===")
        return sold

    # ---- scrolling -------------------------------------------------------------
    def scroll(self, img, btns) -> bool:
        """Scroll the list down. Returns False when it didn't move (= end of list)."""
        sig, (x0, y0, x1, y1) = vision.list_signature(img, btns)
        b = btns[0]
        sx, sy = (x0 + b.x) // 2, (y0 + y1) // 2
        self.ensure_foreground()
        winutil.wheel(self.origin[0] + sx, self.origin[1] + sy, -2)
        self.pause(0.7)
        img2 = self.grab()
        b2 = vision.find_sell_buttons(img2)
        if b2:
            sig2 = vision.list_signature(img2, btns)[0]
            if not vision.same_region(sig, sig2, 3.0):
                return True
        # wheel didn't move it – try dragging the scrollbar
        thumb = vision.find_scroll_thumb(img, btns)
        if thumb:
            cx, cy = thumb.cx, thumb.cy
            dy = max(10, int(thumb.h * 0.6))
            self.ensure_foreground()
            winutil.drag(self.origin[0] + cx, self.origin[1] + cy, self.origin[0] + cx, self.origin[1] + cy + dy)
            self.pause(0.7)
            img3 = self.grab()
            if vision.find_sell_buttons(img3):
                sig3 = vision.list_signature(img3, btns)[0]
                if not vision.same_region(sig, sig3, 3.0):
                    return True
        return False

    # ---- one sale --------------------------------------------------------------
    def sell(self, scan_img, card, fish):
        name = fish["name"]
        # 1) the list must not have moved since we read it
        img = self.grab()
        btns = vision.find_sell_buttons(img)
        b = card.button
        same = [x for x in btns if abs(x.x - b.x) <= 3 and abs(x.y - b.y) <= 3]
        if not same or not vision.same_region(vision.card_signature(scan_img, b), vision.card_signature(img, b)):
            self.log("  (seznam se pohnul, čtu znovu)")
            return "retry"
        money_before = vision.top_bar_numbers(img)

        # 2) click the price button
        self.click(b.cx, b.cy)
        self.pause(0.5)

        # 3) quantity dialog
        def qty_dialog(im):
            blues = vision.find_blue_buttons(im)
            if not blues:
                return None
            lines = ocr.read_lines(im)
            t = vision.find_line(lines, "prodat", "kus")
            return (t, blues[0]) if t else None

        img, r = self.wait_for(qty_dialog, timeout=6)
        if not r:
            raise Abort(f"Po kliknutí na prodej {name} se neobjevil dialog „Prodat kusů“.")
        title, okb = r
        qty = vision.read_qty_dialog(img, title, okb)
        if qty is None:
            self.log(f"  (počet v dialogu nejde přečíst, ponechávám výchozí = vše, čekám {card.count})")
            qty = card.count
        elif qty != card.count:
            self.log(f"  (v dialogu je {qty} ks, v seznamu jsem četl {card.count} – platí dialog)")
        self.save_shot(img, f"prodej_{name}_1dialog")
        self.pause(0.5)

        # 4) confirm
        self.click(okb.cx, okb.cy)
        self.pause(0.5)

        # 5) success message must name the same fish
        def success(im):
            blues = vision.find_blue_buttons(im)
            if not blues:
                return None
            lines = ocr.read_lines(im)
            if vision.find_line(lines, "prodat", "kus"):
                return None  # still the quantity dialog
            sold_name = vision.parse_sold_name(lines, stop_y=blues[0].y)
            if sold_name is not None:
                return ("ok", sold_name, blues[0])
            txt = " / ".join(l.text for l in lines if l.y > im.shape[0] * 0.3 and l.y < im.shape[0] * 0.75)
            return ("other", txt, blues[0])

        img, r = self.wait_for(success, timeout=8)
        if not r:
            raise Abort(f"Po potvrzení prodeje {name} nepřišlo potvrzení ze hry.")
        kind, info, okb2 = r
        self.save_shot(img, f"prodej_{name}_2hotovo")
        if kind != "ok":
            raise Abort(f"Hra odpověděla něčím nečekaným: „{info}“")
        if not self.cat.names_match(info, name):
            raise Abort(f"Hra hlásí prodej „{info}“, ale prodával jsem „{name}“! Zastavuji.")
        self.click(okb2.cx, okb2.cy)
        self.pause(0.5)
        img, gone = self.wait_for(lambda im: not vision.find_blue_buttons(im), timeout=5)
        if not gone:
            raise Abort("Potvrzovací okno se nezavřelo.")

        # 6) money check
        total = card.price * qty
        money_after = vision.top_bar_numbers(img)
        verified = any((b0 + total) in money_after for b0 in money_before)
        if verified:
            self.log(f"  ✔ prodáno {qty}× {name} za {total} FB (peníze sedí)")
        else:
            msg = (f"  ⚠ prodáno {qty}× {name}, ale nárůst peněz o {total} FB se nepodařilo ověřit "
                   f"(před {money_before}, po {money_after})")
            self.log(msg)
            if self.s.strict_money_check:
                raise Abort("Kontrola peněz nesedí – zastavuji pro jistotu (lze vypnout v nastavení).")
        self._write_sale(name, qty, card.price, total, verified)
        self.pause()
        return (name, qty, card.price, "ok" if verified else "neověřeno")

    def _write_sale(self, name, qty, price, total, verified):
        p = os.path.join(LOG_DIR, "prodeje.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["čas", "ryba", "kusů", "cena/ks", "celkem", "ověřeno"])
            w.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), name, qty, price, total,
                        "ano" if verified else "ne"])
