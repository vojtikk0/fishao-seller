"""Tkinter GUI."""
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__, tracker, winutil
from .config import LOG_DIR, Settings

MODES = [("range", "Cena v horní části rozmezí min–max (X % = kde v rozmezí leží)"),
         ("pct_max", "Cena ≥ X % z maxima za období"),
         ("above_avg", "Cena ≥ průměr + X %"),
         ("percentile", "Cena ≥ X. percentil (patří mezi nejlepší dny)")]
NO_DATA = [("skip", "Neprodávat"), ("estimate", "Použít odhad trackeru"), ("sell", "Prodat stejně")]


class App:
    def __init__(self, root):
        self.root = root
        self.s = Settings.load()
        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.data = tracker.load_cache()
        root.title(f"Fishao Seller {__version__}")
        root.minsize(760, 640)
        self._build()
        self._load_into_ui()
        self._refresh_data_label()
        root.after(100, self._drain)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI ------------------------------------------------------------------
    def _build(self):
        r = self.root
        pad = {"padx": 8, "pady": 4}
        main = ttk.Frame(r)
        main.pack(fill="both", expand=True)

        # game
        g = ttk.LabelFrame(main, text="Hra")
        g.pack(fill="x", **pad)
        ttk.Label(g, text="Název okna hry:").grid(row=0, column=0, sticky="w", **pad)
        self.v_title = tk.StringVar()
        ttk.Entry(g, textvariable=self.v_title, width=28).grid(row=0, column=1, sticky="w", **pad)
        ttk.Button(g, text="Najít okno", command=self._find_window).grid(row=0, column=2, **pad)
        self.l_window = ttk.Label(g, text="–")
        self.l_window.grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(g, text="Rozlišení i velikost okna se zjišťují automaticky (okno i celá obrazovka). "
                          "Hra musí být vidět, ne minimalizovaná.", foreground="#666").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=8)

        # rules
        p = ttk.LabelFrame(main, text="Kdy prodat (ceny z fishao-tracker.cz)")
        p.pack(fill="x", **pad)
        ttk.Label(p, text="Pravidlo:").grid(row=0, column=0, sticky="w", **pad)
        self.v_mode = tk.StringVar()
        self.cb_mode = ttk.Combobox(p, state="readonly", width=58, values=[m[1] for m in MODES])
        self.cb_mode.grid(row=0, column=1, columnspan=3, sticky="w", **pad)
        self.cb_mode.bind("<<ComboboxSelected>>", lambda e: self._mode_changed())
        ttk.Label(p, text="X =").grid(row=0, column=4, sticky="e")
        self.v_x = tk.DoubleVar()
        ttk.Spinbox(p, from_=0, to=500, increment=5, textvariable=self.v_x, width=7).grid(row=0, column=5, sticky="w", **pad)
        self.l_x = ttk.Label(p, text="%")
        self.l_x.grid(row=0, column=6, sticky="w")

        ttk.Label(p, text="Historie (dní):").grid(row=1, column=0, sticky="w", **pad)
        self.v_days = tk.IntVar()
        ttk.Spinbox(p, from_=1, to=365, textvariable=self.v_days, width=6).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(p, text="Min. známých dní:").grid(row=1, column=2, sticky="e", **pad)
        self.v_min_samples = tk.IntVar()
        ttk.Spinbox(p, from_=1, to=60, textvariable=self.v_min_samples, width=6).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(p, text="Když je málo dat:").grid(row=2, column=0, sticky="w", **pad)
        self.cb_nodata = ttk.Combobox(p, state="readonly", width=24, values=[n[1] for n in NO_DATA])
        self.cb_nodata.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(p, text="Min. celkem za druh (FB):").grid(row=2, column=3, sticky="e", **pad)
        self.v_min_total = tk.IntVar()
        ttk.Spinbox(p, from_=0, to=1000000, increment=10, textvariable=self.v_min_total, width=8).grid(row=2, column=4, columnspan=2, sticky="w", **pad)
        self.v_verify = tk.BooleanVar()
        ttk.Checkbutton(p, text="Neprodat, když se přečtená cena liší od dnešní ceny v trackeru",
                        variable=self.v_verify).grid(row=3, column=0, columnspan=6, sticky="w", **pad)

        lists = ttk.Frame(p)
        lists.grid(row=4, column=0, columnspan=7, sticky="we", **pad)
        ttk.Label(lists, text="NIKDY neprodávat (1 ryba na řádek):").grid(row=0, column=0, sticky="w")
        ttk.Label(lists, text="VŽDY prodat (bez ohledu na cenu):").grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.t_never = tk.Text(lists, height=4, width=40)
        self.t_never.grid(row=1, column=0, sticky="we")
        self.t_always = tk.Text(lists, height=4, width=40)
        self.t_always.grid(row=1, column=1, sticky="we", padx=(12, 0))

        # safety
        b = ttk.LabelFrame(main, text="Bezpečnost")
        b.pack(fill="x", **pad)
        self.v_dry = tk.BooleanVar()
        ttk.Checkbutton(b, text="NANEČISTO – jen ukázat, co by prodal (nic neklikat na prodej)",
                        variable=self.v_dry).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        self.v_strict = tk.BooleanVar()
        ttk.Checkbutton(b, text="Zastavit, když nesedí kontrola peněz po prodeji",
                        variable=self.v_strict).grid(row=1, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(b, text="Pauza mezi kroky (ms):").grid(row=2, column=0, sticky="w", **pad)
        self.v_delay = tk.IntVar()
        ttk.Spinbox(b, from_=200, to=5000, increment=100, textvariable=self.v_delay, width=7).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(b, text="Max. prodejů za spuštění:").grid(row=2, column=2, sticky="e", **pad)
        self.v_max = tk.IntVar()
        ttk.Spinbox(b, from_=1, to=1000, textvariable=self.v_max, width=6).grid(row=2, column=3, sticky="w", **pad)
        ttk.Label(b, text="Nouzové zastavení: F12 nebo myš do levého horního rohu obrazovky.",
                  foreground="#a00").grid(row=3, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))

        # actions
        a = ttk.Frame(main)
        a.pack(fill="x", **pad)
        ttk.Button(a, text="↻ Načíst ceny z trackeru", command=self._fetch).pack(side="left")
        ttk.Button(a, text="🔍 Test detekce (bez klikání)", command=self._preview).pack(side="left", padx=6)
        self.btn_start = ttk.Button(a, text="▶ START", command=self._start)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop = ttk.Button(a, text="■ STOP", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left")
        ttk.Button(a, text="📁 Logy", command=self._open_logs).pack(side="right")
        self.l_data = ttk.Label(main, text="", foreground="#555")
        self.l_data.pack(fill="x", padx=8)

        lf = ttk.Frame(main)
        lf.pack(fill="both", expand=True, **pad)
        self.t_log = tk.Text(lf, height=14, state="disabled", wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.t_log.yview)
        self.t_log.configure(yscrollcommand=sb.set)
        self.t_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _mode_changed(self):
        idx = self.cb_mode.current()
        key = MODES[idx][0]
        val = {"range": self.s.pct_in_range, "pct_max": self.s.pct_of_max, "above_avg": self.s.pct_above_avg, "percentile": self.s.percentile}[key]
        self.v_x.set(val)
        self.l_x.configure(text="." if key == "percentile" else "%")
        self.v_mode.set(key)

    def _load_into_ui(self):
        s = self.s
        self.v_title.set(s.window_title)
        keys = [m[0] for m in MODES]
        self.cb_mode.current(keys.index(s.mode) if s.mode in keys else 0)
        self._mode_changed()
        self.v_days.set(s.history_days)
        self.v_min_samples.set(s.min_samples)
        nk = [n[0] for n in NO_DATA]
        self.cb_nodata.current(nk.index(s.no_data_action) if s.no_data_action in nk else 0)
        self.v_min_total.set(s.min_total)
        self.v_verify.set(s.verify_with_tracker)
        self.t_never.insert("1.0", "\n".join(s.never_sell))
        self.t_always.insert("1.0", "\n".join(s.always_sell))
        self.v_dry.set(s.dry_run)
        self.v_strict.set(s.strict_money_check)
        self.v_delay.set(s.action_delay_ms)
        self.v_max.set(s.max_sales)

    def _collect(self) -> bool:
        s = self.s
        try:
            s.window_title = self.v_title.get().strip() or "FISHAO - Reborn"
            s.mode = MODES[self.cb_mode.current()][0]
            x = float(self.v_x.get())
            if s.mode == "range":
                s.pct_in_range = min(100.0, x)
            elif s.mode == "pct_max":
                s.pct_of_max = x
            elif s.mode == "above_avg":
                s.pct_above_avg = x
            else:
                s.percentile = min(100.0, x)
            s.history_days = int(self.v_days.get())
            s.min_samples = int(self.v_min_samples.get())
            s.no_data_action = NO_DATA[self.cb_nodata.current()][0]
            s.min_total = int(self.v_min_total.get())
            s.verify_with_tracker = bool(self.v_verify.get())
            s.never_sell = [l.strip() for l in self.t_never.get("1.0", "end").splitlines() if l.strip()]
            s.always_sell = [l.strip() for l in self.t_always.get("1.0", "end").splitlines() if l.strip()]
            s.dry_run = bool(self.v_dry.get())
            s.strict_money_check = bool(self.v_strict.get())
            s.action_delay_ms = int(self.v_delay.get())
            s.max_sales = int(self.v_max.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Chyba", "Některé číselné pole má neplatnou hodnotu.")
            return False
        s.save()
        return True

    # ---- logging ---------------------------------------------------------------
    def log(self, msg):
        self.q.put(("log", msg))

    def _drain(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.t_log.configure(state="normal")
                    self.t_log.insert("end", val + "\n")
                    self.t_log.see("end")
                    self.t_log.configure(state="disabled")
                elif kind == "done":
                    self._set_running(False)
                    self._refresh_data_label()
                elif kind == "image":
                    self._show_image(val)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _refresh_data_label(self):
        if self.data:
            age = tracker.cache_age_min(self.data)
            self.l_data.configure(text=f"Ceny z trackeru: {len(self.data.get('fish', []))} ryb, staženo před {age:.0f} min.")
        else:
            self.l_data.configure(text="Ceny z trackeru zatím nestaženy.")

    def _set_running(self, running):
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")

    # ---- actions ---------------------------------------------------------------
    def _find_window(self):
        self._collect()
        hwnd = winutil.find_window(self.s.window_title)
        if hwnd:
            x, y, w, h = winutil.client_rect(hwnd)
            self.l_window.configure(text=f"✔ nalezeno, {w}×{h}", foreground="#070")
        else:
            self.l_window.configure(text="✖ nenalezeno", foreground="#a00")

    def _run_bg(self, fn):
        if self.worker and self.worker.is_alive():
            return
        if not self._collect():
            return
        self.stop_event.clear()
        self._set_running(True)

        def wrap():
            try:
                fn()
            except Exception as e:
                self.log(f"✖ {e}")
            finally:
                self.q.put(("done", None))

        self.worker = threading.Thread(target=wrap, daemon=True)
        self.worker.start()

    def _ensure_data(self, force=False):
        if force or not self.data or tracker.cache_age_min(self.data) > self.s.tracker_max_age_min:
            self.data = tracker.fetch(log=self.log)
        return self.data

    def _fetch(self):
        self._run_bg(lambda: self._ensure_data(force=True))

    def _preview(self):
        def job():
            from .bot import Bot
            data = self._ensure_data()
            self.log("— Test detekce —")
            p = Bot(self.s, data, self.log, self.stop_event).preview()
            self.log(f"Náhled uložen: {p}")
            self.q.put(("image", p))
        self._run_bg(job)

    def _start(self):
        if not self.v_dry.get():
            if not messagebox.askyesno("Ostrý režim", "Režim NANEČISTO je vypnutý – bot bude opravdu prodávat.\n"
                                       "Máš otevřený obchod „Naše dnešní ceny“?\n\nPokračovat?"):
                return

        def job():
            from .bot import Bot
            data = self._ensure_data()
            self.log("— START —  (F12 = stop)")
            Bot(self.s, data, self.log, self.stop_event).run()
        self._run_bg(job)

    def _stop(self):
        self.stop_event.set()

    def _open_logs(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)

    def _show_image(self, path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            sw, sh = self.root.winfo_screenwidth() * 0.8, self.root.winfo_screenheight() * 0.8
            k = min(1.0, sw / im.width, sh / im.height)
            im = im.resize((int(im.width * k), int(im.height * k)))
            top = tk.Toplevel(self.root)
            top.title("Náhled detekce – zelená = prodal by, červená = neprodá, oranžová = nečitelné")
            ph = ImageTk.PhotoImage(im)
            lbl = ttk.Label(top, image=ph)
            lbl.image = ph
            lbl.pack()
        except Exception as e:
            self.log(f"(náhled nejde zobrazit: {e})")

    def _on_close(self):
        self.stop_event.set()
        try:
            self._collect()
        except Exception:
            pass
        self.root.destroy()


def main():
    winutil.set_dpi_aware()
    root = tk.Tk()
    try:
        import sys
        base = getattr(sys, "_MEIPASS", None)
        pkg = os.path.join(base, "fishao_seller") if base else os.path.dirname(os.path.abspath(__file__))
        root.iconbitmap(os.path.join(pkg, "ui", "icon.ico"))
    except Exception:
        pass
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
