"""
Pawmate Prototype — a live desktop cat you can actually run today.

This is a deliberately small, single-file, dependency-light prototype built to prove
the core "desktop pet" mechanics from docs/master-plan.md *before* investing in the
full Rust/Tauri architecture (see Part 7, Phase 1 "Spikes"). It runs on Windows 10/11
with plain Python.

What it demonstrates (no AI required):
  - A transparent, always-on-top, click-through overlay window (S1/S2 style spike)
  - A procedurally drawn cat (no external art assets / licensing needed) with
    idle / walk / sit / sleep poses and a simple walk-cycle animation
  - Precise movement: wanders the screen, or (best-effort) walks between your real
    desktop icon positions, pausing at each one ("walking over your folders")
  - Drag-to-move
  - Right-click menu: Open App (curated list or browse for any .exe) and
    Close App (pick a running window, confirm before closing — destructive
    action = human click, per the plan's principles)
  - A chat/command bar: typed commands are parsed locally first with zero AI
    ("open notepad", "close chrome", "walk", "sit", "sleep"). If you set
    OPENROUTER_API_KEY (or CF_ACCOUNT_ID + CF_API_TOKEN for Cloudflare
    Workers AI) as environment variables, free-text goes to a free LLM to
    pick an action — but nothing destructive ever runs without your
    confirm click.

What it is NOT: this is not the final Rust/Tauri/Three.js pet. There's no
tracking, no browser extension, no database, no license/auth. It's a fast,
honest proof that the desktop-pet mechanics (transparency, click-through,
precise movement, app open/close with permission, optional free-LLM chat)
work on a real Windows machine.

Run it:  see prototype/README.md
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import random
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from dataclasses import dataclass, field

if sys.platform != "win32":
    print("This prototype targets Windows (Win32 APIs for overlay + desktop icons).")
    print("It will still launch on other platforms with reduced functionality"
          " (no true click-through, no desktop-icon walking).")

try:
    import win32api
    import win32con
    import win32gui
    import win32process
    HAVE_WIN32 = True
except ImportError:
    HAVE_WIN32 = False

from PIL import Image, ImageDraw, ImageTk

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CAT_SIZE = 140                 # window / sprite size in px
TRANSPARENT_KEY = "#ff00ff"    # color-keyed as transparent by the OS
FPS = 30
TICK_MS = int(1000 / FPS)
WALK_SPEED_PX_S = 90.0
IDLE_MIN_S, IDLE_MAX_S = 2.0, 5.0
PAUSE_AT_TARGET_MIN_S, PAUSE_AT_TARGET_MAX_S = 1.5, 4.0
SLEEP_AFTER_IDLE_CYCLES = 4     # after this many idle->walk loops with no user interaction, nap

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct")

CURATED_APPS = {
    "Notepad": "notepad.exe",
    "Calculator": "calc.exe",
    "File Explorer": "explorer.exe",
    "Paint": "mspaint.exe",
    "Browser": "start microsoft-edge:",  # falls back to default browser via shell
}

# ---------------------------------------------------------------------------
# Cat sprite drawing (procedural, no external assets)
#
# Drawn front-on, chibi-proportioned (big head, small body) — this reads as
# a recognizable, cute cat at the small sizes a desktop pet is rendered at,
# which a thin side-profile silhouette does not. Everything is drawn at
# SUPERSAMPLE x the final size and downscaled with LANCZOS for anti-aliased
# edges, since PIL's ImageDraw has no native anti-aliasing.
#
# Every fill is fully opaque (alpha 255) except the transparent color key —
# Tk's -transparentcolor does exact color-key matching, not alpha blending,
# so any translucent pixel here would render wrong (a muddy blend against
# whatever this script's own canvas happened to show) instead of the
# blended-with-fur look you'd expect from a normal alpha compositor.
# ---------------------------------------------------------------------------

SUPERSAMPLE = 5

FUR = (240, 165, 80, 255)
FUR_DARK = (210, 128, 54, 255)
CREAM = (255, 246, 227, 255)
BLACK = (35, 30, 28, 255)
PINK = (255, 176, 188, 255)
PINK_SOFT = (255, 214, 222, 255)
WHITE = (255, 255, 255, 255)
NOSE = (232, 140, 150, 255)
WHISKER = (120, 95, 80, 255)
MAGENTA_OPAQUE = (255, 0, 255, 255)


def _s(v: float) -> float:
    return v * SUPERSAMPLE


def _new_frame() -> Image.Image:
    big = CAT_SIZE * SUPERSAMPLE
    return Image.new("RGBA", (big, big), MAGENTA_OPAQUE)


def _downsample(img: Image.Image) -> Image.Image:
    return img.resize((CAT_SIZE, CAT_SIZE), Image.LANCZOS)


def _ellipse(d: ImageDraw.ImageDraw, cx, cy, rx, ry, **kw):
    d.ellipse([_s(cx - rx), _s(cy - ry), _s(cx + rx), _s(cy + ry)], **kw)


def _line(d: ImageDraw.ImageDraw, pts, fill, width):
    d.line([(_s(x), _s(y)) for x, y in pts], fill=fill, width=int(_s(width)), joint="curve")


def _ear(d: ImageDraw.ImageDraw, base_x, base_y, tip_x, tip_y, width, fill, inner=None):
    dx, dy = tip_x - base_x, tip_y - base_y
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    p1 = (base_x - nx * width / 2, base_y - ny * width / 2)
    p2 = (base_x + nx * width / 2, base_y + ny * width / 2)
    d.polygon([(_s(p1[0]), _s(p1[1])), (_s(p2[0]), _s(p2[1])), (_s(tip_x), _s(tip_y))], fill=fill)
    for p in (p1, p2):  # rounds the ear's base corners
        _ellipse(d, p[0], p[1], width * 0.22, width * 0.22, fill=fill)
    if inner:
        ix, iy = base_x + dx * 0.34, base_y + dy * 0.34
        iw = width * 0.4
        ip1 = (ix - nx * iw / 2, iy - ny * iw / 2)
        ip2 = (ix + nx * iw / 2, iy + ny * iw / 2)
        tip2 = (tip_x - dx * 0.22, tip_y - dy * 0.22)
        d.polygon([(_s(ip1[0]), _s(ip1[1])), (_s(ip2[0]), _s(ip2[1])), (_s(tip2[0]), _s(tip2[1]))], fill=inner)


def _paw(d: ImageDraw.ImageDraw, cx, cy, r):
    _ellipse(d, cx, cy, r, r * 0.75, fill=CREAM, outline=FUR_DARK, width=max(1, int(_s(r) * 0.18)))


def _face(d: ImageDraw.ImageDraw, cx, cy, r, eyes_closed=False, blush=True):
    _ear(d, cx - r * 0.58, cy - r * 0.52, cx - r * 0.92, cy - r * 1.55, r * 0.62, FUR_DARK, PINK)
    _ear(d, cx + r * 0.58, cy - r * 0.52, cx + r * 0.92, cy - r * 1.55, r * 0.62, FUR_DARK, PINK)
    _ellipse(d, cx, cy, r, r * 0.94, fill=FUR)  # head
    _ellipse(d, cx - r * 0.8, cy + r * 0.32, r * 0.4, r * 0.32, fill=FUR)  # cheek fluff
    _ellipse(d, cx + r * 0.8, cy + r * 0.32, r * 0.4, r * 0.32, fill=FUR)
    _ellipse(d, cx, cy + r * 0.4, r * 0.6, r * 0.42, fill=CREAM)  # muzzle
    for off in (-0.3, 0.0, 0.3):  # forehead tabby stripes
        x = cx + off * r
        _line(d, [(x, cy - r * 0.82), (x + off * r * 0.2, cy - r * 0.38)], FUR_DARK, r * 0.1)
    if blush:
        _ellipse(d, cx - r * 0.7, cy + r * 0.16, r * 0.2, r * 0.13, fill=PINK_SOFT)
        _ellipse(d, cx + r * 0.7, cy + r * 0.16, r * 0.2, r * 0.13, fill=PINK_SOFT)
    ex_off, ey = r * 0.34, cy - r * 0.02
    eye_r = r * 0.28
    for sx in (-1, 1):
        ex = cx + sx * ex_off
        if eyes_closed:
            d.arc([_s(ex - eye_r * 0.9), _s(ey - eye_r * 0.35), _s(ex + eye_r * 0.9), _s(ey + eye_r * 0.55)],
                  start=15, end=165, fill=BLACK, width=max(1, int(_s(r * 0.075))))
        else:
            _ellipse(d, ex, ey, eye_r * 0.6, eye_r, fill=BLACK)
            _ellipse(d, ex - eye_r * 0.18, ey - eye_r * 0.4, eye_r * 0.22, eye_r * 0.26, fill=WHITE)
            _ellipse(d, ex + eye_r * 0.2, ey + eye_r * 0.3, eye_r * 0.1, eye_r * 0.12, fill=WHITE)
    nr = r * 0.1
    ny = cy + r * 0.32
    d.polygon([(_s(cx - nr), _s(ny - nr * 0.6)), (_s(cx + nr), _s(ny - nr * 0.6)), (_s(cx), _s(ny + nr * 0.7))],
              fill=NOSE)
    mw = r * 0.2
    my = ny + nr * 0.7
    d.arc([_s(cx - mw), _s(my - mw * 0.5), _s(cx), _s(my + mw * 0.9)], start=15, end=165, fill=BLACK,
          width=int(_s(r * 0.05)))
    d.arc([_s(cx), _s(my - mw * 0.5), _s(cx + mw), _s(my + mw * 0.9)], start=15, end=165, fill=BLACK,
          width=int(_s(r * 0.05)))
    for wy_off in (-0.06, 0.08, 0.22):
        wy = cy + r * (0.4 + wy_off)
        _line(d, [(cx - r * 0.6, wy), (cx - r * 1.15, wy - r * 0.05)], WHISKER, r * 0.03)
        _line(d, [(cx + r * 0.6, wy), (cx + r * 1.15, wy - r * 0.05)], WHISKER, r * 0.03)


def _draw_walk(phase: float) -> Image.Image:
    img = _new_frame()
    d = ImageDraw.Draw(img)
    bob = math.sin(phase * 2 * math.pi) * 2.2
    leg = math.sin(phase * 2 * math.pi) * 6
    body_cx, body_cy = 70, 100 + bob * 0.4

    tail_swing = math.sin(phase * 2 * math.pi) * 5
    _line(d, [(body_cx + 30, body_cy - 2), (body_cx + 50, body_cy - 24 + tail_swing),
              (body_cx + 42, body_cy - 46 + tail_swing)], FUR_DARK, 9)
    _paw(d, body_cx - 14 - leg, body_cy + 26, 7)  # back paws
    _paw(d, body_cx + 14 + leg, body_cy + 26, 7)
    _ellipse(d, body_cx, body_cy, 32, 24, fill=FUR)  # body
    _ellipse(d, body_cx, body_cy + 6, 21, 15, fill=CREAM)
    _paw(d, body_cx - 16 + leg, body_cy + 24, 6.5)  # front paws
    _paw(d, body_cx + 16 - leg, body_cy + 24, 6.5)
    _face(d, body_cx, body_cy - 34 + bob * 0.6, 30)
    return _downsample(img)


def _draw_idle(phase: float, blink: bool) -> Image.Image:
    img = _new_frame()
    d = ImageDraw.Draw(img)
    breathe = math.sin(phase * 2 * math.pi) * 1.2
    body_cx, body_cy = 70, 102

    tail_swish = math.sin(phase * 2 * math.pi * 0.6) * 4
    _line(d, [(body_cx + 30, body_cy - 2), (body_cx + 48, body_cy - 22 + tail_swish),
              (body_cx + 40, body_cy - 42 + tail_swish)], FUR_DARK, 9)
    _paw(d, body_cx - 15, body_cy + 25, 7)
    _paw(d, body_cx + 15, body_cy + 25, 7)
    _ellipse(d, body_cx, body_cy - breathe * 0.15, 32 + breathe, 24 + breathe * 0.4, fill=FUR)
    _ellipse(d, body_cx, body_cy + 6, 21, 15, fill=CREAM)
    _face(d, body_cx, body_cy - 34 - breathe * 0.2, 30, eyes_closed=blink)
    return _downsample(img)


def _draw_sit(phase: float) -> Image.Image:
    img = _new_frame()
    d = ImageDraw.Draw(img)
    breathe = math.sin(phase * 2 * math.pi) * 1.0
    body_cx, body_cy = 70, 108

    _line(d, [(body_cx + 28, body_cy - 6), (body_cx + 48, body_cy + 12), (body_cx + 36, body_cy + 30),
              (body_cx + 14, body_cy + 26)], FUR_DARK, 8)
    _ellipse(d, body_cx, body_cy, 34, 27, fill=FUR)
    _ellipse(d, body_cx, body_cy + 5, 22, 18, fill=CREAM)
    _paw(d, body_cx - 17, body_cy + 26, 7.5)
    _paw(d, body_cx + 17, body_cy + 26, 7.5)
    _face(d, body_cx, body_cy - 40 - breathe * 0.2, 31)
    return _downsample(img)


def _draw_sleep(phase: float) -> Image.Image:
    img = _new_frame()
    d = ImageDraw.Draw(img)
    breathe = math.sin(phase * 2 * math.pi) * 1.0
    cx, cy = 74, 104

    _ellipse(d, cx, cy + breathe * 0.2, 44, 22 + breathe * 0.5, fill=FUR)  # curled loaf body
    _ellipse(d, cx - 2, cy + 6, 28, 12, fill=CREAM)
    d.arc([_s(cx - 34), _s(cy - 24), _s(cx + 38), _s(cy + 20)], start=195, end=345, fill=FUR_DARK, width=int(_s(7)))
    _paw(d, cx - 20, cy + 16, 6.5)  # front paws tucked under chin
    _paw(d, cx - 4, cy + 18, 6.5)
    _face(d, cx - 32, cy - 14, 24, eyes_closed=True, blush=False)  # tucked head, reuses the standard face
    zx, zy = cx + 34, cy - 34  # drifting "Zzz"
    for i, sz_mult in enumerate((1.0, 0.78, 0.58)):
        zxi, zyi = zx + i * 8, zy - i * 11
        sz = 7 * sz_mult
        d.line([(_s(zxi), _s(zyi)), (_s(zxi + sz), _s(zyi)), (_s(zxi), _s(zyi + sz)), (_s(zxi + sz), _s(zyi + sz))],
               fill=WHISKER, width=max(1, int(_s(1.4 * sz_mult))), joint="curve")
    return _downsample(img)


def draw_cat(pose: str, phase: float, face_left: bool) -> Image.Image:
    """Draw one animation frame of the cat.

    pose: 'idle' | 'walk' | 'sit' | 'sleep'
    phase: 0..1 animation phase (walk cycle / breathing/ blink timing)
    face_left: mirror horizontally (walking left, or just a facing choice
               for the stationary poses)
    """
    if pose == "walk":
        img = _draw_walk(phase)
    elif pose == "sit":
        img = _draw_sit(phase)
    elif pose == "sleep":
        img = _draw_sleep(phase)
    else:
        # idle: brief blink once near the top of each phase loop
        img = _draw_idle(phase, blink=phase < 0.06)
    return img.transpose(Image.FLIP_LEFT_RIGHT) if face_left else img


class SpriteCache:
    """Renders and caches PhotoImages per (pose, frame_index, facing)."""

    def __init__(self, frames_per_pose: int = 12):
        self.frames_per_pose = frames_per_pose
        self._cache: dict[tuple[str, int, bool], ImageTk.PhotoImage] = {}

    def get(self, pose: str, phase: float, face_left: bool) -> ImageTk.PhotoImage:
        idx = int(phase * self.frames_per_pose) % self.frames_per_pose
        key = (pose, idx, face_left)
        if key not in self._cache:
            frame_phase = idx / self.frames_per_pose
            pil_img = draw_cat(pose, frame_phase, face_left)
            self._cache[key] = ImageTk.PhotoImage(pil_img)
        return self._cache[key]


# ---------------------------------------------------------------------------
# Windows: desktop icon positions (best-effort; falls back gracefully)
# ---------------------------------------------------------------------------

def get_desktop_icon_positions() -> list[tuple[int, int]]:
    """Read screen coordinates of icons on the real Windows desktop.

    Best-effort: walks Progman/WorkerW -> SHELLDLL_DefView -> SysListView32,
    then reads item positions out of Explorer's process memory. If anything
    about this fails (different Windows build, permissions, etc.) we just
    return an empty list and the caller falls back to random wander points.
    """
    if not HAVE_WIN32:
        return []
    try:
        def find_listview() -> int:
            # Classic path
            progman = win32gui.FindWindow("Progman", None)
            hwnd = win32gui.FindWindowEx(progman, 0, "SHELLDLL_DefView", None)
            if hwnd:
                lv = win32gui.FindWindowEx(hwnd, 0, "SysListView32", None)
                if lv:
                    return lv
            # Newer Explorer sometimes parents the view under a WorkerW sibling
            found = []
            def cb(h, _):
                cls = win32gui.GetClassName(h)
                if cls == "WorkerW":
                    inner = win32gui.FindWindowEx(h, 0, "SHELLDLL_DefView", None)
                    if inner:
                        lv2 = win32gui.FindWindowEx(inner, 0, "SysListView32", None)
                        if lv2:
                            found.append(lv2)
                return True
            win32gui.EnumWindows(cb, None)
            return found[0] if found else 0

        lv_hwnd = find_listview()
        if not lv_hwnd:
            return []

        _, pid = win32process.GetWindowThreadProcessId(lv_hwnd)
        PROCESS_VM_OPERATION = 0x0008
        PROCESS_VM_READ = 0x0010
        PROCESS_VM_WRITE = 0x0020
        PROCESS_QUERY_INFORMATION = 0x0400
        h_process = ctypes.windll.kernel32.OpenProcess(
            PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
            False, pid,
        )
        if not h_process:
            return []

        try:
            LVM_GETITEMCOUNT = 0x1004
            LVM_GETITEMPOSITION = 0x1010
            count = win32gui.SendMessage(lv_hwnd, LVM_GETITEMCOUNT, 0, 0)
            if not count:
                return []

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            remote_buf = ctypes.windll.kernel32.VirtualAllocEx(
                h_process, None, ctypes.sizeof(POINT), 0x1000 | 0x2000, 0x04
            )
            if not remote_buf:
                return []

            positions: list[tuple[int, int]] = []
            pt = POINT()
            for i in range(count):
                ok = ctypes.windll.user32.SendMessageW(lv_hwnd, LVM_GETITEMPOSITION, i, remote_buf)
                if not ok:
                    continue
                bytes_read = ctypes.c_size_t(0)
                ctypes.windll.kernel32.ReadProcessMemory(
                    h_process, remote_buf, ctypes.byref(pt), ctypes.sizeof(pt), ctypes.byref(bytes_read)
                )
                # positions are client-relative to the listview; convert to screen coords
                screen = win32gui.ClientToScreen(lv_hwnd, (pt.x, pt.y))
                positions.append(screen)

            ctypes.windll.kernel32.VirtualFreeEx(h_process, remote_buf, 0, 0x8000)
            return positions
        finally:
            ctypes.windll.kernel32.CloseHandle(h_process)
    except Exception as exc:  # noqa: BLE001 - best-effort feature, never crash the pet
        print(f"[desktop-icons] unavailable ({exc}); falling back to random wander.")
        return []


def list_visible_windows() -> list[tuple[int, str]]:
    """Return (hwnd, title) for real top-level windows a user would recognize."""
    results: list[tuple[int, str]] = []
    if not HAVE_WIN32:
        return results

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        # skip our own cat window and common shell/system windows
        if title in ("Program Manager", "Pawmate"):
            return True
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if exstyle & win32con.WS_EX_TOOLWINDOW:
            return True
        results.append((hwnd, title))
        return True

    win32gui.EnumWindows(cb, None)
    return results


# ---------------------------------------------------------------------------
# Optional free-LLM command interpreter (OpenRouter / Cloudflare Workers AI)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You control a small desktop pet cat. Given the user's message, reply with ONLY a "
    "compact JSON object, no prose, matching one of:\n"
    '{"action":"open_app","target":"<app name or path>"}\n'
    '{"action":"close_app","target":"<window title substring>"}\n'
    '{"action":"pose","target":"walk|sit|sleep|idle"}\n'
    '{"action":"say","target":"<short reply text>"}\n'
    "Never invent destructive actions beyond open_app/close_app. Keep target short."
)


def ask_llm(user_text: str) -> dict | None:
    """Return a parsed {action, target} dict from a free LLM, or None if unavailable."""
    try:
        import requests
    except ImportError:
        return None

    if OPENROUTER_API_KEY:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text},
                    ],
                    "temperature": 0.2,
                },
                timeout=20,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm/openrouter] {exc}")

    if CF_ACCOUNT_ID and CF_API_TOKEN:
        try:
            resp = requests.post(
                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}",
                headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
                json={
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text},
                    ]
                },
                timeout=20,
            )
            resp.raise_for_status()
            content = resp.json()["result"]["response"]
            return _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            print(f"[llm/cloudflare] {exc}")

    return None


def _extract_json(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Local (no-AI) command parsing
# ---------------------------------------------------------------------------

def parse_local_command(text: str) -> dict | None:
    t = text.strip().lower()
    if t in ("walk", "wander"):
        return {"action": "pose", "target": "walk"}
    if t in ("sit", "sit down"):
        return {"action": "pose", "target": "sit"}
    if t in ("sleep", "nap"):
        return {"action": "pose", "target": "sleep"}
    if t in ("idle", "stand"):
        return {"action": "pose", "target": "idle"}
    if t.startswith("open "):
        return {"action": "open_app", "target": text[5:].strip()}
    if t.startswith("close "):
        return {"action": "close_app", "target": text[6:].strip()}
    return None


# ---------------------------------------------------------------------------
# The pet window
# ---------------------------------------------------------------------------

@dataclass
class PetState:
    x: float
    y: float
    pose: str = "idle"
    facing_left: bool = False
    phase: float = 0.0
    target: tuple[float, float] | None = None
    pause_until: float = 0.0
    idle_until: float = 0.0
    dragging: bool = False
    idle_walk_cycles: int = 0


class PawmatePrototype:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Pawmate")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            print("This Tk build doesn't support -transparentcolor "
                  "(true only on Windows). The window will render opaque.")

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        start_x = screen_w // 2 - CAT_SIZE // 2
        start_y = screen_h // 2 - CAT_SIZE // 2
        self.root.geometry(f"{CAT_SIZE}x{CAT_SIZE}+{start_x}+{start_y}")

        self.canvas = tk.Canvas(self.root, width=CAT_SIZE, height=CAT_SIZE,
                                 bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack()

        self.sprites = SpriteCache()
        self.state = PetState(x=float(start_x), y=float(start_y))
        self.desktop_icons: list[tuple[int, int]] = []
        self.image_id = self.canvas.create_image(CAT_SIZE // 2, CAT_SIZE // 2, image=None)

        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Double-Button-1>", lambda e: self._open_chat())

        self.root.after(200, self._apply_win32_styles)
        self.root.after(500, self._refresh_desktop_icons)
        self._pick_new_target()
        self._tick_last = time.time()
        self.root.after(TICK_MS, self._tick)

        self._drag_offset = (0, 0)

    # -- window styling (no-focus-steal, always-on-top, click-through via colorkey) --
    def _apply_win32_styles(self):
        if not HAVE_WIN32:
            return
        try:
            hwnd = self.root.winfo_id()
            exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            exstyle |= win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)
        except Exception as exc:  # noqa: BLE001
            print(f"[win32-style] {exc}")

    def _refresh_desktop_icons(self):
        self.desktop_icons = get_desktop_icon_positions()
        if self.desktop_icons:
            print(f"[desktop-icons] found {len(self.desktop_icons)} icons to visit.")
        else:
            print("[desktop-icons] none found — cat will wander randomly instead.")
        # refresh occasionally in case the user rearranges icons
        self.root.after(60_000, self._refresh_desktop_icons)

    # -- movement targets --
    def _pick_new_target(self):
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        if self.desktop_icons and random.random() < 0.7:
            ix, iy = random.choice(self.desktop_icons)
            tx = max(0, min(screen_w - CAT_SIZE, ix - CAT_SIZE // 2))
            ty = max(0, min(screen_h - CAT_SIZE, iy - CAT_SIZE // 2))
        else:
            tx = random.uniform(0, screen_w - CAT_SIZE)
            ty = random.uniform(0, screen_h - CAT_SIZE)
        self.state.target = (tx, ty)
        self.state.pose = "walk"

    # -- drag handling --
    def _on_drag_start(self, event):
        self.state.dragging = True
        self._drag_offset = (event.x, event.y)
        self.state.pose = "idle"
        self.state.target = None

    def _on_drag_move(self, event):
        if not self.state.dragging:
            return
        ox, oy = self._drag_offset
        new_x = self.root.winfo_x() + (event.x - ox)
        new_y = self.root.winfo_y() + (event.y - oy)
        self.state.x, self.state.y = float(new_x), float(new_y)
        self.root.geometry(f"+{new_x}+{new_y}")

    def _on_drag_end(self, event):
        self.state.dragging = False
        self.state.idle_until = time.time() + random.uniform(IDLE_MIN_S, IDLE_MAX_S)
        self.state.pose = "idle"

    # -- right-click menu --
    def _on_right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🐈 Pawmate prototype", state="disabled")
        menu.add_separator()
        menu.add_command(label="Chat / command…", command=self._open_chat)
        menu.add_separator()

        open_menu = tk.Menu(menu, tearoff=0)
        for label, cmd in CURATED_APPS.items():
            open_menu.add_command(label=label, command=lambda c=cmd: self._open_app(c))
        open_menu.add_separator()
        open_menu.add_command(label="Browse for .exe…", command=self._open_app_browse)
        menu.add_cascade(label="Open App", menu=open_menu)

        close_menu = tk.Menu(menu, tearoff=0)
        windows = list_visible_windows()
        if not windows:
            close_menu.add_command(label="(no windows found)", state="disabled")
        for hwnd, title in windows[:20]:
            label = title if len(title) <= 45 else title[:42] + "..."
            close_menu.add_command(label=label, command=lambda h=hwnd, t=title: self._close_app_confirm(h, t))
        menu.add_cascade(label="Close App", menu=close_menu)

        menu.add_separator()
        menu.add_command(label="Sit", command=lambda: self._set_pose("sit"))
        menu.add_command(label="Sleep", command=lambda: self._set_pose("sleep"))
        menu.add_command(label="Walk", command=lambda: self._set_pose("walk"))
        menu.add_separator()
        menu.add_command(label="Quit", command=self.root.destroy)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _set_pose(self, pose: str):
        self.state.pose = pose
        self.state.target = None
        self.state.idle_until = time.time() + 3600 if pose in ("sit", "sleep") else 0
        if pose == "walk":
            self._pick_new_target()

    # -- app open/close --
    def _open_app(self, cmd: str):
        try:
            if cmd.startswith("start "):
                os.system(cmd)
            else:
                subprocess.Popen(cmd, shell=False)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Pawmate", f"Couldn't open that app:\n{exc}")

    def _open_app_browse(self):
        path = filedialog.askopenfilename(title="Choose an app to open",
                                           filetypes=[("Executables", "*.exe"), ("All files", "*.*")])
        if path:
            self._open_app(path)

    def _close_app_confirm(self, hwnd: int, title: str):
        # Destructive action = human click, per the plan's principle.
        if not messagebox.askyesno("Pawmate — confirm", f'Close "{title}"?'):
            return
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Pawmate", f"Couldn't close that window:\n{exc}")

    def _close_app_by_title_substring(self, substring: str):
        substring = substring.strip().lower()
        matches = [(h, t) for h, t in list_visible_windows() if substring in t.lower()]
        if not matches:
            messagebox.showinfo("Pawmate", f'No open window matching "{substring}".')
            return
        hwnd, title = matches[0]
        self._close_app_confirm(hwnd, title)

    # -- chat / command bar --
    def _open_chat(self):
        text = simpledialog.askstring(
            "Pawmate",
            "Command (try: open notepad / close chrome / walk / sit / sleep)"
            + ("\nFree-text also works — AI is connected." if (OPENROUTER_API_KEY or (CF_ACCOUNT_ID and CF_API_TOKEN)) else
               "\nSet OPENROUTER_API_KEY to also accept free-text via AI."),
            parent=self.root,
        )
        if not text:
            return
        action = parse_local_command(text)
        if action is None:
            action = ask_llm(text)
        if action is None:
            messagebox.showinfo("Pawmate", "Didn't understand that — try a command like 'open notepad'.")
            return
        self._dispatch_action(action)

    def _dispatch_action(self, action: dict):
        kind = action.get("action")
        target = str(action.get("target", "")).strip()
        if kind == "pose" and target in ("walk", "sit", "sleep", "idle"):
            self._set_pose(target)
        elif kind == "open_app":
            cmd = CURATED_APPS.get(target.title(), target)
            self._open_app(cmd)
        elif kind == "close_app":
            self._close_app_by_title_substring(target)
        elif kind == "say":
            messagebox.showinfo("Pawmate says", target or "…")
        else:
            messagebox.showinfo("Pawmate", f"Unrecognized action: {action}")

    # -- main loop --
    def _tick(self):
        now = time.time()
        dt = now - self._tick_last
        self._tick_last = now
        st = self.state

        if not st.dragging:
            if st.pose == "walk" and st.target is not None:
                tx, ty = st.target
                dx, dy = tx - st.x, ty - st.y
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 3:
                    st.pose = "idle"
                    st.idle_until = now + random.uniform(PAUSE_AT_TARGET_MIN_S, PAUSE_AT_TARGET_MAX_S)
                    st.idle_walk_cycles += 1
                else:
                    step = WALK_SPEED_PX_S * dt
                    step = min(step, dist)
                    st.x += dx / dist * step
                    st.y += dy / dist * step
                    st.facing_left = dx < 0
                    self.root.geometry(f"+{int(st.x)}+{int(st.y)}")
            elif st.pose == "idle" and st.target is None and now >= st.idle_until:
                if st.idle_walk_cycles >= SLEEP_AFTER_IDLE_CYCLES:
                    st.pose = "sleep"
                    st.idle_until = now + random.uniform(15, 30)
                    st.idle_walk_cycles = 0
                else:
                    self._pick_new_target()
            elif st.pose == "sleep" and now >= st.idle_until:
                st.pose = "idle"
                st.idle_until = now + random.uniform(IDLE_MIN_S, IDLE_MAX_S)

        st.phase = (st.phase + dt * (0.9 if st.pose == "walk" else 0.25)) % 1.0
        img = self.sprites.get(st.pose, st.phase, st.facing_left)
        self.canvas.itemconfig(self.image_id, image=img)
        self._current_image_ref = img  # keep a reference so Tk doesn't GC it

        self.root.after(TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = PawmatePrototype()
    app.run()
