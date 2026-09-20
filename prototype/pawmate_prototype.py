"""
Pawmate Prototype — a live desktop cat you can actually run today.

This is a deliberately small, single-file, dependency-light prototype built to prove
the core "desktop pet" mechanics from docs/master-plan.md *before* investing in the
full Rust/Tauri architecture (see Part 7, Phase 1 "Spikes"). It runs on Windows 10/11
with plain Python.

What it demonstrates (no AI required):
  - A transparent, always-on-top, click-through overlay window (S1/S2 style spike)
  - A real cat sprite (the classic "Neko" desktop-pet pixel art — see
    assets/CREDIT.md; two from-scratch hand-drawn attempts didn't read as
    a cat at this size, so this uses actual art instead of more geometry
    guesswork) — idle / sit / sleep / walk (4-directional) / a jump
    celebration on app open / a paw-swipe on app close
  - Precise movement: wanders the screen, or (best-effort) walks between your real
    desktop icon positions, pausing at each one ("walking over your folders")
  - Drag-to-move
  - Open any installed app by name via chat — resolved dynamically against
    everything Windows' own Start menu knows about (Get-StartApps), with a
    "did you mean X?" confirm on a typo instead of a dead "file not found"
  - Right-click menu: Close App (pick a running window, confirm before
    closing — destructive action = human click, per the plan's principles;
    kept as a live menu because it shows *running* windows, which Start
    Menu can't — Open App as a static list was cut, since chat's resolver
    replaces it and a hardcoded list was never anything but a worse Start
    menu)
  - A chat/command bar: typed commands are parsed locally first with zero AI
    ("open notepad", "close chrome", "walk", "sit", "sleep"). If you set
    OPENROUTER_API_KEY (or CF_ACCOUNT_ID + CF_API_TOKEN for Cloudflare
    Workers AI) as environment variables, free-text goes to a free LLM to
    pick an action — but nothing destructive ever runs without your
    confirm click, and open/close app names still go through the same
    resolver either way.

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
import difflib
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog
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

from PIL import Image, ImageTk

import pawmate_digest as digest
import pawmate_hotkey as hotkeys
import pawmate_ui as ui
from pawmate_breaks import BreakOverlay, BreakScheduler
from pawmate_classify import AdaptiveClassifier
from pawmate_git import GitWatcher
from pawmate_tracking import (ActivityTracker, Settings, Store, Todos,
                              fmt_minutes, foreground_app, idle_seconds)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CAT_SIZE = 128                 # window / sprite size in px — 4x the sprite sheet's native 32px, for crisp NEAREST upscaling
TRANSPARENT_KEY = "#ff00ff"    # color-keyed as transparent by the OS
FPS = 30
TICK_MS = int(1000 / FPS)
WALK_SPEED_PX_S = 48.0          # an unhurried, deliberate walk — not a scurry across the screen
WALK_MIN_SPEED_PX_S = 10.0
WALK_ACCEL_PX_S2 = 90.0         # ease in/out of walks instead of snapping to full speed
WALK_EASE_PX = 55.0             # start braking this far from the destination
STRIDE_PX = 34.0                # ground covered per full walk cycle; the animation phase is
                                 # driven by distance/STRIDE_PX so paws can never skate
IDLE_CYCLE_HZ = 0.5             # frame swaps/sec at rest (breathing-speed, not a slideshow)
IDLE_BOB_PX = 2.5               # small vertical bob while idle/sit, so rest is never perfectly frozen
WALK_BOB_PX = 2.5               # body bounce per footfall — with a low-frame-count sprite this is
                                 # most of what separates "walking" from "a picture being dragged"
JUMP_HEIGHT_PX = 40.0
WATER_EVERY_S = 45 * 60         # plan default: a glass every 45 active minutes
BREAK_EVERY_S = 50 * 60         # plan default: a break after 50 minutes           # how high the window actually rises during the jump action
IDLE_MIN_S, IDLE_MAX_S = 2.0, 5.0
PAUSE_AT_TARGET_MIN_S, PAUSE_AT_TARGET_MAX_S = 1.5, 4.0
SLEEP_AFTER_IDLE_CYCLES = 4     # after this many idle->walk loops with no user interaction, nap

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct")

# ---------------------------------------------------------------------------
# Cat sprite loading.
#
# Drop-in artist-made art is the goal: a 2-frame-per-pose sheet can't look
# smooth no matter how the playback is tuned, and hand-drawing a convincing
# cat out of primitives didn't work either (two attempts, both wrong). So
# this loads whatever sprite pack you put in assets/cat/, handling the three
# layouts asset packs actually ship in:
#
#   1. assets/cat/<anim>/frame0.png, frame1.png, ...   (folder per animation)
#   2. assets/cat/<anim>.png                           (horizontal strip)
#   3. assets/cat/sheet.png + sheet.json               (grid, explicit map)
#
# Animation names it looks for, in preference order per pose, are in
# _ANIM_ALIASES. Run `python pawmate_prototype.py --inspect` to see exactly
# what got discovered and dump a labeled contact sheet to check the mapping.
#
# Falls back to the bundled oneko.gif (see assets/CREDIT.md) when no pack is
# present, so the app always runs.
# ---------------------------------------------------------------------------

MAGENTA_OPAQUE = (255, 0, 255, 255)
_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_CAT_DIR = os.path.join(_ASSET_DIR, "cat")
_ONEKO_PATH = os.path.join(_ASSET_DIR, "oneko.gif")

# Logical pose -> candidate animation names in a downloaded pack (case/sep
# insensitive). First one that exists wins.
_ANIM_ALIASES: dict[str, list[str]] = {
    "walk": ["walk", "walking", "walk_right", "run", "running", "move"],
    "idle": ["idle", "idle_blink", "stand", "standing", "breathe"],
    "sit": ["sit", "sitting", "sit_idle", "sitdown", "sit_down"],
    "sleep": ["sleep", "sleeping", "lay", "laying", "lie", "lying", "rest"],
    "jump": ["jump", "jumping", "pounce", "leap", "attack"],
    "swipe": ["swipe", "scratch", "attack", "paw", "hit", "claw"],
    "groom": ["groom", "grooming", "lick", "licking", "wash"],
}

_IMG_EXT = (".png", ".gif", ".webp", ".bmp")


def _norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _key_out_background(img: Image.Image) -> Image.Image:
    """Make a sprite's background transparent when it has no real alpha.

    Plenty of packs ship frames with NO alpha channel at all — the
    background is just a flat colour (often a lurid key colour, or white).
    Pasting those straight onto the colour key covers it completely, which
    is what puts a solid rectangle around the character on screen. So: if
    the image is fully opaque, take the majority corner colour as the
    background and knock it out.
    """
    alpha = img.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return img                                    # already has real transparency

    w, h = img.size
    corners = [img.getpixel(p)[:3] for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    bg = max(set(corners), key=corners.count)
    if corners.count(bg) < 3:
        return img                                    # no consistent background; leave it alone

    px = img.load()
    tol = 12
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if abs(r - bg[0]) <= tol and abs(g - bg[1]) <= tol and abs(b - bg[2]) <= tol:
                px[x, y] = (r, g, b, 0)
    return img


def _to_colorkey(img: Image.Image, size: int) -> Image.Image:
    """Scale to `size` and composite onto the opaque colour key.

    Tk's -transparentcolor is an exact match, not alpha blending, so an
    anti-aliased edge pixel (half character, half key colour) matches
    neither and shows up as a coloured halo. Scaling happens on the ALPHA
    image first and the alpha is then hardened to 0/255, so edges stay
    clean instead of fringing.
    """
    img = _key_out_background(img.convert("RGBA"))

    if img.size != (size, size):
        w, h = img.size
        scale = min(size / w, size / h)
        new = (max(1, int(w * scale)), max(1, int(h * scale)))
        resample = Image.NEAREST if max(img.size) < 96 else Image.LANCZOS
        img = img.resize(new, resample)

    r, g, b, a = img.split()
    a = a.point(lambda v: 255 if v >= 128 else 0)     # harden: no partial-alpha fringe
    img = Image.merge("RGBA", (r, g, b, a))

    out = Image.new("RGBA", (size, size), MAGENTA_OPAQUE)
    ox = (size - img.size[0]) // 2
    oy = size - img.size[1]                           # feet on the bottom edge
    out.paste(img, (ox, oy), img)
    return out


def _slice_strip(img: Image.Image) -> list[Image.Image]:
    """Split a horizontal strip into frames.

    Prefers fully-transparent gutter columns (exact, handles uneven frames);
    falls back to assuming square frames (width being an exact multiple of
    height is the near-universal convention for strips).
    """
    w, h = img.size
    alpha = img.convert("RGBA").split()[3]
    cols = alpha.load()
    empty = []
    for x in range(w):
        if all(cols[x, y] == 0 for y in range(0, h, max(1, h // 24))):
            empty.append(x)
    # group contiguous empty columns into gutters, cut at their midpoints
    if empty and len(empty) < w * 0.9:
        runs, start = [], empty[0]
        for a, b in zip(empty, empty[1:]):
            if b != a + 1:
                runs.append((start, a))
                start = b
        runs.append((start, empty[-1]))
        interior = [r for r in runs if r[0] > 0 and r[1] < w - 1]
        if interior:
            cuts = [0] + [(a + b) // 2 for a, b in interior] + [w]
            frames = [img.crop((cuts[i], 0, cuts[i + 1], h)) for i in range(len(cuts) - 1)]
            frames = [f for f in frames if f.getbbox()]
            if len(frames) >= 2:
                return frames
    if w % h == 0 and w // h >= 2:
        n = w // h
        return [img.crop((i * h, 0, (i + 1) * h, h)) for i in range(n)]
    return [img]


def _natural_key(path: str):
    """Sort frame files the way a human numbers them (2 before 10)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", stem)]


def _extract_zips(root: str):
    """Auto-unzip any archive dropped straight into assets/cat/."""
    import zipfile
    for name in os.listdir(root):
        if not name.lower().endswith(".zip"):
            continue
        marker = os.path.join(root, "." + name + ".extracted")
        if os.path.exists(marker):
            continue
        try:
            with zipfile.ZipFile(os.path.join(root, name)) as z:
                z.extractall(os.path.join(root, os.path.splitext(name)[0]))
            open(marker, "w").close()
            print(f"[sprite] extracted {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[sprite] couldn't extract {name}: {exc}")


def _discover(root: str) -> dict[tuple[str, str], list[str]]:
    """Catalog every candidate animation under `root`, recursively.

    Asset packs unzip into arbitrary nesting (`cat/Black Cat/PNG/walk/*.png`)
    and name frames inconsistently (`walk/0.png`, `walk_00.png`,
    `cat_walk_01.png`), so this indexes BOTH:
      ("dir",  <folder name>) -> images directly inside that folder
      ("file", <stem base>)   -> images sharing a stem once the trailing
                                 frame number is stripped
    Callers then match these keys against animation-name aliases.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    file_dirs: dict[str, set[str]] = {}          # stem base -> directories it appeared in
    dir_groups: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        imgs = [f for f in filenames if f.lower().endswith(_IMG_EXT)]
        if not imgs:
            continue
        dirkey = _norm_name(os.path.basename(dirpath))
        if dirkey and len(imgs) >= 2:
            groups.setdefault(("dir", dirkey), []).extend(os.path.join(dirpath, f) for f in imgs)
            dir_groups.add(dirpath)
        for f in imgs:
            stem = os.path.splitext(f)[0]
            base = _norm_name(re.sub(r"[\s_\-.]*\d+\s*$", "", stem))
            if base:
                groups.setdefault(("file", base), []).append(os.path.join(dirpath, f))
                file_dirs.setdefault(base, set()).add(dirpath)

    # A stem shared across several folders (the very common `Walk/frame_00.png`,
    # `Idle/frame_00.png` convention) is a naming coincidence, not one
    # animation — it would otherwise splice every pose into a single blob.
    # Drop those, but only when the folders themselves already gave us groups.
    for base, dirs in file_dirs.items():
        if len(dirs) > 1 and dirs <= dir_groups:
            groups.pop(("file", base), None)

    for paths in groups.values():
        paths.sort(key=_natural_key)
    return groups


def _open_frames(path: str) -> list[Image.Image]:
    """Every frame of an image file.

    Crucially this handles ANIMATED files (GIF/WebP): PIL hands you only
    frame 0 unless you seek through the sequence, so a perfectly good
    8-frame walk GIF otherwise silently collapses into one static picture.
    """
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    if n <= 1:
        return [im.convert("RGBA")]
    out = []
    for i in range(n):
        try:
            im.seek(i)
        except EOFError:
            break
        out.append(im.convert("RGBA"))
    return out


def _load_frames(paths: list[str]) -> list[Image.Image]:
    """Turn a discovered group of files into an ordered animation."""
    loaded: list[tuple[str, list[Image.Image]]] = []
    for p in paths:
        try:
            frames = _open_frames(p)
            if frames:
                loaded.append((p, frames))
        except Exception as exc:  # noqa: BLE001
            print(f"[sprite] couldn't read {p}: {exc}")
    if not loaded:
        return []

    # If the files are themselves animations, each one is a COMPLETE clip —
    # they're variants (commonly the same clip exported at 2x and 4x, whose
    # names collapse to the same stem), not frames of a shared animation.
    # Pick the richest/highest-resolution one rather than splicing them.
    animated = [(p, f) for p, f in loaded if len(f) > 1]
    if animated:
        path, frames = max(animated, key=lambda pf: (len(pf[1]), pf[1][0].size[0] * pf[1][0].size[1]))
        if len(animated) > 1:
            print(f"[sprite] {len(animated)} animated variants; using {os.path.basename(path)} "
                  f"({len(frames)} frames, {frames[0].size[0]}x{frames[0].size[1]})")
        return frames

    if len(loaded) == 1:
        return _slice_strip(loaded[0][1][0])          # a lone still image = a strip
    return [f[0] for _, f in loaded]                  # one still image per frame


# ---------------------------------------------------------------------------
# Built-in character: a procedural blob.
#
# Every failed attempt at drawing this pet was an ANATOMY failure — legs,
# proportions, gait. A blob has no anatomy to get wrong: it's animated purely
# with squash & stretch (volume-preserving, so it never looks like it's just
# being scaled), which is continuous maths rather than a fixed set of drawn
# frames. That means it can be sampled at any smoothness, and there is no
# "that doesn't look like the animal" failure mode.
# ---------------------------------------------------------------------------

BLOB_BODY = (94, 204, 176, 255)
BLOB_DARK = (58, 166, 141, 255)
BLOB_HILITE = (168, 234, 216, 255)
BLOB_EYE = (28, 42, 48, 255)
BLOB_WHITE = (255, 255, 255, 255)
BLOB_SHADOW = (150, 120, 160, 255)
BLOB_EYE_HEX = "#1c2a30"
BLOB_WHITE_HEX = "#ffffff"
GAZE_FOLLOW_HZ = 9.0            # how fast the eyes catch up to the cursor
_BLOB_SS = 4
_BLOB_GROUND = 108.0
_BLOB_LOGICAL = 128


def _blob_frame(rise: float, squash: float, look: float = 1.0, blink: bool = False,
                size: int = 128) -> Image.Image:
    """One blob frame. `squash` > 1 is wide/flat, < 1 is tall/thin.

    Height scales inversely with width so the blob conserves volume — that's
    what makes squash & stretch read as a soft body rather than a resize.
    """
    from PIL import ImageDraw as _ID
    ss = _BLOB_SS
    big = _BLOB_LOGICAL * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = _ID.Draw(img)

    def S(v):
        return v * ss

    def E(cx, cy, rx, ry, **kw):
        d.ellipse([S(cx - rx), S(cy - ry), S(cx + rx), S(cy + ry)], **kw)

    r0 = 30.0
    rx, ry = r0 * squash, r0 / squash
    cx, cy = 64.0, _BLOB_GROUND - ry - rise

    t = max(0.0, min(1.0, rise / 40.0))                       # shadow shrinks with height
    E(cx, _BLOB_GROUND + 3, (26 - 10 * t) * squash, 6 - 2.5 * t, fill=BLOB_SHADOW)

    E(cx, cy, rx, ry, fill=BLOB_BODY)
    E(cx, cy + ry * 0.34, rx * 0.82, ry * 0.5, fill=BLOB_DARK)
    E(cx, cy, rx, ry * 0.99, fill=BLOB_BODY)
    E(cx - rx * 0.34, cy - ry * 0.40, rx * 0.26, ry * 0.19, fill=BLOB_HILITE)

    # Eyes are NOT baked in here — they're drawn as live canvas items on top so
    # they can track the cursor continuously. Caching a frame per look-direction
    # would mean thousands of pre-rendered images; this is one image per pose.
    ex, ey = cx, cy - ry * 0.10
    mw = rx * 0.17
    d.arc([S(ex - mw), S(ey + ry * 0.16), S(ex + mw), S(ey + ry * 0.16 + ry * 0.35)],
          start=10, end=170, fill=BLOB_EYE, width=int(S(1.5)))

    img = img.resize((size, size), Image.LANCZOS)
    r, g, b, a = img.split()
    a = a.point(lambda v: 255 if v >= 128 else 0)             # clean colour-key edges
    img = Image.merge("RGBA", (r, g, b, a))
    out = Image.new("RGBA", (size, size), MAGENTA_OPAQUE)
    out.paste(img, (0, 0), img)
    return out


def _blob_pose(pose: str, p: float) -> tuple[float, float, bool]:
    """(rise, squash, blink) for a pose at cycle position p."""
    if pose in ("walk", "jump"):
        # anticipate (crouch) -> launch -> arc -> land -> wobble out
        if p < 0.18:
            return 0.0, 1.0 + 0.22 * math.sin((p / 0.18) * math.pi), False
        if p < 0.80:
            u = (p - 0.18) / 0.62
            return math.sin(u * math.pi) * 34, 1.0 - 0.20 * math.sin(u * math.pi), False
        u = (p - 0.80) / 0.20
        return 0.0, 1.0 + 0.26 * math.sin(u * math.pi) * math.cos(u * math.pi * 3), False
    if pose == "sleep":
        return 0.0, 1.34 + 0.05 * math.sin(p * 2 * math.pi), True
    if pose == "sit":
        return 0.0, 1.16 + 0.03 * math.sin(p * 2 * math.pi), p % 1.0 < 0.06
    if pose == "swipe":
        return 0.0, 1.0 + 0.30 * math.sin(p * math.pi), False
    return 0.0, 1.0 + 0.055 * math.sin(p * 2 * math.pi), p % 1.0 < 0.07   # idle


def blob_eye_layout(pose: str, phase: float, size: int = 128) -> dict:
    """Where the eyes sit for this pose/phase, in final image pixels.

    Mirrors the body maths in _blob_frame so canvas-drawn eyes stay glued to
    the squashing, hopping body.
    """
    rise, squash, blink = _blob_pose(pose, phase)
    k = size / float(_BLOB_LOGICAL)
    r0 = 30.0
    rx, ry = r0 * squash, r0 / squash
    cx, cy = 64.0, _BLOB_GROUND - ry - rise
    return {
        "cx": cx * k, "cy": (cy - ry * 0.10) * k,
        "spacing": rx * 0.27 * k,
        "white_rx": rx * 0.115 * k, "white_ry": ry * 0.155 * k,
        "pupil_rx": rx * 0.072 * k, "pupil_ry": ry * 0.10 * k,
        "travel_x": rx * 0.085 * k, "travel_y": ry * 0.075 * k,
        "blink": blink,
    }


class BlobSource:
    """The built-in procedural character. Same interface as SpriteSource."""

    FRAMES = 24                  # sampling resolution of the continuous motion
    handles_own_bob = True       # its hop already carries the vertical motion

    def __init__(self, size: int):
        self.size = size
        self.origin = "builtin:blob (procedural)"
        self.catalog: dict[tuple[str, str], list[str]] = {}
        self._cache: dict[tuple[str, int, bool], Image.Image] = {}

    def frame(self, pose: str, phase: float, face_left: bool) -> Image.Image:
        idx = int(phase * self.FRAMES) % self.FRAMES
        key = (pose, idx, face_left)
        if key not in self._cache:
            rise, squash, blink = _blob_pose(pose, idx / self.FRAMES)
            img = _blob_frame(rise, squash, look=1.0, blink=blink, size=self.size)
            if face_left:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            self._cache[key] = img
        return self._cache[key]

    def frame_count(self, pose: str) -> int:
        return self.FRAMES

    def describe(self) -> str:
        return f"[sprite] source={self.origin} — {self.FRAMES} sampled frames/pose, continuous motion"

    def contact_sheet(self, path: str):
        poses = ("walk", "idle", "sit", "sleep", "swipe")
        sheet = Image.new("RGB", (self.size * 8, self.size * len(poses)), (240, 240, 244))
        for r, pose in enumerate(poses):
            for c in range(8):
                f = self.frame(pose, c / 8, False)
                sheet.paste(f.convert("RGB"), (c * self.size, r * self.size))
        sheet.save(path)


class SpriteSource:
    """Discovers and loads cat animations from assets/cat/, else oneko."""

    handles_own_bob = False

    def __init__(self, size: int):
        self.size = size
        self.anims: dict[str, list[Image.Image]] = {}
        self.catalog: dict[tuple[str, str], list[str]] = {}
        self.origin = "none"
        self._load()

    # -- discovery ---------------------------------------------------------
    def _load(self):
        if os.path.isdir(_CAT_DIR) and self._load_pack():
            self.origin = f"pack:{_CAT_DIR}"
        else:
            self._load_oneko()
            self.origin = "builtin:oneko.gif"
        self._fill_gaps()

    def _load_pack(self) -> bool:
        _extract_zips(_CAT_DIR)
        catalog = _discover(_CAT_DIR)
        self.catalog = catalog
        found: dict[str, list[Image.Image]] = {}

        for pose, aliases in _ANIM_ALIASES.items():
            best: tuple[int, list[str]] | None = None
            for rank, alias in enumerate(aliases):
                key = _norm_name(alias)
                for (kind, name), paths in catalog.items():
                    if name == key:
                        score = 1000 - rank * 10          # exact name match on a preferred alias
                    elif key in name:
                        score = 500 - rank * 10           # e.g. "catwalkright" contains "walk"
                    else:
                        continue
                    score += min(len(paths), 20)          # prefer the richer animation
                    if kind == "dir":
                        score += 5                        # a folder is a stronger signal than a stem
                    if best is None or score > best[0]:
                        best = (score, paths)
            if not best:
                continue
            frames = _load_frames(best[1])
            if frames:
                found[pose] = frames

        if not found:
            return False
        self.anims = {k: [_to_colorkey(f, self.size) for f in v] for k, v in found.items()}
        return True

    def _load_oneko(self):
        """Bundled fallback: 8x4 grid of 32px frames, 2 frames per pose."""
        cells = {
            "idle": [(2, 2), (1, 1)], "sit": [(4, 3), (1, 3)], "sleep": [(2, 0), (2, 1)],
            "walk": [(4, 2), (5, 2)],
            "jump": [(2, 3), (7, 3), (3, 2)], "swipe": [(1, 2), (0, 0), (1, 0)],
        }
        try:
            sheet = Image.open(_ONEKO_PATH).convert("RGBA")
        except FileNotFoundError:
            print(f"[sprite] no pack in {_CAT_DIR} and {_ONEKO_PATH} is missing — nothing to draw.")
            return
        for pose, cs in cells.items():
            self.anims[pose] = [
                _to_colorkey(sheet.crop((c * 32, r * 32, (c + 1) * 32, (r + 1) * 32)), self.size)
                for c, r in cs
            ]

    def _fill_gaps(self):
        """Any pose the pack didn't provide falls back to something sensible."""
        fallbacks = {"walk": "idle", "idle": "walk", "sit": "idle", "sleep": "sit",
                     "jump": "walk", "swipe": "idle", "groom": "sit"}
        for pose, alt in fallbacks.items():
            if not self.anims.get(pose):
                src = self.anims.get(alt) or next(iter(self.anims.values()), None)
                if src:
                    self.anims[pose] = src

    # -- access ------------------------------------------------------------
    def frame(self, pose: str, phase: float, face_left: bool) -> Image.Image:
        frames = self.anims.get(pose) or self.anims.get("idle")
        if not frames:
            return Image.new("RGBA", (self.size, self.size), MAGENTA_OPAQUE)
        idx = int(phase * len(frames)) % len(frames)
        img = frames[idx]
        return img.transpose(Image.FLIP_LEFT_RIGHT) if face_left else img

    def frame_count(self, pose: str) -> int:
        return len(self.anims.get(pose) or ())

    def describe(self) -> str:
        parts = ", ".join(f"{k}:{len(v)}f" for k, v in sorted(self.anims.items()))
        return f"[sprite] source={self.origin} — {parts}"

    def contact_sheet(self, path: str):
        """Dump every loaded frame, labeled, so the mapping can be eyeballed."""
        from PIL import ImageDraw as _ID
        rows = [(k, v) for k, v in sorted(self.anims.items())]
        if not rows:
            return
        cols = max(len(v) for _, v in rows)
        pad = 16
        sheet = Image.new("RGB", (cols * self.size, len(rows) * (self.size + pad)), (240, 240, 244))
        d = _ID.Draw(sheet)
        for r, (name, frames) in enumerate(rows):
            y = r * (self.size + pad)
            d.text((4, y + 3), f"{name}  ({len(frames)} frames)", fill=(0, 0, 0))
            for c, f in enumerate(frames):
                rgb = Image.new("RGB", f.size, (240, 240, 244))
                rgb.paste(f.convert("RGB"), (0, 0))
                sheet.paste(rgb, (c * self.size, y + pad))
        sheet.save(path)


SPRITES = None            # initialised in main(), after CAT_SIZE is known
# Some packs draw the character facing LEFT. Rendering assumes it faces right
# and mirrors for leftward travel, so a left-facing pack walks backwards
# unless this is flipped (CLI: --flip).
SPRITE_FACES_LEFT = False


def draw_cat(pose: str, phase: float, face_left: bool) -> Image.Image:
    """One animation frame. pose: walk|idle|sit|sleep|jump|swipe|groom."""
    if SPRITE_FACES_LEFT:
        face_left = not face_left      # art already faces left; invert the mirror
    return SPRITES.frame(pose, phase, face_left)


class SpriteCache:
    """Caches PhotoImages per (pose, frame_index, facing).

    Quantises on each animation's *real* frame count rather than a fixed
    number, so a pack shipping an 8-frame walk gets all 8 frames instead of
    being resampled down to some arbitrary grid.
    """

    def __init__(self):
        self._cache: dict[tuple[str, int, bool], ImageTk.PhotoImage] = {}

    def get(self, pose: str, phase: float, face_left: bool) -> ImageTk.PhotoImage:
        n = max(1, SPRITES.frame_count(pose) or 1)
        idx = int(phase * n) % n
        key = (pose, idx, face_left)
        if key not in self._cache:
            pil_img = draw_cat(pose, (idx + 0.5) / n, face_left)
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
        try:
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
        except Exception as exc:  # noqa: BLE001 - a window can vanish mid-enumeration; never crash the pet
            print(f"[list-windows] skipped a window ({exc})")
        return True

    try:
        win32gui.EnumWindows(cb, None)
    except Exception as exc:  # noqa: BLE001
        print(f"[list-windows] EnumWindows failed ({exc})")
    return results


# ---------------------------------------------------------------------------
# Dynamic app index — everything Windows itself considers "installed", with
# fuzzy "did you mean" resolution. No curated/hardcoded app list.
# ---------------------------------------------------------------------------

def _normalize_app_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


class AppIndex:
    """Every app Windows' own Start menu knows about, resolved by name.

    Built from `Get-StartApps` (a built-in PowerShell cmdlet — it's the same
    list backing Start menu search, so it covers classic desktop apps *and*
    Store/UWP apps uniformly, each with an AppID that `explorer.exe
    shell:AppsFolder\\<AppID>` launches regardless of app type). Falls back
    to the registry's App Paths key if PowerShell is ever unavailable.

    Built once in a background thread at startup (Get-StartApps takes on
    the order of ~0.5s) so `resolve()` on the UI thread is just a dict
    lookup, never a blocking subprocess call.
    """

    REFRESH_SECONDS = 600

    def __init__(self):
        self._apps: dict[str, dict] = {}  # normalized name -> {name, kind, value}
        self._lock = threading.Lock()
        self.ready = threading.Event()

    def start(self):
        threading.Thread(target=self._build_loop, daemon=True).start()

    def _build_loop(self):
        while True:
            self.refresh_now()
            time.sleep(self.REFRESH_SECONDS)

    def refresh_now(self):
        apps: dict[str, dict] = {}
        try:
            apps.update(self._from_powershell())
        except Exception as exc:  # noqa: BLE001
            print(f"[app-index] Get-StartApps unavailable ({exc}); falling back to registry App Paths.")
        if not apps:
            try:
                apps.update(self._from_app_paths())
            except Exception as exc:  # noqa: BLE001
                print(f"[app-index] registry fallback also failed ({exc}).")
        if apps:
            with self._lock:
                self._apps = apps
            self.ready.set()
            print(f"[app-index] indexed {len(apps)} installed apps.")
        else:
            print("[app-index] found no apps at all — open-by-name will report 'not found' until this recovers.")

    def _from_powershell(self) -> dict[str, dict]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, creationflags=creationflags,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError(proc.stderr.strip() or "empty output from Get-StartApps")
        data = json.loads(proc.stdout)
        if isinstance(data, dict):
            data = [data]
        out = {}
        for item in data:
            name = (item.get("Name") or "").strip()
            app_id = (item.get("AppID") or "").strip()
            if not name or not app_id:
                continue
            out[_normalize_app_name(name)] = {"name": name, "kind": "startapp", "value": app_id}
        return out

    def _from_app_paths(self) -> dict[str, dict]:
        import winreg
        out = {}
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths")
            except OSError:
                continue
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        path, _ = winreg.QueryValueEx(sk, None)
                except OSError:
                    continue
                name = sub[:-4] if sub.lower().endswith(".exe") else sub
                out[_normalize_app_name(name)] = {"name": name, "kind": "path", "value": path}
        return out

    def resolve(self, query: str):
        """Resolve a typed app name.

        Returns ("exact", entry) | ("fuzzy", entry) | ("none", [suggestion names]).
        "fuzzy" means a plausible typo match ("chrom" -> "Chrome") — the
        caller should confirm with the user before launching it.
        """
        query = query.strip()
        # A literal path or existing file bypasses the index entirely.
        if os.path.isabs(query) and os.path.isfile(query):
            return "exact", {"name": os.path.basename(query), "kind": "path", "value": query}

        self.ready.wait(timeout=5)
        with self._lock:
            apps = dict(self._apps)
        if not apps:
            return "none", []

        nq = _normalize_app_name(query)
        if nq in apps:
            return "exact", apps[nq]

        substr_hits = [v for k, v in apps.items() if nq in k or k in nq]
        if substr_hits:
            substr_hits.sort(key=lambda v: len(v["name"]))  # shortest/most-specific name wins
            return "exact", substr_hits[0]

        # Fuzzy against full names ("chrom" ~ "google chrome") — but a typo'd
        # single word compared against a multi-word name scores badly on
        # length alone ("chrme" vs "google chrome"), so also fuzzy-match
        # against each individual word ("chrme" ~ "chrome").
        word_map: dict[str, dict] = {}
        for k, v in apps.items():
            for word in k.split():
                if word not in word_map or len(v["name"]) < len(word_map[word]["name"]):
                    word_map[word] = v

        close = difflib.get_close_matches(nq, apps.keys(), n=1, cutoff=0.6)
        if close:
            return "fuzzy", apps[close[0]]
        close_words = difflib.get_close_matches(nq, word_map.keys(), n=1, cutoff=0.6)
        if close_words:
            return "fuzzy", word_map[close_words[0]]

        weak = difflib.get_close_matches(nq, list(apps.keys()) + list(word_map.keys()), n=3, cutoff=0.3)
        seen, suggestions = set(), []
        for k in weak:
            entry = apps.get(k) or word_map.get(k)
            if entry and entry["name"] not in seen:
                seen.add(entry["name"])
                suggestions.append(entry["name"])
        return "none", suggestions

    @staticmethod
    def launch(entry: dict):
        if entry["kind"] == "startapp":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{entry['value']}"])
        else:
            path = entry["value"]
            if os.path.isabs(path):
                subprocess.Popen([path])
            else:
                subprocess.Popen(path, shell=True)


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
    if t in ("log", "standup", "daily log", "/standup", "/log", "summary"):
        return {"action": "digest", "target": ""}
    if t in ("projects", "repos", "/projects", "git"):
        return {"action": "projects", "target": ""}
    if t in ("tasks", "todos", "todo list", "/todo", "/tasks"):
        return {"action": "todos", "target": ""}
    if t in ("settings", "/settings", "preferences"):
        return {"action": "settings", "target": ""}
    if t in ("what's left", "whats left", "progress", "status"):
        return {"action": "todo_status", "target": ""}
    if t.startswith(("todo ", "/todo ", "task ", "add task ", "remind me to ")):
        body = re.sub(r"^(/?todo|task|add task|remind me to)\s+", "", text.strip(),
                      flags=re.I)
        return {"action": "add_todo", "target": body}
    if t.startswith(("done ", "/done ", "finished ", "completed ")):
        body = re.sub(r"^(/?done|finished|completed)\s+", "", text.strip(), flags=re.I)
        return {"action": "done_todo", "target": body}
    if t in ("today", "stats", "report", "/today", "dashboard"):
        return {"action": "stats", "target": ""}
    if t in ("water", "/water", "drink"):
        return {"action": "water", "target": ""}
    if t in ("resume", "/resume"):
        return {"action": "resume_tracking", "target": ""}
    m = re.match(r"^/?focus\s*(\d+)?", t)
    if m and t.startswith(("focus", "/focus")):
        return {"action": "focus", "target": m.group(1) or "25"}
    m = re.match(r"^/?pause\s*(\d+)?\s*(m|min|h)?", t)
    if m and t.startswith(("pause", "/pause")):
        n = float(m.group(1) or 30)
        secs = n * 3600 if (m.group(2) or "").startswith("h") else n * 60
        return {"action": "pause_tracking", "target": str(secs)}
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
    target_x: float | None = None     # cats walk along the floor, so only x is a destination
    speed: float = 0.0                # current px/s, eased toward the target speed
    pause_until: float = 0.0
    idle_until: float = 0.0
    dragging: bool = False
    idle_walk_cycles: int = 0
    # one-shot action overlay (jump/swipe), preempts normal pose/movement
    action: str | None = None
    action_phase: float = 0.0
    action_duration: float = 0.9
    # smoothed gaze direction, -1..1 on each axis
    look_x: float = 0.0
    look_y: float = 0.0


class PawmatePrototype:
    def __init__(self):
        global SPRITES
        if SPRITES is None:          # constructing directly (tests/imports) must still work
            SPRITES = BlobSource(CAT_SIZE)
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
        self.state.y = self._floor_y()          # cats belong on a surface, not mid-screen
        self.root.geometry(f"{CAT_SIZE}x{CAT_SIZE}+{int(self.state.x)}+{int(self.state.y)}")
        self.desktop_icons: list[tuple[int, int]] = []
        self.image_id = self.canvas.create_image(CAT_SIZE // 2, CAT_SIZE // 2, image=None)

        self.app_index = AppIndex()
        self.app_index.start()

        # eyes are canvas items layered over the body image so they can follow
        # the cursor continuously (see blob_eye_layout)
        self.eye_items: list[int] = []

        self.store = Store()
        self.settings = Settings(self.store)
        self.classifier = AdaptiveClassifier(self.store)
        self.todos = Todos(self.store)
        self.git = GitWatcher(self.store)
        self.git.start()
        self.tracker = ActivityTracker(self.store)
        self.tracker.classifier = self.classifier   # learned categories, not fixed rules
        self.tracker.git = self.git                 # repo/branch attribution
        self.tracker.settings = self.settings
        self.tracker.context = self                 # for focus/todo learning signals
        self.tracker.start()
        self._bubble = None
        self._overlay = None
        self._last_ontask_check = time.time()

        self.breaks = BreakScheduler(self.settings, idle_seconds, self._fire_break)
        self.root.after(5000, self._check_reminders)

        # global hotkey for the unified chat box
        self.hotkeys = hotkeys.HotkeyListener(self.settings.get("hotkey") or "ctrl+grave")
        if self.hotkeys.start():
            print(f"[hotkey] {hotkeys.describe(self.hotkeys.spec)} opens the chat box")
        elif self.hotkeys.error:
            print(f"[hotkey] unavailable: {self.hotkeys.error} "
                  f"(double-click the pet instead)")
        self.root.after(120, self._poll_hotkey)

        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Double-Button-1>", lambda e: self._open_chat())

        # paint the very first frame immediately instead of waiting for the
        # first _tick() ~33ms later, so there's never a blank-canvas window
        initial = self.sprites.get(self.state.pose, 0.0, self.state.facing_left)
        self.canvas.itemconfig(self.image_id, image=initial)
        self._current_image_ref = initial

        self.root.after(200, self._apply_win32_styles)
        self.root.after(500, self._refresh_desktop_icons)
        self._pick_new_target()
        self._tick_last = time.time()
        self.root.after(TICK_MS, self._tick)

        self._drag_offset = (0, 0)

    def _resolve_toplevel_hwnd(self) -> int:
        """Walk up the HWND parent chain to the real top-level window.

        Tk's *root* window on Windows has a long-documented quirk where
        winfo_id() doesn't always return the actual top-level HWND (unlike a
        Toplevel). Walking GetParent() up until it hits 0 finds the true
        top-level regardless of which Tk build/version we're running under.
        """
        hwnd = self.root.winfo_id()
        seen = set()
        while True:
            parent = win32gui.GetParent(hwnd)
            if not parent or parent in seen:
                return hwnd
            seen.add(parent)
            hwnd = parent

    # -- window styling (no-focus-steal, always-on-top, click-through via colorkey) --
    def _apply_win32_styles(self):
        if not HAVE_WIN32:
            return
        try:
            hwnd = self._resolve_toplevel_hwnd()
            exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            exstyle |= win32con.WS_EX_LAYERED | win32con.WS_EX_TOOLWINDOW | win32con.WS_EX_NOACTIVATE
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)

            # Changing GWL_EXSTYLE on an already-layered window can silently
            # drop the colorkey Tk set up for -transparentcolor, which makes
            # the whole window paint nothing (not "opaque" — invisible).
            # Re-assert it explicitly so that can never happen.
            r, g, b = 255, 0, 255  # TRANSPARENT_KEY, #ff00ff
            colorref = win32api.RGB(r, g, b)
            win32gui.SetLayeredWindowAttributes(hwnd, colorref, 0, win32con.LWA_COLORKEY)

            # Force Windows to recompute the frame/visuals after an exstyle change.
            win32gui.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_FRAMECHANGED,
            )

            rect = win32gui.GetWindowRect(hwnd)
            print(f"[win32-style] applied to hwnd={hwnd:#x} class={win32gui.GetClassName(hwnd)!r} "
                  f"rect={rect} exstyle={exstyle:#x}")
        except Exception as exc:  # noqa: BLE001
            print(f"[win32-style] FAILED — window will likely stay click-through-only "
                  f"with no OS-level no-activate/toolwindow behavior: {exc}")

    def _refresh_desktop_icons(self):
        self.desktop_icons = get_desktop_icon_positions()
        if self.desktop_icons:
            print(f"[desktop-icons] found {len(self.desktop_icons)} icons to visit.")
        else:
            print("[desktop-icons] none found — cat will wander randomly instead.")
        # refresh occasionally in case the user rearranges icons
        self.root.after(60_000, self._refresh_desktop_icons)

    # -- the floor --
    def _floor_y(self) -> float:
        """Top edge of the window when the cat is standing on the floor.

        Uses the *work area* (SPI_GETWORKAREA), so the cat stands on top of
        the taskbar rather than behind it.
        """
        screen_h = self.root.winfo_screenheight()
        bottom = screen_h
        if HAVE_WIN32:
            try:
                rect = wt.RECT()
                if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                    bottom = rect.bottom
            except Exception:  # noqa: BLE001
                pass
        return float(bottom - CAT_SIZE)

    # -- movement targets --
    def _pick_new_target(self):
        """Pick somewhere to walk *along the floor*.

        Deliberately horizontal-only: a cat walks on a surface. Drifting
        diagonally across the middle of the desktop is screensaver
        behaviour, and it reads as annoying rather than alive.
        """
        screen_w = self.root.winfo_screenwidth()
        margin = 8
        span = max(120.0, screen_w * 0.35)          # a purposeful stroll, not a twitch
        here = self.state.x
        low, high = margin, screen_w - CAT_SIZE - margin
        candidates = [here - span, here + span]
        random.shuffle(candidates)
        tx = next((c for c in candidates if low <= c <= high), None)
        if tx is None:
            tx = min(max(here + random.uniform(-span, span), low), high)
        self.state.target_x = tx
        self.state.y = self._floor_y()
        self.state.pose = "walk"

    # -- drag handling --
    def _on_drag_start(self, event):
        self.state.dragging = True
        self._drag_offset = (event.x, event.y)
        self.state.pose = "idle"
        self.state.target_x = None
        self.state.speed = 0.0

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
    #
    # Deliberately NOT a static list of apps to launch — that's just a worse
    # Start menu. Opening an app goes through chat/the resolver below, which
    # knows about every app Windows itself knows about and asks "did you
    # mean X?" on a typo instead of failing. Close App stays as a live menu
    # here because it shows *running* windows, which Start Menu can't — that
    # is genuinely different information, not a duplicate control.
    def _on_right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Pawmate", state="disabled")
        menu.add_separator()
        menu.add_command(label="Chat / command…", command=self._open_chat)
        menu.add_command(label="Open app…", command=self._prompt_open_app)
        menu.add_separator()
        menu.add_command(label="Today", command=self._show_dashboard)
        menu.add_command(label="Daily log", command=self._show_digest)
        menu.add_command(label="Projects", command=self._show_projects)
        menu.add_command(label="Tasks", command=self._show_todos)
        menu.add_separator()
        menu.add_command(label="Log a glass of water", command=self._log_water)
        menu.add_command(label="Re-categorise what I'm using", command=self._retag_current)

        focus_menu = tk.Menu(menu, tearoff=0)
        for m in (15, 25, 50):
            focus_menu.add_command(label=f"{m} minutes", command=lambda mm=m: self._start_focus(mm))
        menu.add_cascade(label="Focus session", menu=focus_menu)

        track_menu = tk.Menu(menu, tearoff=0)
        if self.tracker.paused:
            track_menu.add_command(label="Resume tracking", command=self._resume_tracking)
        else:
            for label, secs in (("Pause 15 min", 900), ("Pause 1 hour", 3600),
                                ("Pause until tomorrow", 12 * 3600)):
                track_menu.add_command(label=label, command=lambda sx=secs: self._pause_tracking(sx))
        menu.add_cascade(label="Tracking", menu=track_menu)
        menu.add_separator()

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
        menu.add_command(label="Settings", command=self._show_settings)
        menu.add_command(label="Quit", command=self._quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _pause_tracking(self, seconds: float):
        self.tracker.pause(seconds)
        ui.toast(self.root, f"tracking paused for {int(seconds // 60)} min")

    def _resume_tracking(self):
        self.tracker.resume()
        ui.toast(self.root, "tracking resumed")

    def _set_pose(self, pose: str):
        self.state.pose = pose
        self.state.target_x = None
        self.state.speed = 0.0
        self.state.idle_until = time.time() + 3600 if pose in ("sit", "sleep") else 0
        if pose == "walk":
            self._pick_new_target()

    # -- app open (dynamic index + fuzzy "did you mean") --
    def _prompt_open_app(self):
        ui.CommandBar(self.root, self._open_app_query,
                      hint="Type any installed app name. Typos are fine — "
                           "it'll ask if you meant something close.",
                      suggestions=("notepad", "chrome", "calculator", "spotify"))

    def _open_app_query(self, query: str):
        query = query.strip()
        if not query:
            return
        kind, payload = self.app_index.resolve(query)
        if kind == "exact":
            self._launch_resolved(payload)
        elif kind == "fuzzy":
            name = payload["name"]
            ui.confirm(self.root, f'No app called "{query}".\nDid you mean "{name}"?',
                       on_yes=lambda: self._launch_resolved(payload), yes="Open it")
        else:  # "none"
            suggestions = payload
            msg = f'No app found matching "{query}".'
            if suggestions:
                msg += "  Closest: " + ", ".join(suggestions)
            elif not self.app_index.ready.is_set():
                msg += "  (still indexing installed apps — try again in a moment)"
            ui.toast(self.root, msg, ok=False)

    def _launch_resolved(self, entry: dict):
        try:
            self.app_index.launch(entry)
            self._play_action("jump", duration=0.7)  # long enough for the arc to read as an arc
        except Exception as exc:  # noqa: BLE001
            ui.toast(self.root, f'Couldn\'t open "{entry["name"]}": {exc}', ok=False)

    # -- app close (live running windows, confirm, fuzzy match) --
    def _close_app_confirm(self, hwnd: int, title: str):
        # Destructive action = human click, per the plan's principle.
        def do_close():
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                self._play_action("swipe", duration=0.45)
                ui.toast(self.root, f'closed "{title[:40]}"')
            except Exception as exc:  # noqa: BLE001
                ui.toast(self.root, f"Couldn't close that window: {exc}", ok=False)
        ui.confirm(self.root, f'Close "{title}"?', on_yes=do_close, yes="Close it")

    def _close_app_by_title_substring(self, substring: str):
        substring = substring.strip()
        if not substring:
            return
        open_windows = list_visible_windows()
        lower = substring.lower()
        exact = [(h, t) for h, t in open_windows if lower in t.lower()]
        if exact:
            self._close_app_confirm(*exact[0])
            return
        # no substring hit — offer the closest open window title as a "did you mean"
        titles = {t: h for h, t in open_windows}
        close = difflib.get_close_matches(substring, titles.keys(), n=1, cutoff=0.5)
        if close:
            title = close[0]
            ui.confirm(self.root, f'No window matching "{substring}".\nDid you mean "{title}"?',
                       on_yes=lambda: self._close_app_confirm(titles[title], title),
                       yes="That one")
            return
        ui.toast(self.root, f'No open window matching "{substring}".', ok=False)

    # -- gaze: eyes follow the cursor --
    def _update_gaze(self, dt: float, pose: str, phase: float):
        st = self.state
        try:
            px, py = self.root.winfo_pointerxy()
        except Exception:  # noqa: BLE001
            return
        cx = st.x + CAT_SIZE / 2
        cy = st.y + CAT_SIZE / 2
        dx, dy = px - cx, py - cy
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            tx = ty = 0.0
        else:
            # saturate: past ~340px away the gaze is already fully deflected,
            # so distant cursor movement doesn't keep yanking the eyes
            reach = min(1.0, dist / 340.0)
            tx, ty = (dx / dist) * reach, (dy / dist) * reach
        k = min(1.0, dt * GAZE_FOLLOW_HZ)          # critically-damped-ish smoothing
        st.look_x += (tx - st.look_x) * k
        st.look_y += (ty - st.look_y) * k
        self._draw_eyes(pose, phase)

    def _draw_eyes(self, pose: str, phase: float):
        for i in self.eye_items:
            self.canvas.delete(i)
        self.eye_items = []
        if not isinstance(SPRITES, BlobSource):
            return                                   # sprite packs draw their own eyes
        lay = blob_eye_layout(pose, phase, CAT_SIZE)
        st = self.state
        cx, cy = lay["cx"], lay["cy"]
        sp = lay["spacing"]
        lx = st.look_x * lay["travel_x"]
        ly = st.look_y * lay["travel_y"]
        for sx in (-1, 1):
            ex = cx + sx * sp
            if lay["blink"] or pose == "sleep":
                self.eye_items.append(self.canvas.create_line(
                    ex - lay["white_rx"], cy, ex + lay["white_rx"], cy,
                    fill=BLOB_EYE_HEX, width=2, capstyle="round"))
                continue
            self.eye_items.append(self.canvas.create_oval(
                ex - lay["white_rx"], cy - lay["white_ry"],
                ex + lay["white_rx"], cy + lay["white_ry"],
                fill=BLOB_WHITE_HEX, outline=""))
            self.eye_items.append(self.canvas.create_oval(
                ex + lx - lay["pupil_rx"], cy + ly - lay["pupil_ry"],
                ex + lx + lay["pupil_rx"], cy + ly + lay["pupil_ry"],
                fill=BLOB_EYE_HEX, outline=""))
            self.eye_items.append(self.canvas.create_oval(
                ex + lx - lay["pupil_rx"] * 0.9, cy + ly - lay["pupil_ry"] * 0.95,
                ex + lx - lay["pupil_rx"] * 0.2, cy + ly - lay["pupil_ry"] * 0.3,
                fill=BLOB_WHITE_HEX, outline=""))

    # -- speech bubbles + wellbeing nudges --
    def say(self, text: str, actions=None, timeout_ms: int = 6000):
        try:
            if self._bubble and self._bubble.winfo_exists():
                self._bubble.destroy()
        except Exception:  # noqa: BLE001
            pass
        self._bubble = ui.Bubble(self.root, text,
                                 int(self.state.x + CAT_SIZE / 2), int(self.state.y),
                                 actions=actions, timeout_ms=timeout_ms)

    def _poll_hotkey(self):
        self.root.after(120, self._poll_hotkey)
        if self.hotkeys.poll():
            self._open_chat()

    def _check_reminders(self):
        """Breaks + on-task nudges, both paced off real activity."""
        self.root.after(15_000, self._check_reminders)
        if self.tracker.paused or self._overlay is not None:
            return
        kind = self.breaks.due()
        if kind:
            self.breaks.fire(kind)
            return
        self._check_on_task()

    def _fire_break(self, kind: str):
        """Show the break. Blocking overlays hold the screen; Esc always skips."""
        blocking = (self.settings.get("water_blocking") if kind == "water"
                    else self.settings.get("blocking_breaks"))
        if kind == "water":
            if not blocking:
                self.say("time for some water",
                         actions=[("Done", self._log_water), ("Later", lambda: None)],
                         timeout_ms=15000)
                return
            self._show_overlay("water", 90, on_done=self._log_water)
        elif kind == "eye":
            secs = int(self.settings.get("eye_duration_s") or 20)
            if not blocking:
                self.say(f"look away for {secs}s — 20-20-20", timeout_ms=8000)
                return
            self._show_overlay("eye", secs)
        else:
            if not blocking:
                self.say("you've been at it a while.\nstretch for a bit?", timeout_ms=12000)
                return
            self._show_overlay("break", 60)

    def _show_overlay(self, kind: str, seconds: int, on_done=None):
        if self._overlay is not None:
            return

        def finish(skipped: bool):
            self._overlay = None
            self.store.log_event(f"break_{kind}", "skipped" if skipped else "done")
            if not skipped and on_done:
                on_done()
            if skipped:
                # don't re-nag instantly after an intentional skip
                self.breaks.suspend(5 * 60)

        self._overlay = BreakOverlay(
            self.root, kind, seconds,
            on_done=lambda: finish(False), on_skip=lambda: finish(True),
            confirm_key="w")

    # -- on-task detection (uses the active todo) --
    def _check_on_task(self):
        """If a task is marked active, credit time to it and nudge when the
        foreground is something distracting instead."""
        todo = self.todos.active()
        if not todo:
            return
        now = time.time()
        dt = now - self._last_ontask_check
        self._last_ontask_check = now
        if dt <= 0 or dt > 300:
            return
        exe, title = foreground_app()
        cat = self.classifier.categorise(exe, title)
        if idle_seconds() > 120:
            return
        self.todos.add_spent(todo["id"], dt)
        if cat == "distracting":
            self._offtask_s = getattr(self, "_offtask_s", 0.0) + dt
            if self._offtask_s >= 120:
                self._offtask_s = 0.0
                self.say(f"still on “{todo['text'][:28]}”?",
                         actions=[("Yes", lambda: None),
                                  ("Switch", self._show_todos)], timeout_ms=12000)
        else:
            self._offtask_s = 0.0

    def _show_todos(self):
        ui.TodoPanel(self.root, self.todos)

    def _show_settings(self):
        ui.SettingsPanel(self.root, self.settings, on_save=self._settings_saved,
                         classifier=self.classifier)

    def _settings_saved(self):
        self.tracker.reload_settings()
        ui.toast(self.root, "settings saved")

    def _retag_current(self):
        """Correct the category of whatever is focused, and learn from it."""
        exe, title = foreground_app()
        if not exe and not title:
            ui.toast(self.root, "nothing focused to re-categorise", ok=False)
            return
        verdict = self.classifier.classify(exe, title)

        def apply(scope: str, category: str):
            scope = (scope or "").strip().lower()
            if not scope:
                return
            self.classifier.teach(exe, title, category, explicit=True,
                                  focus_tokens=[scope])
            ui.toast(self.root, f'"{scope.replace("exe:", "")}" is {category} from now on')
        ui.RetagDialog(self.root, exe, title, verdict, apply)

    # -- reporting --
    def _log_water(self):
        self.store.log_water()
        self.breaks.reset("water")
        self._play_action("jump", duration=0.7)
        goal = int(self.settings.get("water_goal") or 8)
        n = self.store.water_count()
        ui.toast(self.root, f"logged. {n} of {goal} glasses today")

    def _show_dashboard(self):
        ui.Dashboard(self.root, self.tracker.live_stats(),
                     segments=self.store.segments(),
                     day_start=self.store.day_bounds()[0],
                     on_water=self._log_water, on_digest=self._show_digest)

    def _build_digest(self, day_offset: int = 0):
        stats = self.tracker.live_stats(day_offset)
        segs = self.store.segments(day_offset)
        a, b = self.store.day_bounds(day_offset)
        repos = self.git.day_report(a, b)
        rows = self.todos.list()
        return digest.build(stats, segs, repos,
                            [r for r in rows if r["done"]],
                            [r for r in rows if not r["done"]], a)

    def _show_digest(self):
        d = self._build_digest()
        ui.DigestPanel(self.root, d, digest.render_standup(d),
                       digest.render_detail(d), digest.insights(d))

    def _show_projects(self):
        a, b = self.store.day_bounds()
        report = self.git.day_report(a, b)
        note = ("git not found on PATH" if not self.git.available
                else f"{len(self.git.repos)} repositories watched")
        ui.ProjectsPanel(self.root, report, note)

    # -- one-shot action animation (jump on open, swipe on close) --
    def _play_action(self, name: str, duration: float = 0.9):
        st = self.state
        st.action = name
        st.action_phase = 0.0
        st.action_duration = duration

    # -- chat / command bar --
    def _open_chat(self):
        ai = bool(OPENROUTER_API_KEY or (CF_ACCOUNT_ID and CF_API_TOKEN))
        hint = ("Free text works too — AI is connected." if ai else
                "Set OPENROUTER_API_KEY to also accept free text via AI.")
        if getattr(self, "hotkeys", None) and self.hotkeys.ok:
            hint = f"{hotkeys.describe(self.hotkeys.spec)} opens this from anywhere.  " + hint
        ui.CommandBar(self.root, self._handle_command, hint=hint,
                      suggestions=("standup", "today", "todo ", "projects", "focus 25"))

    def _handle_command(self, text: str):
        action = parse_local_command(text)
        if action is None:
            action = ask_llm(text)
        if action is None:
            ui.toast(self.root, "Didn't catch that — try 'open notepad' or 'today'.", ok=False)
            return
        self._dispatch_action(action)

    def _dispatch_action(self, action: dict):
        kind = action.get("action")
        target = str(action.get("target", "")).strip()
        if kind == "pose" and target in ("walk", "sit", "sleep", "idle"):
            self._set_pose(target)
        elif kind == "open_app":
            self._open_app_query(target)
        elif kind == "close_app":
            self._close_app_by_title_substring(target)
        elif kind == "stats":
            self._show_dashboard()
        elif kind == "todos":
            self._show_todos()
        elif kind == "digest":
            self._show_digest()
        elif kind == "projects":
            self._show_projects()
        elif kind == "settings":
            self._show_settings()
        elif kind == "add_todo":
            self.todos.add(target)
            ui.toast(self.root, f'added: {target}')
        elif kind == "done_todo":
            row = self.todos.find(target)
            if row:
                self.todos.set_done(row["id"], True)
                done, total = self.todos.progress()
                self._play_action("jump", duration=0.7)
                ui.toast(self.root, f'done: {row["text"]}  ({done}/{total})')
            else:
                ui.toast(self.root, f'no task matching "{target}"', ok=False)
        elif kind == "todo_status":
            done, total = self.todos.progress()
            rows = [r for r in self.todos.list() if not r["done"]]
            if not total:
                self.say("nothing planned yet.\nwant to add something?",
                         actions=[("Open tasks", self._show_todos)], timeout_ms=10000)
            else:
                nxt = rows[0]["text"][:30] if rows else "all done!"
                self.say(f"{done}/{total} done\nnext: {nxt}",
                         actions=[("Open", self._show_todos)], timeout_ms=10000)
        elif kind == "water":
            self._log_water()
        elif kind == "focus":
            self._start_focus(int(target or 25))
        elif kind == "pause_tracking":
            self.tracker.pause(float(target or 1800))
            ui.toast(self.root, f"tracking paused for {int(float(target or 1800) // 60)} min")
        elif kind == "resume_tracking":
            self.tracker.resume()
            ui.toast(self.root, "tracking resumed")
        elif kind == "say":
            self.say(target or "…")
        else:
            ui.toast(self.root, f"Not sure how to do that: {action}", ok=False)

    # -- focus sessions (plan W3) --
    def _start_focus(self, minutes: int):
        minutes = max(1, min(180, minutes))
        self.focus_until = time.time() + minutes * 60
        self._set_pose("sit")
        self.say(f"focus session, {minutes} min.\nsitting guard.", timeout_ms=5000)
        self.root.after(minutes * 60 * 1000, self._end_focus)

    def _end_focus(self):
        if not getattr(self, "focus_until", 0):
            return
        self.focus_until = 0
        stats = self.tracker.live_stats()
        self._play_action("jump", duration=0.7)
        self.say(f"focus done. {fmt_minutes(stats['deep_work_min'])} deep work today.",
                 actions=[("See today", self._show_dashboard)], timeout_ms=12000)

    # -- main loop --
    def _tick(self):
        now = time.time()
        dt = now - self._tick_last
        self._tick_last = now
        st = self.state

        if st.action:
            # A one-shot action (jump/swipe) preempts normal movement/pose
            # entirely until it finishes. Swapping sprite frames alone has
            # no sense of "up" — a jump needs the window to actually move,
            # or it's just two pictures flickering in place. So: real
            # vertical motion here, following a parabolic arc, exactly the
            # way normal walking already moves the window horizontally.
            st.action_phase += dt / st.action_duration
            if st.action_phase >= 1.0:
                st.action = None
                st.pose = "idle"
                st.idle_until = now + random.uniform(IDLE_MIN_S, IDLE_MAX_S)
                self.root.geometry(f"+{int(st.x)}+{int(st.y)}")  # land exactly back on the ground line
            else:
                y_offset = 0.0
                if st.action == "jump":
                    y_offset = math.sin(st.action_phase * math.pi) * JUMP_HEIGHT_PX
                self.root.geometry(f"+{int(st.x)}+{int(st.y - y_offset)}")
                img = self.sprites.get(st.action, st.action_phase, st.facing_left)
                self.canvas.itemconfig(self.image_id, image=img)
                self._current_image_ref = img
                self._update_gaze(dt, st.action, st.action_phase)
                self.root.after(TICK_MS, self._tick)
                return

        if not st.dragging:
            moved = 0.0
            if st.pose == "walk" and st.target_x is not None:
                dx = st.target_x - st.x
                dist = abs(dx)
                if dist < 1.5 and st.speed < 6.0:
                    st.pose = "idle"
                    st.speed = 0.0
                    st.x = st.target_x
                    st.idle_until = now + random.uniform(PAUSE_AT_TARGET_MIN_S, PAUSE_AT_TARGET_MAX_S)
                    st.idle_walk_cycles += 1
                    st.target_x = None
                else:
                    # Ease in and decelerate into the destination instead of
                    # snapping between 0 and full speed — a cat doesn't start
                    # and stop like a vehicle hitting a wall.
                    braking = WALK_SPEED_PX_S * min(1.0, dist / WALK_EASE_PX)
                    want = min(WALK_SPEED_PX_S, max(WALK_MIN_SPEED_PX_S, braking))
                    st.speed += max(-WALK_ACCEL_PX_S2 * dt,
                                    min(WALK_ACCEL_PX_S2 * dt, want - st.speed))
                    step = min(st.speed * dt, dist)
                    st.x += math.copysign(step, dx)
                    st.facing_left = dx < 0
                    moved = step
            elif st.pose == "idle" and st.target_x is None and now >= st.idle_until:
                if st.idle_walk_cycles >= SLEEP_AFTER_IDLE_CYCLES:
                    st.pose = random.choice(("sleep", "sit", "sit"))
                    st.idle_until = now + (random.uniform(18, 40) if st.pose == "sleep"
                                           else random.uniform(6, 14))
                    st.idle_walk_cycles = 0
                else:
                    self._pick_new_target()
            elif st.pose in ("sleep", "sit") and now >= st.idle_until:
                st.pose = "idle"
                st.idle_until = now + random.uniform(IDLE_MIN_S, IDLE_MAX_S)

            # Advance the walk cycle by DISTANCE TRAVELLED, not by wall time.
            # This is the fix for "ice skating": however fast the cat happens
            # to be moving (including while easing in and out), the paws
            # advance exactly one stride per STRIDE_PX of ground covered, so
            # feet and ground can never disagree.
            if st.pose == "walk":
                st.phase = (st.phase + moved / STRIDE_PX) % 1.0
                # One bounce per footfall (two per stride cycle), driven by the
                # same distance-synced phase as the legs, so the bounce lands
                # with the steps instead of drifting against them.
                bob = (0.0 if getattr(SPRITES, "handles_own_bob", False)
                       else abs(math.sin(st.phase * 2 * math.pi)) * WALK_BOB_PX)
            else:
                st.phase = (st.phase + dt * IDLE_CYCLE_HZ) % 1.0
                bob = math.sin(st.phase * 2 * math.pi) * IDLE_BOB_PX if st.pose in ("idle", "sit") else 0.0
            self.root.geometry(f"+{int(st.x)}+{int(st.y - bob)}")

        img = self.sprites.get(st.pose, st.phase, st.facing_left)
        self.canvas.itemconfig(self.image_id, image=img)
        self._current_image_ref = img  # keep a reference so Tk doesn't GC it
        self._update_gaze(dt, st.pose, st.phase)

        self.root.after(TICK_MS, self._tick)

    def _quit(self):
        try:
            self.tracker.stop()          # flush the in-flight segment before exiting
        except Exception:  # noqa: BLE001
            pass
        try:
            self.hotkeys.stop()
        except Exception:  # noqa: BLE001
            pass
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()


def main():
    global SPRITES, SPRITE_FACES_LEFT
    if "--flip" in sys.argv:
        SPRITE_FACES_LEFT = not SPRITE_FACES_LEFT
    # The procedural blob is the default: it always works, needs no download,
    # and is smooth at any sampling rate. --sprites opts into a pack instead.
    if "--sprites" in sys.argv:
        SPRITES = SpriteSource(CAT_SIZE)
    else:
        SPRITES = BlobSource(CAT_SIZE)
    print(SPRITES.describe())

    if "--inspect" in sys.argv:
        # Verify what got discovered from a dropped-in sprite pack, and dump
        # a labeled contact sheet so the pose mapping can be eyeballed
        # before anything animates.
        print(f"[inspect] pack folder: {_CAT_DIR}")
        if not os.path.isdir(_CAT_DIR):
            print("[inspect] that folder doesn't exist yet. Create it and unzip a sprite pack "
                  "inside (nested folders are fine; a .zip dropped in is auto-extracted).")
        else:
            # Show EVERY candidate group found, not just the ones that matched a
            # known animation name — if a pack uses unusual names, this is what
            # tells us which aliases to add.
            cat = SPRITES.catalog
            if cat:
                print(f"[inspect] {len(cat)} candidate animation groups found in the pack:")
                for (kind, name), paths in sorted(cat.items(), key=lambda kv: -len(kv[1]))[:40]:
                    sample = os.path.relpath(paths[0], _CAT_DIR)
                    print(f"    {kind:4}  {name:<24} {len(paths):>3} file(s)   e.g. {sample}")
            else:
                print("[inspect] no image files found under that folder at all.")

            # Per-file detail: dimensions and internal frame count. This is what
            # distinguishes a static sprite sheet from an animated GIF, and shows
            # which scale variant is which.
            seen: set[str] = set()
            files: list[str] = []
            for paths in cat.values():
                for p in paths:
                    if p not in seen:
                        seen.add(p)
                        files.append(p)
            if files:
                print(f"\n[inspect] {len(files)} image file(s) in detail:")
                for p in sorted(files):
                    try:
                        im = Image.open(p)
                        n = getattr(im, "n_frames", 1)
                        kind = f"{n} frames (animated)" if n > 1 else "still"
                        print(f"    {os.path.relpath(p, _CAT_DIR):<52} "
                              f"{im.size[0]:>5}x{im.size[1]:<5} {kind}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"    {os.path.relpath(p, _CAT_DIR):<52} unreadable ({exc})")
        print()
        print("[inspect] mapped to poses:")
        for pose in ("walk", "idle", "sit", "sleep", "jump", "swipe", "groom"):
            print(f"    {pose:<7} {SPRITES.frame_count(pose):>3} frames")
        out = os.path.join(_ASSET_DIR, "contact_sheet.png")
        SPRITES.contact_sheet(out)
        print(f"\n[inspect] wrote {out} — open it to check nothing got mis-sliced.")
        return

    PawmatePrototype().run()


if __name__ == "__main__":
    main()
