"""Panels, rendered in the paper/ink visual language (see pawmate_theme).

Everything here is typography and hairlines. No emoji, no icon artwork, no
gradients or glow — marks that need drawing are drawn from canvas primitives
so nothing can fall back to an empty glyph box on a machine missing a font.
"""
from __future__ import annotations

import tkinter as tk

from pawmate_theme import (CATEGORY, CLAY, COOL, DEEP, IDLE, INK, INK_2, INK_3,
                           INK_4, LINE, OCHRE, PAPER, SERIF, SUNK, SURFACE,
                           Button, Scroller, Window, bar, caption, check_mark,
                           font)
from pawmate_tracking import fmt_minutes


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def stat(parent, value: str, label: str, colour=INK, bg=PAPER):
    """A large numeral over a small caption — the core display unit."""
    box = tk.Frame(parent, bg=bg)
    tk.Label(box, text=value, bg=bg, fg=colour,
             font=font(21, "normal", family=SERIF)).pack(anchor="w")
    tk.Label(box, text=label.upper(), bg=bg, fg=INK_3,
             font=font(8, "bold")).pack(anchor="w", pady=(1, 0))
    return box


def toast(master, text: str, ok: bool = True):
    t = tk.Toplevel(master)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    t.configure(bg=LINE)
    inner = tk.Frame(t, bg=SURFACE)
    inner.pack(padx=1, pady=1)
    tk.Label(inner, text=text, bg=SURFACE, fg=(INK if ok else CLAY),
             font=font(10), padx=20, pady=12, wraplength=460,
             justify="left").pack()
    t.update_idletasks()
    sw, sh = t.winfo_screenwidth(), t.winfo_screenheight()
    t.geometry(f"+{(sw - t.winfo_width()) // 2}+{sh - 180}")
    t.after(3600, t.destroy)
    return t


def confirm(master, text: str, on_yes, yes="Continue", no="Cancel"):
    """Same guarantee as always: nothing destructive runs without a click."""
    p = Window(master, "Confirm", w=440, h=210)
    tk.Label(p.body, text=text, bg=PAPER, fg=INK, font=font(11),
             wraplength=380, justify="left").pack(padx=26, pady=(22, 20), anchor="w")
    row = tk.Frame(p.body, bg=PAPER)
    row.pack(fill="x", padx=26)
    Button(row, yes, command=lambda: (p.destroy(), on_yes()),
           primary=True, bg=PAPER).pack(side="right", padx=(10, 0))
    Button(row, no, command=p.destroy, bg=PAPER).pack(side="right")
    p.bind("<Return>", lambda e: (p.destroy(), on_yes()))
    return p


class Bubble(tk.Toplevel):
    """Speech bubble above the pet."""

    KEY = "#ff00ff"

    def __init__(self, master, text: str, x: int, y: int,
                 actions=None, timeout_ms: int = 6000):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        self.configure(bg=self.KEY)

        shell = tk.Frame(self, bg=LINE)
        shell.pack(padx=6, pady=6)
        inner = tk.Frame(shell, bg=SURFACE)
        inner.pack(padx=1, pady=1)

        tk.Label(inner, text=text, bg=SURFACE, fg=INK, font=font(10),
                 justify="left", wraplength=260, padx=16, pady=12).pack(anchor="w")
        if actions:
            tk.Frame(inner, bg=LINE, height=1).pack(fill="x")
            row = tk.Frame(inner, bg=SURFACE)
            row.pack(fill="x")
            for i, (lbl, cb) in enumerate(actions):
                if i:
                    tk.Frame(row, bg=LINE, width=1).pack(side="left", fill="y")
                b = tk.Label(row, text=lbl, bg=SURFACE,
                             fg=(DEEP if i == 0 else INK_3),
                             font=font(9, "bold" if i == 0 else "normal"),
                             padx=18, pady=9, cursor="hand2")
                b.pack(side="left", fill="x", expand=True)
                b.bind("<Button-1>", lambda e, c=cb: (c(), self.destroy()))
                b.bind("<Enter>", lambda e, w=b: w.config(bg=SUNK))
                b.bind("<Leave>", lambda e, w=b: w.config(bg=SURFACE))

        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw = self.winfo_screenwidth()
        self.geometry(f"+{max(4, min(sw - w - 4, x - w // 2))}+{max(4, y - h - 6)}")
        if timeout_ms:
            self.after(timeout_ms, self.destroy)


class CommandBar(Window):
    """The one input for everything."""

    def __init__(self, master, on_submit, hint: str = "", suggestions=()):
        super().__init__(master, "Ask", hint, w=560, h=190)
        self.on_submit = on_submit

        wrap = tk.Frame(self.body, bg=LINE)
        wrap.pack(fill="x", padx=26, pady=(18, 10))
        self.entry = tk.Entry(wrap, bg=SURFACE, fg=INK, insertbackground=DEEP,
                              relief="flat", font=font(13))
        self.entry.pack(fill="x", padx=1, pady=1, ipady=10)
        self.entry.bind("<Return>", self._go)
        self.entry.bind("<Escape>", lambda e: self.destroy())
        self.after(80, self.entry.focus_force)

        chips = tk.Frame(self.body, bg=PAPER)
        chips.pack(fill="x", padx=26)
        for s in suggestions:
            c = tk.Label(chips, text=s, bg=SUNK, fg=INK_2, font=font(8),
                         padx=9, pady=4, cursor="hand2")
            c.pack(side="left", padx=(0, 6))
            c.bind("<Button-1>", lambda e, t=s: (self.entry.delete(0, "end"),
                                                 self.entry.insert(0, t),
                                                 self.entry.focus_set()))

    def _go(self, _e=None):
        text = self.entry.get().strip()
        self.destroy()
        if text:
            self.on_submit(text)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class Dashboard(Window):
    def __init__(self, master, stats: dict, segments=None, day_start=0.0,
                 on_water=None, on_digest=None):
        super().__init__(master, "Today", "where the time actually went",
                         w=720, h=620)
        self.on_water = on_water
        self._build(stats, segments or [], day_start, on_digest)

    def _build(self, s, segments, day_start, on_digest):
        top = tk.Frame(self.body, bg=PAPER)
        top.pack(fill="x", padx=26, pady=(22, 18))
        for i, (val, lbl, col) in enumerate((
            (str(s["score"]), "focus score",
             DEEP if s["score"] >= 70 else (OCHRE if s["score"] >= 45 else CLAY)),
            (fmt_minutes(s["active_min"]), "at the desk", INK),
            (fmt_minutes(s["deep_work_min"]), "deep work", DEEP),
            (fmt_minutes(s["distracting_min"]), "leaked", CLAY),
        )):
            stat(top, val, lbl, col).pack(side="left", padx=(0, 46) if i < 3 else 0)

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26)

        band_wrap = tk.Frame(self.body, bg=PAPER)
        band_wrap.pack(fill="x", padx=26, pady=(20, 6))
        caption(band_wrap, "the split", bg=PAPER).pack(anchor="w", pady=(0, 8))
        self.band = tk.Canvas(band_wrap, height=10, bg=PAPER, highlightthickness=0)
        self.band.pack(fill="x")
        legend = tk.Frame(band_wrap, bg=PAPER)
        legend.pack(fill="x", pady=(9, 0))
        for name, key, col in (("Productive", "productive_min", DEEP),
                               ("Neutral", "neutral_min", COOL),
                               ("Distracting", "distracting_min", CLAY)):
            cell = tk.Frame(legend, bg=PAPER)
            cell.pack(side="left", padx=(0, 22))
            sw = tk.Canvas(cell, width=8, height=8, bg=PAPER, highlightthickness=0)
            sw.create_rectangle(0, 0, 8, 8, fill=col, width=0)
            sw.pack(side="left", pady=(0, 1))
            tk.Label(cell, text=f"  {name} {fmt_minutes(s[key])}", bg=PAPER,
                     fg=INK_2, font=font(9)).pack(side="left")
        self.after(60, lambda: self._draw_band(s))

        tl = tk.Frame(self.body, bg=PAPER)
        tl.pack(fill="x", padx=26, pady=(20, 6))
        caption(tl, "the day", bg=PAPER).pack(anchor="w", pady=(0, 8))
        self.timeline = tk.Canvas(tl, height=42, bg=PAPER, highlightthickness=0)
        self.timeline.pack(fill="x")
        self.after(80, lambda: self.draw_timeline(segments, day_start))

        apps_wrap = tk.Frame(self.body, bg=PAPER)
        apps_wrap.pack(fill="both", expand=True, padx=26, pady=(18, 0))
        caption(apps_wrap, "applications", bg=PAPER).pack(anchor="w", pady=(0, 6))
        holder = Scroller(apps_wrap)
        holder.pack(fill="both", expand=True)

        total = max(1.0, sum(v for _, v in s["top_apps"]) or 1.0)
        if not s["top_apps"]:
            tk.Label(holder.inner,
                     text="Nothing tracked yet. Leave it running a few minutes.",
                     bg=PAPER, fg=INK_3, font=font(10)).pack(anchor="w", pady=8)
        for name, secs in s["top_apps"]:
            row = tk.Frame(holder.inner, bg=PAPER)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name[:30], bg=PAPER, fg=INK, font=font(10),
                     width=22, anchor="w").pack(side="left")
            tk.Label(row, text=fmt_minutes(secs / 60), bg=PAPER, fg=INK_2,
                     font=font(9), width=8, anchor="e").pack(side="right")
            cv = tk.Canvas(row, height=6, bg=PAPER, highlightthickness=0)
            cv.pack(side="left", fill="x", expand=True, padx=12, pady=7)
            cv.bind("<Configure>", lambda e, c=cv, f=secs / total: (
                c.delete("all"), bar(c, 0, 0, e.width, 6, f, INK_4, SUNK)))

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26, pady=(14, 0))
        foot = tk.Frame(self.body, bg=PAPER)
        foot.pack(fill="x", padx=26, pady=14)
        tk.Label(foot, text=f"{s['water']} glasses of water", bg=PAPER, fg=INK_3,
                 font=font(9)).pack(side="left")
        if on_digest:
            Button(foot, "Daily log", command=lambda: (self.destroy(), on_digest()),
                   primary=True, bg=PAPER).pack(side="right", padx=(10, 0))
        if self.on_water:
            Button(foot, "Log a glass",
                   command=lambda: (self.on_water(), self.destroy()),
                   bg=PAPER).pack(side="right")

    def _draw_band(self, s):
        self.band.delete("all")
        w = self.band.winfo_width() or 640
        total = max(1.0, s["productive_min"] + s["neutral_min"] + s["distracting_min"])
        x = 0.0
        for key, col in (("productive_min", DEEP), ("neutral_min", COOL),
                         ("distracting_min", CLAY)):
            seg = w * (s[key] / total)
            if seg > 0.4:
                self.band.create_rectangle(x, 0, x + seg, 10, fill=col, width=0)
            x += seg

    def draw_timeline(self, segments, day_start):
        cv = self.timeline
        cv.delete("all")
        w = cv.winfo_width() or 640
        cv.create_rectangle(0, 6, w, 24, fill=SUNK, width=0)
        for r in segments:
            a = (r["start_ts"] - day_start) / 86400.0
            b = (r["end_ts"] - day_start) / 86400.0
            if b <= a:
                continue
            col = IDLE if r["idle"] else CATEGORY.get(r["category"] or "", COOL)
            cv.create_rectangle(a * w, 6, max(a * w + 1, b * w), 24, fill=col, width=0)
        for h in range(0, 25, 3):
            x = min(w - 1, w * h / 24.0)
            cv.create_line(x, 24, x, 28, fill=INK_4)
            if h % 6 == 0:
                cv.create_text(min(w - 12, max(12, x)), 31, text=f"{h:02d}",
                               fill=INK_3, font=font(7), anchor="n")


# ---------------------------------------------------------------------------
# Daily log
# ---------------------------------------------------------------------------

class DigestPanel(Window):
    """The paragraph you'd otherwise write by hand every morning."""

    def __init__(self, master, digest: dict, standup: str, detail: str,
                 insights: list):
        super().__init__(master, "Daily log", digest["date"], w=740, h=660)
        self.standup, self.detail = standup, detail
        self.mode = "standup"

        if insights:
            ins = tk.Frame(self.body, bg=PAPER)
            ins.pack(fill="x", padx=26, pady=(20, 4))
            caption(ins, "what stood out", bg=PAPER).pack(anchor="w", pady=(0, 8))
            for line in insights:
                row = tk.Frame(ins, bg=PAPER)
                row.pack(fill="x", pady=2)
                tk.Frame(row, bg=DEEP, width=2, height=15).pack(side="left",
                                                                padx=(0, 10))
                tk.Label(row, text=line, bg=PAPER, fg=INK, font=font(10),
                         wraplength=620, justify="left").pack(side="left", anchor="w")

        tabs = tk.Frame(self.body, bg=PAPER)
        tabs.pack(fill="x", padx=26, pady=(18, 0))
        self.tab_labels = {}
        for key, text in (("standup", "Standup"), ("detail", "Full record")):
            l = tk.Label(tabs, text=text, bg=PAPER, fg=INK_3, font=font(10),
                         pady=6, cursor="hand2")
            l.pack(side="left", padx=(0, 20))
            l.bind("<Button-1>", lambda e, k=key: self._switch(k))
            self.tab_labels[key] = l
        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26)

        body_wrap = tk.Frame(self.body, bg=LINE)
        body_wrap.pack(fill="both", expand=True, padx=26, pady=(14, 0))
        self.text = tk.Text(body_wrap, bg=SURFACE, fg=INK, relief="flat",
                            font=("Consolas", 10), wrap="word", padx=18, pady=16,
                            insertbackground=DEEP, highlightthickness=0)
        self.text.pack(fill="both", expand=True, padx=1, pady=1)

        foot = tk.Frame(self.body, bg=PAPER)
        foot.pack(fill="x", padx=26, pady=14)
        self.copied = tk.Label(foot, text="", bg=PAPER, fg=DEEP, font=font(9))
        self.copied.pack(side="left")
        Button(foot, "Copy", command=self._copy, primary=True,
               bg=PAPER).pack(side="right")
        self._switch("standup")

    def _switch(self, key):
        self.mode = key
        for k, l in self.tab_labels.items():
            l.config(fg=INK if k == key else INK_3,
                     font=font(10, "bold" if k == key else "normal"))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", self.standup if key == "standup" else self.detail)

    def _copy(self):
        payload = self.standup if self.mode == "standup" else self.detail
        try:
            self.clipboard_clear()
            self.clipboard_append(payload)
            self.copied.config(text="Copied to clipboard")
            self.after(2600, lambda: self.copied.config(text=""))
        except tk.TclError:
            self.copied.config(text="Could not reach the clipboard")


# ---------------------------------------------------------------------------
# Projects (git)
# ---------------------------------------------------------------------------

class ProjectsPanel(Window):
    def __init__(self, master, report: list, note: str = ""):
        super().__init__(master, "Projects",
                         note or "time by repository and branch", w=700, h=600)
        holder = Scroller(self.body)
        holder.pack(fill="both", expand=True, padx=26, pady=(20, 18))

        if not report:
            tk.Label(holder.inner,
                     text="No repository activity tracked yet.\n\n"
                          "Open a project in your editor and the time gets\n"
                          "attributed to that repo and branch automatically.",
                     bg=PAPER, fg=INK_3, font=font(10),
                     justify="left").pack(anchor="w")
            return

        for r in report:
            head = tk.Frame(holder.inner, bg=PAPER)
            head.pack(fill="x", pady=(12, 2))
            tk.Label(head, text=r["name"], bg=PAPER, fg=INK,
                     font=font(12, "bold")).pack(side="left")
            tk.Label(head, text=fmt_minutes(r["secs"] / 60), bg=PAPER, fg=INK,
                     font=font(11)).pack(side="right")
            tk.Frame(holder.inner, bg=LINE, height=1).pack(fill="x", pady=(2, 6))

            for br, secs in sorted(r["branches"].items(), key=lambda kv: -kv[1])[:6]:
                row = tk.Frame(holder.inner, bg=PAPER)
                row.pack(fill="x", pady=1)
                tk.Label(row, text=br[:38], bg=PAPER, fg=INK_2, font=font(9),
                         width=30, anchor="w").pack(side="left")
                tk.Label(row, text=fmt_minutes(secs / 60), bg=PAPER, fg=INK_3,
                         font=font(9)).pack(side="right")

            if r["commits"]:
                tk.Label(holder.inner, text=f"{len(r['commits'])} COMMITS",
                         bg=PAPER, fg=INK_3,
                         font=font(8, "bold")).pack(anchor="w", pady=(8, 2))
                for c in r["commits"][:8]:
                    row = tk.Frame(holder.inner, bg=PAPER)
                    row.pack(fill="x", pady=1)
                    tk.Label(row, text=c["sha"], bg=PAPER, fg=INK_4,
                             font=("Consolas", 8)).pack(side="left", padx=(0, 10))
                    tk.Label(row, text=c["subject"][:64], bg=PAPER, fg=INK_2,
                             font=font(9), anchor="w").pack(side="left")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TodoPanel(Window):
    def __init__(self, master, todos, on_change=None):
        super().__init__(master, "Tasks", "one at a time", w=560, h=560)
        self.todos = todos
        self.on_change = on_change

        wrap = tk.Frame(self.body, bg=PAPER)
        wrap.pack(fill="x", padx=26, pady=(20, 12))
        box = tk.Frame(wrap, bg=LINE)
        box.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(box, bg=SURFACE, fg=INK, insertbackground=DEEP,
                              relief="flat", font=font(11))
        self.entry.pack(fill="x", padx=1, pady=1, ipady=8)
        self.entry.bind("<Return>", self._add)
        Button(wrap, "Add", command=self._add, primary=True,
               bg=PAPER).pack(side="right", padx=(10, 0))

        self.progress = tk.Label(self.body, text="", bg=PAPER, fg=INK_3,
                                 font=font(9))
        self.progress.pack(anchor="w", padx=26)

        self.holder = Scroller(self.body)
        self.holder.pack(fill="both", expand=True, padx=26, pady=(10, 0))

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26)
        tk.Label(self.body,
                 text="Click a task to close it. Start marks what you're on now, "
                      "so time is credited to it.",
                 bg=PAPER, fg=INK_3, font=font(8), justify="left",
                 wraplength=490).pack(anchor="w", padx=26, pady=12)
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
        for w in self.holder.inner.winfo_children():
            w.destroy()
        rows = self.todos.list()
        done, total = self.todos.progress()
        self.progress.config(text=f"{done} of {total} closed" if total
                             else "nothing planned yet")
        if not rows:
            tk.Label(self.holder.inner, text="What are you getting done today?",
                     bg=PAPER, fg=INK_3, font=font(10)).pack(anchor="w", pady=12)
        for r in rows:
            self._row(r)
        if self.on_change:
            self.on_change()

    def _row(self, r):
        active, done = bool(r["active"]), bool(r["done"])
        bg = SUNK if active else PAPER
        row = tk.Frame(self.holder.inner, bg=bg)
        row.pack(fill="x", pady=1)

        mark = tk.Canvas(row, width=17, height=17, bg=bg, highlightthickness=0,
                         cursor="hand2")
        mark.pack(side="left", padx=(8, 10), pady=10)
        mark.create_rectangle(1, 1, 15, 15, outline=(DEEP if done else INK_4),
                              width=1)
        if done:
            check_mark(mark, 0, 0, 16, DEEP)
        mark.bind("<Button-1>", lambda e, i=r["id"], d=done: (
            self.todos.set_done(i, not d), self.refresh()))

        txt = tk.Label(row, text=r["text"][:44], bg=bg,
                       fg=(INK_4 if done else INK), font=font(10), anchor="w",
                       cursor="hand2")
        txt.pack(side="left", fill="x", expand=True, pady=10)
        txt.bind("<Button-1>", lambda e, i=r["id"], d=done: (
            self.todos.set_done(i, not d), self.refresh()))

        x = tk.Label(row, text="×", bg=bg, fg=INK_4, font=font(11), cursor="hand2")
        x.pack(side="right", padx=10)
        x.bind("<Button-1>", lambda e, i=r["id"]: (self.todos.delete(i),
                                                   self.refresh()))
        if r["spent_s"]:
            tk.Label(row, text=fmt_minutes(r["spent_s"] / 60), bg=bg, fg=INK_3,
                     font=font(8)).pack(side="right", padx=6)
        if not done:
            b = tk.Label(row, text=("Stop" if active else "Start"), bg=bg,
                         fg=(DEEP if active else INK_3),
                         font=font(9, "bold" if active else "normal"),
                         cursor="hand2", padx=8)
            b.pack(side="right")
            b.bind("<Button-1>", lambda e, i=r["id"], a=active: (
                self.todos.set_active(None if a else i), self.refresh()))
        tk.Frame(self.holder.inner, bg=LINE, height=1).pack(fill="x")


# ---------------------------------------------------------------------------
# Settings + what the classifier has learned
# ---------------------------------------------------------------------------

class SettingsPanel(Window):
    FIELDS = [
        ("water_every_min", "Water", "minutes between reminders"),
        ("break_every_min", "Stretch break", "minutes"),
        ("eye_every_min", "Eye break", "minutes"),
        ("eye_duration_s", "Eye break length", "seconds"),
        ("idle_threshold_s", "Idle after", "seconds without input"),
        ("water_goal", "Water goal", "glasses per day"),
    ]

    def __init__(self, master, settings, on_save=None, classifier=None):
        super().__init__(master, "Settings", "zero turns a reminder off",
                         w=580, h=620)
        self.settings = settings
        self.on_save = on_save
        self.classifier = classifier
        self.vars: dict = {}

        body = Scroller(self.body)
        body.pack(fill="both", expand=True, padx=26, pady=(18, 0))
        caption(body.inner, "reminders", bg=PAPER).pack(anchor="w", pady=(0, 8))
        for key, label, hint in self.FIELDS:
            self._field(body.inner, key, label, hint)

        caption(body.inner, "breaks", bg=PAPER).pack(anchor="w", pady=(18, 8))
        self.block_var = tk.BooleanVar(value=bool(settings.get("blocking_breaks")))
        self.water_block_var = tk.BooleanVar(value=bool(settings.get("water_blocking")))
        self._check(body.inner, "Breaks hold the screen", self.block_var)
        self._check(body.inner, "Water reminder holds the screen",
                    self.water_block_var)
        tk.Label(body.inner, text="Escape always skips a break, immediately.",
                 bg=PAPER, fg=INK_3, font=font(8)).pack(anchor="w", pady=(6, 0))

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26, pady=(12, 0))
        foot = tk.Frame(self.body, bg=PAPER)
        foot.pack(fill="x", padx=26, pady=14)
        Button(foot, "Save", command=self._save, primary=True,
               bg=PAPER).pack(side="right")
        if classifier is not None:
            Button(foot, "What it has learned",
                   command=lambda: LearnedPanel(self.master, classifier),
                   bg=PAPER).pack(side="left")

    def _field(self, parent, key, label, hint):
        row = tk.Frame(parent, bg=PAPER)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=PAPER, fg=INK, font=font(10),
                 width=18, anchor="w").pack(side="left")
        box = tk.Frame(row, bg=LINE)
        box.pack(side="left")
        v = tk.StringVar(value=str(self.settings.get(key)))
        self.vars[key] = v
        tk.Entry(box, textvariable=v, bg=SURFACE, fg=INK, insertbackground=DEEP,
                 relief="flat", width=6, justify="center",
                 font=font(10)).pack(padx=1, pady=1, ipady=5)
        tk.Label(row, text=hint, bg=PAPER, fg=INK_3,
                 font=font(8)).pack(side="left", padx=10)

    def _check(self, parent, label, var):
        tk.Checkbutton(parent, text=label, variable=var, bg=PAPER, fg=INK,
                       selectcolor=SURFACE, activebackground=PAPER,
                       activeforeground=INK, font=font(10), anchor="w",
                       highlightthickness=0, bd=0).pack(anchor="w", pady=2)

    def _save(self):
        for key, _l, _h in self.FIELDS:
            try:
                self.settings.set(key, max(0, int(float(self.vars[key].get()))))
            except ValueError:
                continue
        self.settings.set("blocking_breaks", bool(self.block_var.get()))
        self.settings.set("water_blocking", bool(self.water_block_var.get()))
        self.destroy()
        if self.on_save:
            self.on_save()


class LearnedPanel(Window):
    """What the classifier worked out — and a way to undo any of it."""

    def __init__(self, master, classifier):
        st = classifier.stats()
        super().__init__(master, "What it has learned",
                         f"{st['terms']} terms, {st['explicit']} you set yourself",
                         w=600, h=560)
        self.classifier = classifier
        self.holder = Scroller(self.body)
        self.holder.pack(fill="both", expand=True, padx=26, pady=(18, 18))
        self.refresh()

    def refresh(self):
        for w in self.holder.inner.winfo_children():
            w.destroy()
        rows = self.classifier.learned_terms()
        if not rows:
            tk.Label(self.holder.inner,
                     text="Nothing learned yet. Correct a category from the pet's\n"
                          "menu and it starts building a picture of your work.",
                     bg=PAPER, fg=INK_3, font=font(10),
                     justify="left").pack(anchor="w")
            return
        for token, cat, weight, explicit in rows:
            row = tk.Frame(self.holder.inner, bg=PAPER)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=token[:30], bg=PAPER, fg=INK, font=font(10),
                     width=24, anchor="w").pack(side="left")
            tk.Label(row, text=cat, bg=PAPER, fg=CATEGORY.get(cat, INK_2),
                     font=font(9, "bold"), width=12, anchor="w").pack(side="left")
            tk.Label(row, text=("you set this" if explicit else "inferred"),
                     bg=PAPER, fg=INK_3, font=font(8), width=12,
                     anchor="w").pack(side="left")
            x = tk.Label(row, text="×", bg=PAPER, fg=INK_4, font=font(10),
                         cursor="hand2")
            x.pack(side="right", padx=8)
            x.bind("<Button-1>", lambda e, t=token: (self.classifier.forget(t),
                                                     self.refresh()))
            tk.Frame(self.holder.inner, bg=LINE, height=1).pack(fill="x")


class RetagDialog(Window):
    """Correct a category, and choose how widely the correction applies."""

    def __init__(self, master, exe: str, title: str, verdict: dict, on_pick):
        super().__init__(master, "Re-categorise", "", w=580, h=420)
        self.on_pick = on_pick

        tk.Label(self.body, text=(title or exe or "unknown")[:80], bg=PAPER,
                 fg=INK, font=font(12, "bold"), wraplength=510,
                 justify="left").pack(anchor="w", padx=26, pady=(20, 2))
        cur = verdict.get("category", "neutral")
        conf = int(round(verdict.get("confidence", 0) * 100))
        src = {"you": "because you set it",
               "learned": "learned from your habits",
               "prior": "starting guess",
               "unknown": "no signal yet"}.get(verdict.get("source", ""), "")
        tk.Label(self.body, text=f"currently {cur}  ·  {conf}% confident  ·  {src}",
                 bg=PAPER, fg=INK_3, font=font(9)).pack(anchor="w", padx=26)
        if verdict.get("evidence"):
            tk.Label(self.body, text="based on: " + ", ".join(verdict["evidence"]),
                     bg=PAPER, fg=INK_3, font=font(8)).pack(anchor="w", padx=26,
                                                            pady=(4, 0))

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26, pady=16)
        caption(self.body, "remember this for", bg=PAPER).pack(anchor="w", padx=26)

        words = [w for w in (title or "").lower().replace("-", " ")
                 .replace("|", " ").split() if len(w) > 3][:5]
        self.scope = tk.StringVar(value=(words[0] if words else (exe or "").lower()))
        opts = tk.Frame(self.body, bg=PAPER)
        opts.pack(fill="x", padx=26, pady=(8, 0))
        for w in words:
            tk.Radiobutton(opts, text=f'titles containing "{w}"', value=w,
                           variable=self.scope, bg=PAPER, fg=INK,
                           selectcolor=SURFACE, activebackground=PAPER,
                           font=font(9), highlightthickness=0, bd=0,
                           anchor="w").pack(anchor="w")
        if exe:
            tk.Radiobutton(opts, text=f"everything in {exe}",
                           value="exe:" + exe.lower().replace(".exe", ""),
                           variable=self.scope, bg=PAPER, fg=INK,
                           selectcolor=SURFACE, activebackground=PAPER,
                           font=font(9), highlightthickness=0, bd=0,
                           anchor="w").pack(anchor="w")

        tk.Frame(self.body, bg=LINE, height=1).pack(fill="x", padx=26, pady=16)
        row = tk.Frame(self.body, bg=PAPER)
        row.pack(fill="x", padx=26)
        for cat in ("productive", "neutral", "distracting"):
            b = tk.Label(row, text=cat, bg=SURFACE, fg=CATEGORY[cat],
                         font=font(10, "bold"), padx=18, pady=10, cursor="hand2",
                         highlightbackground=LINE, highlightthickness=1)
            b.pack(side="left", padx=(0, 10))
            b.bind("<Button-1>", lambda e, c=cat: (
                self.destroy(), self.on_pick(self.scope.get(), c)))
