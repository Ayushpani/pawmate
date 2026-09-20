"""Global hotkey via RegisterHotKey — works even when the pet isn't focused.

Default is Ctrl+` (backtick). That combo is deliberately chosen: Windows
itself doesn't claim it (unlike Ctrl+Space, which IMEs take, or
Win+anything), and it's two keys reachable with one hand.
"""
from __future__ import annotations

import ctypes
import queue
import sys
import threading

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

# name -> virtual-key code
VK = {
    "grave": 0xC0, "`": 0xC0, "space": 0x20, "tab": 0x09,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "semicolon": 0xBA, ";": 0xBA, "comma": 0xBC, ",": 0xBC,
    "period": 0xBE, ".": 0xBE, "slash": 0xBF, "/": 0xBF,
    "backslash": 0xDC, "\\": 0xDC, "quote": 0xDE, "'": 0xDE,
    "lbracket": 0xDB, "[": 0xDB, "rbracket": 0xDD, "]": 0xDD,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)

MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT,
        "shift": MOD_SHIFT, "win": MOD_WIN}


def parse(spec: str) -> tuple[int, int] | None:
    """'ctrl+grave' -> (modifiers, vk). None if it can't be parsed."""
    mods, vk = 0, None
    for part in (spec or "").lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in MODS:
            mods |= MODS[part]
        else:
            vk = VK.get(part)
    if not vk or not mods:
        return None
    return mods, vk


def describe(spec: str) -> str:
    pretty = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
              "grave": "`", "space": "Space"}
    return " + ".join(pretty.get(p, p.upper() if len(p) == 1 else p.title())
                      for p in (spec or "").lower().split("+") if p)


class HotkeyListener:
    """Registers the hotkey on its own thread and pushes hits onto a queue.

    RegisterHotKey delivers WM_HOTKEY to the *thread* that registered it, so
    this owns a dedicated thread with its own message loop. Tk then drains
    the queue on its own loop — Tk calls must not happen off the UI thread.
    """

    def __init__(self, spec: str = "ctrl+grave"):
        self.spec = spec
        self.events: queue.Queue = queue.Queue()
        self.ok = False
        self.error = ""
        self._thread = None
        self._tid = None

    def start(self) -> bool:
        if sys.platform != "win32":
            self.error = "global hotkeys are Windows-only"
            return False
        parsed = parse(self.spec)
        if not parsed:
            self.error = f"could not parse hotkey {self.spec!r}"
            return False
        self._thread = threading.Thread(target=self._loop, args=parsed, daemon=True)
        self._thread.start()
        # give the thread a moment to report registration success/failure
        import time
        for _ in range(40):
            if self.ok or self.error:
                break
            time.sleep(0.01)
        return self.ok

    def _loop(self, mods: int, vk: int):
        user32 = ctypes.windll.user32
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            err = ctypes.get_last_error()
            self.error = (f"{describe(self.spec)} is already taken by another app"
                          if err == 1409 else f"RegisterHotKey failed (error {err})")
            return
        self.ok = True
        try:
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.events.put(True)
        finally:
            user32.UnregisterHotKey(None, 1)

    def poll(self) -> bool:
        """True if the hotkey fired since the last poll. Safe on the UI thread."""
        hit = False
        while True:
            try:
                self.events.get_nowait()
                hit = True
            except queue.Empty:
                return hit

    def stop(self):
        if self._tid:
            try:                    # nudge GetMessage so the thread can exit
                ctypes.windll.user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)
            except Exception:  # noqa: BLE001
                pass


import ctypes.wintypes  # noqa: E402  (needed for MSG above)
