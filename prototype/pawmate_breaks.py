"""Blocking break overlay — holds the screen for the duration of a break.

Deliberately interruptible: a break that can't be escaped is a break that
gets the whole app uninstalled the first time it fires mid-demo. Esc always
aborts immediately, and the escape hatch is stated on screen rather than
hidden.
"""
from __future__ import annotations

import time
import tkinter as tk

BG = "#0d1014"
FG = "#e8eaee"
MUTED = "#8b94a3"
ACCENT = "#5eccaa"
WATER = "#6fb5e8"


class BreakOverlay(tk.Toplevel):
    """Fullscreen hold. Returns control via Esc (skip) or the confirm key.

    kind:
      'eye'   – 20-20-20 look-away, runs its countdown then releases itself
      'break' – longer stretch break
      'water' – waits for the confirm key, logs a glass on success
    """

    def __init__(self, master, kind: str, seconds: int, on_done=None, on_skip=None,
                 confirm_key: str = "w", message: str | None = None):
        super().__init__(master)
        self.kind = kind
        self.total = max(1, int(seconds))
        self.left = self.total
        self.on_done = on_done
        self.on_skip = on_skip
        self.confirm_key = (confirm_key or "w").lower()
        self._finished = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-fullscreen", True)
        except tk.TclError:
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        try:
            self.attributes("-alpha", 0.94)
        except tk.TclError:
            pass
        self.configure(bg=BG, cursor="none")

        accent = WATER if kind == "water" else ACCENT
        head = {"eye": "Look away", "break": "Time to stretch",
                "water": "Water break"}.get(kind, "Break")
        sub = message or {
            "eye": "Focus on something ~20 feet away.",
            "break": "Stand up, roll your shoulders, walk it off.",
            "water": "Go drink a glass of water.",
        }.get(kind, "")

        wrap = tk.Frame(self, bg=BG)
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text=head, bg=BG, fg=FG,
                 font=("Segoe UI", 44, "bold")).pack()
        tk.Label(wrap, text=sub, bg=BG, fg=MUTED,
                 font=("Segoe UI", 15)).pack(pady=(10, 26))

        self.ring = tk.Canvas(wrap, width=210, height=210, bg=BG, highlightthickness=0)
        self.ring.pack()
        self.accent = accent

        if kind == "water":
            hint = f"press  {self.confirm_key.upper()}  when you've had a glass"
        else:
            hint = "releases automatically"
        tk.Label(wrap, text=hint, bg=BG, fg=accent,
                 font=("Segoe UI", 13)).pack(pady=(26, 6))
        tk.Label(wrap, text="Esc  ·  skip if you're mid-something important",
                 bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack()

        # Hold the keyboard so typing doesn't leak into whatever is behind.
        # Escape is bound with bind_all as well as on the toplevel: if focus
        # ends up on a child widget, a toplevel-only binding would miss it,
        # and an unescapable screen-blocking window is the worst possible
        # failure mode for this feature.
        for seq, fn in (("<Escape>", lambda e: self._end(skipped=True)),
                        (f"<KeyPress-{self.confirm_key}>", lambda e: self._confirm()),
                        (f"<KeyPress-{self.confirm_key.upper()}>", lambda e: self._confirm())):
            self.bind(seq, fn)
            self.bind_all(seq, fn)
        self.bind("<Button-1>", lambda e: "break")

        # Retry focus a few times — the grab can lose a race with whatever
        # window was being opened as the break fired.
        for delay in (30, 200, 600, 1500):
            self.after(delay, self._grab)

        # Hard failsafe: no matter what goes wrong with timers, grabs or
        # callbacks, this window releases itself. A stuck overlay would mean
        # a locked desktop.
        self.after(int((self.total + 45) * 1000), lambda: self._end(skipped=True))
        self._tick()

    def _grab(self):
        try:
            self.focus_force()
            self.grab_set_global()
        except Exception:  # noqa: BLE001 - a failed grab must not wedge the overlay
            try:
                self.grab_set()
            except Exception:  # noqa: BLE001
                pass

    def _draw(self):
        self.ring.delete("all")
        frac = self.left / float(self.total)
        self.ring.create_oval(14, 14, 196, 196, outline="#232a33", width=10)
        if frac > 0:
            self.ring.create_arc(14, 14, 196, 196, start=90, extent=-359.9 * frac,
                                 outline=self.accent, width=10, style="arc")
        label = str(self.left) if self.kind != "water" else self.confirm_key.upper()
        size = 52 if self.kind != "water" else 60
        self.ring.create_text(105, 100, text=label, fill=FG,
                              font=("Segoe UI", size, "bold"))
        if self.kind != "water":
            self.ring.create_text(105, 143, text="seconds", fill=MUTED,
                                  font=("Segoe UI", 10))

    def _tick(self):
        if self._finished:
            return
        self._draw()
        if self.left <= 0:
            # A water break waits for the keypress; timed breaks release.
            self._end(skipped=False) if self.kind != "water" else self._water_timeout()
            return
        self.left -= 1
        self.after(1000, self._tick)

    def _water_timeout(self):
        self._end(skipped=True)

    def _confirm(self):
        if self.kind != "water":
            return
        self._end(skipped=False)

    def _end(self, skipped: bool):
        if self._finished:
            return
        self._finished = True
        for seq in ("<Escape>", f"<KeyPress-{self.confirm_key}>",
                    f"<KeyPress-{self.confirm_key.upper()}>"):
            try:
                self.unbind_all(seq)      # bind_all is global; don't leak it
            except Exception:  # noqa: BLE001
                pass
        try:
            self.grab_release()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()
        cb = self.on_skip if skipped else self.on_done
        if cb:
            try:
                cb()
            except Exception as exc:  # noqa: BLE001
                print(f"[break] callback failed: {exc}")


class BreakScheduler:
    """Decides when eye/water/stretch breaks are due, from live settings.

    Counts *active* time only — an idle gap means you already stepped away,
    so the clock rewinds rather than firing the moment you sit back down.
    """

    def __init__(self, settings, idle_fn, on_due):
        self.settings = settings
        self.idle_fn = idle_fn
        self.on_due = on_due
        now = time.time()
        self.last = {"eye": now, "water": now, "break": now}
        self.suspended_until = 0.0

    def suspend(self, seconds: float):
        self.suspended_until = time.time() + seconds

    def reset(self, kind: str):
        self.last[kind] = time.time()

    def due(self) -> str | None:
        now = time.time()
        if now < self.suspended_until:
            return None
        idle = self.idle_fn()
        if idle > 180:
            # away from the desk: that IS the break
            self.last["break"] = self.last["eye"] = now
            return None
        for kind, key in (("eye", "eye_every_min"),
                          ("water", "water_every_min"),
                          ("break", "break_every_min")):
            every = float(self.settings.get(key) or 0)
            if every <= 0:
                continue
            if now - self.last[kind] >= every * 60:
                return kind
        return None

    def fire(self, kind: str):
        self.last[kind] = time.time()
        self.on_due(kind)
