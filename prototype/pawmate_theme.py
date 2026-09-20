"""Visual language: warm paper, ink, hairlines.

Deliberately not a dark/neon/glow dashboard. The reference points are print
and editorial layout — a warm off-white ground, near-black ink, hairline
rules, generous whitespace, and hierarchy carried by type size and weight
rather than by colour or boxes. Colour is used sparingly and only where it
carries meaning (a category, a state), never for decoration.

No emoji and no icon artwork anywhere: every mark is either a typographic
glyph or drawn from canvas primitives, so nothing depends on an emoji font
being present or a glyph falling back to a blank box.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

# -- ground / surfaces -------------------------------------------------------
PAPER = "#f7f6f2"          # app background, warm off-white
SURFACE = "#fffefb"        # cards
SUNK = "#efece5"           # wells, track backgrounds
LINE = "#e3dfd6"           # hairline rules
LINE_STRONG = "#d3cec2"

# -- ink ---------------------------------------------------------------------
INK = "#22201c"            # primary text
INK_2 = "#55514a"          # secondary
INK_3 = "#8d887e"          # tertiary / captions
INK_4 = "#b3ada1"          # faintest

# -- meaning -----------------------------------------------------------------
DEEP = "#3d6b5c"           # productive / primary action (muted forest)
COOL = "#4a6480"           # neutral (slate)
CLAY = "#9c5548"           # distracting / destructive (muted clay)
OCHRE = "#94762f"          # caution
IDLE = "#c6c0b4"           # idle time

CATEGORY = {
    "productive": DEEP,
    "neutral": COOL,
    "distracting": CLAY,
    "": IDLE,
}

FAMILY = "Segoe UI"
SERIF = "Georgia"          # used only for display numerals


def font(size=10, weight="normal", family=None, slant="roman"):
    return tkfont.Font(family=family or FAMILY, size=size,
                       weight=weight, slant=slant)


def rule(parent, pad=(0, 0)):
    """A hairline divider — the main structural device in this design."""
    f = tk.Frame(parent, bg=LINE, height=1)
    f.pack(fill="x", padx=pad[0], pady=pad[1])
    return f


def card(parent, **kw):
    """A surface with a hairline border and no shadow."""
    outer = tk.Frame(parent, bg=LINE, **kw)
    inner = tk.Frame(outer, bg=SURFACE)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    outer.inner = inner
    return outer


def label(parent, text, size=10, weight="normal", fg=INK, bg=SURFACE, **kw):
    return tk.Label(parent, text=text, bg=bg, fg=fg,
                    font=font(size, weight), **kw)


def caption(parent, text, bg=SURFACE, **kw):
    """Small-caps-ish section caption: the label style used throughout."""
    return tk.Label(parent, text=text.upper(), bg=bg, fg=INK_3,
                    font=font(8, "bold"), **kw)


class Button(tk.Frame):
    """Flat text button. Bordered for primary, bare for secondary."""

    def __init__(self, parent, text, command=None, primary=False, danger=False,
                 bg=SURFACE, pad=(16, 8)):
        super().__init__(parent, bg=(LINE if primary else bg))
        self.command = command
        fg = SURFACE if primary else (CLAY if danger else INK_2)
        fill = (DEEP if primary else bg)
        self.inner = tk.Label(self, text=text, bg=fill, fg=fg,
                              font=font(9, "bold" if primary else "normal"),
                              padx=pad[0], pady=pad[1], cursor="hand2")
        self.inner.pack(padx=(0 if primary else 0), pady=0)
        for w in (self, self.inner):
            w.bind("<Button-1>", self._go)
        self.inner.bind("<Enter>", lambda e: self.inner.config(
            bg=("#35604f" if primary else SUNK)))
        self.inner.bind("<Leave>", lambda e: self.inner.config(bg=fill))

    def _go(self, _e=None):
        if self.command:
            self.command()


class Window(tk.Toplevel):
    """Borderless paper panel with a typographic header and drag strip."""

    def __init__(self, master, title: str, subtitle: str = "", w=680, h=560):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=LINE)

        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(40, (sh - h) // 3)}")

        shell = tk.Frame(self, bg=PAPER)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        head = tk.Frame(shell, bg=PAPER)
        head.pack(fill="x", padx=26, pady=(20, 0))
        left = tk.Frame(head, bg=PAPER)
        left.pack(side="left")
        tk.Label(left, text=title, bg=PAPER, fg=INK,
                 font=font(17, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(left, text=subtitle, bg=PAPER, fg=INK_3,
                     font=font(9)).pack(anchor="w", pady=(2, 0))

        close = tk.Label(head, text="×", bg=PAPER, fg=INK_3,
                         font=font(15), cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.destroy())
        close.bind("<Enter>", lambda e: close.config(fg=INK))
        close.bind("<Leave>", lambda e: close.config(fg=INK_3))

        tk.Frame(shell, bg=LINE, height=1).pack(fill="x", padx=26, pady=(16, 0))

        self.body = tk.Frame(shell, bg=PAPER)
        self.body.pack(fill="both", expand=True)

        for w_ in (head, left, shell):
            w_.bind("<ButtonPress-1>", self._drag_start)
            w_.bind("<B1-Motion>", self._drag)
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(60, lambda: (self.lift(), self.focus_force()))

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()

    def _drag(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")


class Scroller(tk.Frame):
    """Scrollable column with no visible scrollbar chrome."""

    def __init__(self, parent, bg=PAPER):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _wheel(self, e):
        try:
            if self.canvas.winfo_exists():
                self.canvas.yview_scroll(int(-e.delta / 120), "units")
        except tk.TclError:
            pass


def bar(canvas: tk.Canvas, x, y, w, h, frac, colour, track=SUNK):
    """A thin measured bar — the only chart primitive used."""
    canvas.create_rectangle(x, y, x + w, y + h, fill=track, width=0)
    if frac > 0:
        canvas.create_rectangle(x, y, x + max(2, w * min(1.0, frac)), y + h,
                                fill=colour, width=0)


def check_mark(canvas: tk.Canvas, x, y, size, colour):
    """Drawn check — no glyph, so it can't fall back to a blank box."""
    canvas.create_line(x + size * 0.22, y + size * 0.52,
                       x + size * 0.42, y + size * 0.72,
                       x + size * 0.78, y + size * 0.28,
                       fill=colour, width=2, capstyle="round", joinstyle="round")
