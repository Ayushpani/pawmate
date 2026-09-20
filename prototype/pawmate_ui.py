"""Furnished UI: a real dashboard, speech bubbles, and a styled command bar.

Replaces the stock tkinter dialogs (simpledialog/messagebox), which look like
Windows 95 and were the main thing making this feel unfinished.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from pawmate_tracking import fmt_minutes

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

BG = "#15171c"
CARD = "#1e2128"
CARD_HI = "#262a33"
FG = "#e8eaee"
MUTED = "#9aa3b2"
ACCENT = "#5eccaa"
WARN = "#e8a33d"
BAD = "#e2686f"
GOOD = "#5eccaa"

CAT_COLORS = {"productive": GOOD, "neutral": "#6f9ad6", "distracting": BAD, "": "#3a3f4a"}


def _font(size=10, weight="normal"):
    return tkfont.Font(family="Segoe UI", size=size, weight=weight)


def round_rect(cv: tk.Canvas, x0, y0, x1, y1, r=10, **kw):
    """Rounded rectangle on a canvas (tkinter has no native one)."""
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class Panel(tk.Toplevel):
    """Borderless dark panel with a drag strip and Esc-to-close."""

    def __init__(self, master, title: str, w: int, h: int):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(40, (sh - h) // 3)}")

        bar = tk.Frame(self, bg=BG, height=38)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text=title, bg=BG, fg=FG, font=_font(11, "bold")).pack(side="left", padx=14)
        close = tk.Label(bar, text="✕", bg=BG, fg=MUTED, font=_font(11), cursor="hand2")
        close.pack(side="right", padx=14)
        close.bind("<Button-1>", lambda e: self.destroy())
        close.bind("<Enter>", lambda e: close.config(fg=FG))
        close.bind("<Leave>", lambda e: close.config(fg=MUTED))

        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)

        for w_ in (bar, self):
            w_.bind("<ButtonPress-1>", self._drag_start)
            w_.bind("<B1-Motion>", self._drag)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(60, lambda: (self.focus_force(), self.lift()))

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class Dashboard(Panel):
    """Today at a glance: score, split, timeline, top apps (plan T5 / R1)."""

    def __init__(self, master, stats: dict, on_water=None):
        super().__init__(master, "Today", 620, 560)
        self.on_water = on_water
        self._build(stats)

    def _card(self, parent, **kw):
        f = tk.Frame(parent, bg=CARD, **kw)
        return f

    def _build(self, s: dict):
        pad = {"padx": 14, "pady": 7}
        top = tk.Frame(self.body, bg=BG)
        top.pack(fill="x", **pad)

        # --- score ring ---
        ring = self._card(top)
        ring.pack(side="left", fill="y", padx=(0, 12))
        cv = tk.Canvas(ring, width=150, height=150, bg=CARD, highlightthickness=0)
        cv.pack(padx=12, pady=12)
        score = s["score"]
        col = GOOD if score >= 70 else (WARN if score >= 45 else BAD)
        cv.create_oval(16, 16, 134, 134, outline="#2b303a", width=12)
        if score > 0:
            cv.create_arc(16, 16, 134, 134, start=90, extent=-3.6 * score,
                          outline=col, width=12, style="arc")
        cv.create_text(75, 68, text=str(score), fill=FG, font=_font(28, "bold"))
        cv.create_text(75, 96, text="focus score", fill=MUTED, font=_font(8))

        # --- headline numbers ---
        nums = tk.Frame(top, bg=BG)
        nums.pack(side="left", fill="both", expand=True)
        for label, val, c in (
            ("Active", fmt_minutes(s["active_min"]), FG),
            ("Deep work", fmt_minutes(s["deep_work_min"]), GOOD),
            ("Distracting", fmt_minutes(s["distracting_min"]), BAD),
            ("App switches", str(s["switches"]), MUTED),
        ):
            row = self._card(nums)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=CARD, fg=MUTED, font=_font(9)).pack(side="left", padx=12, pady=7)
            tk.Label(row, text=val, bg=CARD, fg=c, font=_font(12, "bold")).pack(side="right", padx=12)

        # --- category split bar ---
        split = self._card(self.body)
        split.pack(fill="x", **pad)
        tk.Label(split, text="Where the time went", bg=CARD, fg=MUTED,
                 font=_font(9)).pack(anchor="w", padx=12, pady=(10, 4))
        bar = tk.Canvas(split, height=22, bg=CARD, highlightthickness=0)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        self.after(50, lambda: self._draw_split(bar, s))

        # --- timeline ---
        tl_card = self._card(self.body)
        tl_card.pack(fill="x", **pad)
        tk.Label(tl_card, text="Timeline (00:00 → 24:00)", bg=CARD, fg=MUTED,
                 font=_font(9)).pack(anchor="w", padx=12, pady=(10, 4))
        self.timeline = tk.Canvas(tl_card, height=34, bg=CARD, highlightthickness=0)
        self.timeline.pack(fill="x", padx=12, pady=(0, 12))

        # --- top apps ---
        apps = self._card(self.body)
        apps.pack(fill="both", expand=True, **pad)
        tk.Label(apps, text="Top apps", bg=CARD, fg=MUTED,
                 font=_font(9)).pack(anchor="w", padx=12, pady=(10, 6))
        total = max(1.0, sum(v for _, v in s["top_apps"]) or 1.0)
        if not s["top_apps"]:
            tk.Label(apps, text="Nothing tracked yet — leave it running for a few minutes.",
                     bg=CARD, fg=MUTED, font=_font(9)).pack(anchor="w", padx=12, pady=6)
        for name, secs in s["top_apps"]:
            row = tk.Frame(apps, bg=CARD)
            row.pack(fill="x", padx=12, pady=2)
            tk.Label(row, text=name[:26], bg=CARD, fg=FG, font=_font(9),
                     width=20, anchor="w").pack(side="left")
            tk.Label(row, text=fmt_minutes(secs / 60), bg=CARD, fg=MUTED,
                     font=_font(9), width=7, anchor="e").pack(side="right")
            track = tk.Canvas(row, height=10, bg=CARD_HI, highlightthickness=0)
            track.pack(side="left", fill="x", expand=True, padx=8)
            track.bind("<Configure>",
                       lambda e, c=track, f=secs / total: (
                           c.delete("b"),
                           c.create_rectangle(0, 0, max(3, e.width * f), 10,
                                              fill=ACCENT, width=0, tags="b")))

        # --- footer ---
        foot = tk.Frame(self.body, bg=BG)
        foot.pack(fill="x", **pad)
        tk.Label(foot, text=f"Water  ·  {s['water']} glasses today", bg=BG, fg=MUTED,
                 font=_font(9)).pack(side="left")
        if self.on_water:
            b = tk.Label(foot, text="+ log a glass", bg=CARD, fg=ACCENT,
                         font=_font(9), padx=10, pady=5, cursor="hand2")
            b.pack(side="right")
            b.bind("<Button-1>", lambda e: (self.on_water(), self.destroy()))

    def _draw_split(self, bar: tk.Canvas, s: dict):
        bar.delete("all")
        w = bar.winfo_width() or 560
        total = max(1.0, s["productive_min"] + s["neutral_min"] + s["distracting_min"])
        x = 0.0
        for key, col in (("productive_min", GOOD), ("neutral_min", CAT_COLORS["neutral"]),
                         ("distracting_min", BAD)):
            seg = w * (s[key] / total)
            if seg > 0.5:
                bar.create_rectangle(x, 0, x + seg, 22, fill=col, width=0)
            x += seg

    def draw_timeline(self, segments, day_start: float):
        cv = self.timeline
        cv.delete("all")
        w = cv.winfo_width() or 560
        cv.create_rectangle(0, 8, w, 26, fill=CARD_HI, width=0)
        for r in segments:
            a = (r["start_ts"] - day_start) / 86400.0
            b = (r["end_ts"] - day_start) / 86400.0
            if b <= a:
                continue
            col = "#333945" if r["idle"] else CAT_COLORS.get(r["category"] or "", "#6f9ad6")
            cv.create_rectangle(a * w, 8, max(a * w + 1, b * w), 26, fill=col, width=0)
        for h in range(0, 25, 6):
            x = w * h / 24.0
            cv.create_line(x, 26, x, 30, fill=MUTED)
            cv.create_text(min(w - 10, max(10, x)), 33, text=f"{h:02d}", fill=MUTED,
                           font=_font(7), anchor="n")


# ---------------------------------------------------------------------------
# Speech bubble
# ---------------------------------------------------------------------------

class Bubble(tk.Toplevel):
    """A small rounded speech bubble that floats above the pet."""

    KEY = "#ff00ff"

    def __init__(self, master, text: str, x: int, y: int,
                 actions: list[tuple[str, callable]] | None = None, timeout_ms: int = 6000):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        self.configure(bg=self.KEY)

        f = _font(10)
        actions = actions or []
        lines = text.split("\n")
        tw = max(f.measure(l) for l in lines) + 28
        bw = max(150, min(320, tw))
        bh = 26 + 18 * len(lines) + (34 if actions else 0)

        self.cv = tk.Canvas(self, width=bw, height=bh + 10, bg=self.KEY, highlightthickness=0)
        self.cv.pack()
        round_rect(self.cv, 2, 2, bw - 2, bh, r=12, fill="#20242c", outline=ACCENT)
        self.cv.create_polygon(bw // 2 - 8, bh, bw // 2 + 8, bh, bw // 2, bh + 9,
                               fill="#20242c", outline="")
        self.cv.create_text(bw // 2, 14 + 9 * (len(lines) - 1), text=text,
                            fill=FG, font=f, justify="center")

        bx = bw // 2 - (len(actions) * 74) // 2
        for i, (label, cb) in enumerate(actions):
            x0 = bx + i * 74
            tag = f"btn{i}"
            round_rect(self.cv, x0, bh - 30, x0 + 66, bh - 8, r=8,
                       fill=CARD_HI, outline="", tags=tag)
            self.cv.create_text(x0 + 33, bh - 19, text=label, fill=ACCENT, font=_font(9), tags=tag)
            self.cv.tag_bind(tag, "<Button-1>", lambda e, c=cb: (c(), self.destroy()))
            self.cv.config(cursor="hand2")

        sw = self.winfo_screenwidth()
        self.geometry(f"+{max(4, min(sw - bw - 4, x - bw // 2))}+{max(4, y - bh - 14)}")
        if timeout_ms:
            self.after(timeout_ms, self.destroy)


# ---------------------------------------------------------------------------
# Command bar
# ---------------------------------------------------------------------------

class CommandBar(Panel):
    """Styled chat/command input with hint chips — replaces simpledialog."""

    def __init__(self, master, on_submit, hint: str = "", suggestions=()):
        super().__init__(master, "Ask your pet", 520, 176)
        self.on_submit = on_submit

        self.entry = tk.Entry(self.body, bg=CARD, fg=FG, insertbackground=ACCENT,
                              relief="flat", font=_font(12))
        self.entry.pack(fill="x", padx=16, pady=(6, 8), ipady=9)
        self.entry.bind("<Return>", self._go)
        self.entry.bind("<Escape>", lambda e: self.destroy())
        self.after(80, self.entry.focus_force)

        chips = tk.Frame(self.body, bg=BG)
        chips.pack(fill="x", padx=16)
        for s in suggestions:
            c = tk.Label(chips, text=s, bg=CARD_HI, fg=MUTED, font=_font(8),
                         padx=8, pady=4, cursor="hand2")
            c.pack(side="left", padx=(0, 6))
            c.bind("<Button-1>", lambda e, t=s: (self.entry.delete(0, "end"),
                                                 self.entry.insert(0, t), self.entry.focus_set()))

        if hint:
            tk.Label(self.body, text=hint, bg=BG, fg=MUTED, font=_font(8),
                     justify="left", wraplength=480).pack(anchor="w", padx=16, pady=(10, 0))

    def _go(self, _e=None):
        text = self.entry.get().strip()
        self.destroy()
        if text:
            self.on_submit(text)


def toast(master, text: str, ok: bool = True):
    """Brief bottom-centre notification — replaces messagebox.showinfo."""
    t = tk.Toplevel(master)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    t.configure(bg=BG)
    tk.Label(t, text=text, bg=CARD, fg=(FG if ok else BAD), font=_font(10),
             padx=18, pady=11, wraplength=460, justify="left").pack()
    t.update_idletasks()
    sw = t.winfo_screenwidth()
    sh = t.winfo_screenheight()
    t.geometry(f"+{(sw - t.winfo_width()) // 2}+{sh - 170}")
    t.after(3600, t.destroy)
    return t


def confirm(master, text: str, on_yes, yes="Yes", no="Cancel"):
    """Styled yes/no — replaces messagebox.askyesno, same guarantee:
    nothing destructive happens without an explicit click."""
    p = Panel(master, "Confirm", 420, 170)
    tk.Label(p.body, text=text, bg=BG, fg=FG, font=_font(10),
             wraplength=380, justify="left").pack(padx=18, pady=(10, 16), anchor="w")
    row = tk.Frame(p.body, bg=BG)
    row.pack(fill="x", padx=18)

    def mk(parent, label, colour, cb):
        b = tk.Label(parent, text=label, bg=CARD_HI, fg=colour, font=_font(10),
                     padx=16, pady=8, cursor="hand2")
        b.bind("<Button-1>", lambda e: (p.destroy(), cb()))
        return b

    mk(row, yes, ACCENT, on_yes).pack(side="right", padx=(8, 0))
    mk(row, no, MUTED, lambda: None).pack(side="right")
    p.bind("<Return>", lambda e: (p.destroy(), on_yes()))
    return p


# ---------------------------------------------------------------------------
# Todo list
# ---------------------------------------------------------------------------

class TodoPanel(Panel):
    """The day's task list. One task can be marked 'working on this now',
    which is what lets the pet tell on-task from off-task."""

    def __init__(self, master, todos, on_change=None):
        super().__init__(master, "Today's tasks", 520, 520)
        self.todos = todos
        self.on_change = on_change

        add = tk.Frame(self.body, bg=BG)
        add.pack(fill="x", padx=16, pady=(4, 10))
        self.entry = tk.Entry(add, bg=CARD, fg=FG, insertbackground=ACCENT,
                              relief="flat", font=_font(11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", self._add)
        btn = tk.Label(add, text="+ add", bg=CARD_HI, fg=ACCENT, font=_font(10),
                       padx=14, pady=7, cursor="hand2")
        btn.pack(side="right", padx=(8, 0))
        btn.bind("<Button-1>", self._add)

        self.progress = tk.Label(self.body, text="", bg=BG, fg=MUTED, font=_font(9))
        self.progress.pack(anchor="w", padx=16)

        self.list_frame = tk.Frame(self.body, bg=BG)
        self.list_frame.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(self.body, text="Click a task to mark it done · ▶ sets what you're working on",
                 bg=BG, fg=MUTED, font=_font(8)).pack(anchor="w", padx=16, pady=(0, 10))
        self.refresh()
        self.after(90, self.entry.focus_force)

    def _add(self, _e=None):
        text = self.entry.get().strip()
        if not text:
            return
        self.todos.add(text)
        self.entry.delete(0, "end")
        self.refresh()

    def refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        rows = self.todos.list()
        done, total = self.todos.progress()
        self.progress.config(text=f"{done} of {total} done" if total else "nothing planned yet")

        if not rows:
            tk.Label(self.list_frame, text="Add what you want to get done today.",
                     bg=BG, fg=MUTED, font=_font(9)).pack(anchor="w", padx=8, pady=10)
        for r in rows:
            self._row(r)
        if self.on_change:
            self.on_change()

    def _row(self, r):
        active = bool(r["active"])
        done = bool(r["done"])
        bg = CARD_HI if active else CARD
        row = tk.Frame(self.list_frame, bg=bg)
        row.pack(fill="x", pady=2, padx=6)

        box = tk.Label(row, text="✓" if done else "○", bg=bg,
                       fg=(ACCENT if done else MUTED), font=_font(12), cursor="hand2",
                       padx=10, pady=8)
        box.pack(side="left")
        box.bind("<Button-1>", lambda e, i=r["id"], d=done: (
            self.todos.set_done(i, not d), self.refresh()))

        label = tk.Label(row, text=r["text"][:48], bg=bg,
                         fg=(MUTED if done else FG), font=_font(10), anchor="w", cursor="hand2")
        label.pack(side="left", fill="x", expand=True, pady=8)
        label.bind("<Button-1>", lambda e, i=r["id"], d=done: (
            self.todos.set_done(i, not d), self.refresh()))

        if r["spent_s"]:
            tk.Label(row, text=fmt_minutes(r["spent_s"] / 60), bg=bg, fg=MUTED,
                     font=_font(8)).pack(side="right", padx=6)

        if not done:
            play = tk.Label(row, text="▶" if not active else "■", bg=bg,
                            fg=(ACCENT if active else MUTED), font=_font(10),
                            cursor="hand2", padx=10)
            play.pack(side="right")
            play.bind("<Button-1>", lambda e, i=r["id"], a=active: (
                self.todos.set_active(None if a else i), self.refresh()))

        dele = tk.Label(row, text="✕", bg=bg, fg="#59606d", font=_font(9),
                        cursor="hand2", padx=8)
        dele.pack(side="right")
        dele.bind("<Button-1>", lambda e, i=r["id"]: (self.todos.delete(i), self.refresh()))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsPanel(Panel):
    """Everything about reminders is adjustable here (plan §4.13)."""

    FIELDS = [
        ("water_every_min", "Water reminder", "every N minutes of active time", 0, 180),
        ("break_every_min", "Stretch break", "every N minutes", 0, 240),
        ("eye_every_min", "Eye break (20-20-20)", "every N minutes", 0, 120),
        ("eye_duration_s", "Eye break length", "seconds", 5, 120),
        ("idle_threshold_s", "Count as idle after", "seconds with no input", 30, 900),
        ("water_goal", "Daily water goal", "glasses", 1, 20),
    ]

    def __init__(self, master, settings, on_save=None, rules=None):
        super().__init__(master, "Settings", 560, 620)
        self.settings = settings
        self.on_save = on_save
        self.rules = rules
        self.vars: dict[str, tk.StringVar] = {}

        tk.Label(self.body, text="Reminders", bg=BG, fg=MUTED,
                 font=_font(9, "bold")).pack(anchor="w", padx=18, pady=(4, 6))
        for key, label, hint, lo, hi in self.FIELDS:
            self._field(key, label, hint, lo, hi)

        tk.Label(self.body, text="Breaks", bg=BG, fg=MUTED,
                 font=_font(9, "bold")).pack(anchor="w", padx=18, pady=(12, 6))
        self.block_var = tk.BooleanVar(value=bool(settings.get("blocking_breaks")))
        self.water_block_var = tk.BooleanVar(value=bool(settings.get("water_blocking")))
        self._check("Breaks hold the screen (Esc always skips)", self.block_var)
        self._check("Water reminder holds the screen too", self.water_block_var)

        tk.Label(self.body, text="0 turns a reminder off entirely.",
                 bg=BG, fg=MUTED, font=_font(8)).pack(anchor="w", padx=18, pady=(10, 0))

        foot = tk.Frame(self.body, bg=BG)
        foot.pack(fill="x", padx=18, pady=14, side="bottom")
        save = tk.Label(foot, text="Save", bg=CARD_HI, fg=ACCENT, font=_font(10),
                        padx=18, pady=8, cursor="hand2")
        save.pack(side="right")
        save.bind("<Button-1>", lambda e: self._save())
        if self.rules is not None:
            mgr = tk.Label(foot, text="Category overrides…", bg=BG, fg=MUTED,
                           font=_font(9), cursor="hand2")
            mgr.pack(side="left")
            mgr.bind("<Button-1>", lambda e: RulesPanel(self.master, self.rules))

    def _field(self, key, label, hint, lo, hi):
        row = tk.Frame(self.body, bg=CARD)
        row.pack(fill="x", padx=14, pady=3)
        tk.Label(row, text=label, bg=CARD, fg=FG, font=_font(10),
                 anchor="w").pack(side="left", padx=12, pady=9)
        v = tk.StringVar(value=str(self.settings.get(key)))
        self.vars[key] = v
        e = tk.Entry(row, textvariable=v, bg=CARD_HI, fg=FG, insertbackground=ACCENT,
                     relief="flat", width=6, justify="center", font=_font(10))
        e.pack(side="right", padx=12, ipady=4)
        tk.Label(row, text=hint, bg=CARD, fg=MUTED,
                 font=_font(8)).pack(side="right", padx=4)

    def _check(self, label, var):
        row = tk.Frame(self.body, bg=CARD)
        row.pack(fill="x", padx=14, pady=3)
        c = tk.Checkbutton(row, text=label, variable=var, bg=CARD, fg=FG,
                           selectcolor=CARD_HI, activebackground=CARD,
                           activeforeground=FG, font=_font(10), anchor="w",
                           highlightthickness=0, bd=0)
        c.pack(side="left", padx=8, pady=7, fill="x")

    def _save(self):
        for key, _l, _h, lo, hi in self.FIELDS:
            try:
                val = int(float(self.vars[key].get()))
            except ValueError:
                continue
            self.settings.set(key, max(lo if key != "water_every_min" else 0, min(hi, val)))
        self.settings.set("blocking_breaks", bool(self.block_var.get()))
        self.settings.set("water_blocking", bool(self.water_block_var.get()))
        self.destroy()
        if self.on_save:
            self.on_save()


class RulesPanel(Panel):
    """Shows and removes user category overrides (the YouTube-lecture fix)."""

    def __init__(self, master, rules):
        super().__init__(master, "Category overrides", 520, 420)
        self.rules = rules
        tk.Label(self.body,
                 text="These win over the built-in rules. Re-tag from the pet's\n"
                      "right-click menu while the app is focused.",
                 bg=BG, fg=MUTED, font=_font(9), justify="left").pack(anchor="w", padx=18, pady=(4, 10))
        self.list_frame = tk.Frame(self.body, bg=BG)
        self.list_frame.pack(fill="both", expand=True, padx=10)
        self.refresh()

    def refresh(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        items = self.rules.list_all()
        if not items:
            tk.Label(self.list_frame, text="No overrides yet.", bg=BG, fg=MUTED,
                     font=_font(9)).pack(anchor="w", padx=10, pady=8)
        for kind, pattern, cat in items:
            row = tk.Frame(self.list_frame, bg=CARD)
            row.pack(fill="x", pady=2, padx=6)
            tk.Label(row, text=f"{kind}: “{pattern}”", bg=CARD, fg=FG, font=_font(9),
                     anchor="w").pack(side="left", padx=12, pady=8, fill="x", expand=True)
            tk.Label(row, text=cat, bg=CARD, fg=CAT_COLORS.get(cat, FG),
                     font=_font(9, "bold")).pack(side="right", padx=10)
            x = tk.Label(row, text="✕", bg=CARD, fg="#59606d", font=_font(9), cursor="hand2")
            x.pack(side="right", padx=8)
            x.bind("<Button-1>", lambda e, k=kind, p=pattern: (
                self.rules.remove(k, p), self.refresh()))


class RetagDialog(Panel):
    """Re-tag whatever is currently focused, and remember it."""

    def __init__(self, master, exe: str, title: str, on_pick):
        super().__init__(master, "Re-tag this", 520, 300)
        shown = (title or exe or "unknown")[:70]
        tk.Label(self.body, text=shown, bg=BG, fg=FG, font=_font(11, "bold"),
                 wraplength=470, justify="left").pack(anchor="w", padx=18, pady=(6, 2))
        tk.Label(self.body, text=f"({exe})", bg=BG, fg=MUTED,
                 font=_font(8)).pack(anchor="w", padx=18)

        tk.Label(self.body, text="Remember this as:", bg=BG, fg=MUTED,
                 font=_font(9)).pack(anchor="w", padx=18, pady=(14, 6))

        # Match on a distinctive word from the title so the rule generalises
        # (e.g. a channel or course name), not on the exact full title.
        words = [w for w in (title or "").lower().replace("-", " ").split() if len(w) > 3]
        self.key_var = tk.StringVar(value=(words[0] if words else (exe or "").lower()))
        e = tk.Entry(self.body, textvariable=self.key_var, bg=CARD, fg=FG,
                     insertbackground=ACCENT, relief="flat", font=_font(10))
        e.pack(fill="x", padx=18, ipady=6)
        tk.Label(self.body, text="any window title containing this word gets that category",
                 bg=BG, fg=MUTED, font=_font(8)).pack(anchor="w", padx=18, pady=(4, 12))

        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", padx=18)
        for cat, col in (("productive", GOOD), ("neutral", CAT_COLORS["neutral"]),
                         ("distracting", BAD)):
            b = tk.Label(row, text=cat, bg=CARD_HI, fg=col, font=_font(10),
                         padx=14, pady=9, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda ev, c=cat: (
                self.destroy(), on_pick(self.key_var.get().strip(), c)))
