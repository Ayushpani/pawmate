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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CAT_SIZE = 128                 # window / sprite size in px — 4x the sprite sheet's native 32px, for crisp NEAREST upscaling
TRANSPARENT_KEY = "#ff00ff"    # color-keyed as transparent by the OS
FPS = 30
TICK_MS = int(1000 / FPS)
WALK_SPEED_PX_S = 90.0
WALK_CYCLE_HZ = 3.2             # leg-frame swaps/sec while walking — has to be fast relative to
                                 # WALK_SPEED_PX_S or the glide and the "steps" visually disagree
IDLE_CYCLE_HZ = 0.5             # frame swaps/sec at rest (breathing-speed, not a slideshow)
IDLE_BOB_PX = 2.5               # small vertical bob while idle/sit, so rest is never perfectly frozen
JUMP_HEIGHT_PX = 40.0           # how high the window actually rises during the jump action
IDLE_MIN_S, IDLE_MAX_S = 2.0, 5.0
PAUSE_AT_TARGET_MIN_S, PAUSE_AT_TARGET_MAX_S = 1.5, 4.0
SLEEP_AFTER_IDLE_CYCLES = 4     # after this many idle->walk loops with no user interaction, nap

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free")
CF_ACCOUNT_ID = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_API_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.environ.get("CF_MODEL", "@cf/meta/llama-3.1-8b-instruct")

# ---------------------------------------------------------------------------
# Cat sprite — real pixel art, not hand-drawn primitives.
#
# Two from-scratch attempts at drawing a cat out of ellipses/polygons both
# came out looking wrong (a front-facing "chibi blob," then a side-profile
# that still didn't read as a cat at this size) — geometry-by-guesswork
# doesn't converge on "obviously a cat" the way an artist's actual drawing
# does. So: this uses the classic "Neko" desktop-pet sprite instead (see
# assets/CREDIT.md for provenance/license) — small (32x32), hand-drawn,
# instantly recognizable, and it's specifically *designed* for this exact
# job (a screen-roaming desktop cat) since 1989.
#
# The sheet is 8 columns x 4 rows of 32x32 frames. Poses below were picked
# by rendering the whole grid, labeling every cell, and looking at it —
# not by trusting a half-remembered mapping.
# ---------------------------------------------------------------------------

MAGENTA_OPAQUE = (255, 0, 255, 255)
_SPRITE_SHEET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "oneko.gif")
_FRAME_PX = 32

# pose -> sequence of (column, row) cells in the sheet, alternated over phase 0..1
_POSE_FRAMES: dict[str, list[tuple[int, int]]] = {
    "idle": [(2, 2), (1, 1)],
    "sit": [(4, 3), (1, 3)],
    "sleep": [(2, 0), (2, 1)],
    "walk_e": [(4, 2), (5, 2)],
    "walk_n": [(5, 0), (6, 0)],
    "walk_s": [(6, 2), (7, 2)],
    # crouch -> airborne -> landing; paired with real vertical window motion
    # in _tick (swapping sprites alone has no sense of "up" — the window
    # itself has to actually move for a jump to read as a jump)
    "jump": [(2, 3), (7, 3), (3, 2)],
    "swipe": [(1, 2), (0, 0), (1, 0)],   # reach -> swipe -> follow-through, on app close
}

try:
    _sheet = Image.open(_SPRITE_SHEET_PATH).convert("RGBA")
except FileNotFoundError:
    _sheet = None
    print(f"[sprite] missing {_SPRITE_SHEET_PATH!r} — the cat will not render. "
          f"Re-clone/re-pull the repo so prototype/assets/oneko.gif comes along.")

_frame_cache: dict[tuple[int, int], Image.Image] = {}


def _sprite_frame(col: int, row: int) -> Image.Image:
    key = (col, row)
    if key in _frame_cache:
        return _frame_cache[key]
    box = (col * _FRAME_PX, row * _FRAME_PX, (col + 1) * _FRAME_PX, (row + 1) * _FRAME_PX)
    raw = _sheet.crop(box) if _sheet else Image.new("RGBA", (_FRAME_PX, _FRAME_PX), (0, 0, 0, 0))
    # composite onto opaque magenta (colorkey transparency, not alpha — see
    # the module docstring) with NEAREST upscaling to keep pixel art crisp
    # instead of LANCZOS-blurring a 32px sprite into mush.
    canvas = Image.new("RGBA", raw.size, MAGENTA_OPAQUE)
    canvas.paste(raw, (0, 0), raw)
    canvas = canvas.resize((CAT_SIZE, CAT_SIZE), Image.NEAREST)
    _frame_cache[key] = canvas
    return canvas


def draw_cat(pose: str, phase: float, face_left: bool) -> Image.Image:
    """Draw one animation frame of the cat.

    pose: 'idle' | 'sit' | 'sleep' | 'walk_e' | 'walk_n' | 'walk_s' | 'jump' | 'swipe'
    phase: 0..1, selects among that pose's frames (2 frames for a walk
           cycle, or a jump/swipe's position through its one-shot gesture)
    face_left: mirror horizontally — used for walk_e (becomes "walk west")
               and as a general facing choice for the stationary poses
    """
    frames = _POSE_FRAMES.get(pose, _POSE_FRAMES["idle"])
    idx = int(phase * len(frames)) % len(frames)
    img = _sprite_frame(*frames[idx])
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
    walk_dir: str = "walk_e"  # which directional sprite to use while pose == "walk"
    phase: float = 0.0
    target: tuple[float, float] | None = None
    pause_until: float = 0.0
    idle_until: float = 0.0
    dragging: bool = False
    idle_walk_cycles: int = 0
    # one-shot action overlay (jump/swipe), preempts normal pose/movement
    action: str | None = None
    action_phase: float = 0.0
    action_duration: float = 0.9


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

        self.app_index = AppIndex()
        self.app_index.start()

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
    #
    # Deliberately NOT a static list of apps to launch — that's just a worse
    # Start menu. Opening an app goes through chat/the resolver below, which
    # knows about every app Windows itself knows about and asks "did you
    # mean X?" on a typo instead of failing. Close App stays as a live menu
    # here because it shows *running* windows, which Start Menu can't — that
    # is genuinely different information, not a duplicate control.
    def _on_right_click(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="🐈 Pawmate prototype", state="disabled")
        menu.add_separator()
        menu.add_command(label="Chat / command…", command=self._open_chat)
        menu.add_command(label="Open app…", command=self._prompt_open_app)
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

    # -- app open (dynamic index + fuzzy "did you mean") --
    def _prompt_open_app(self):
        query = simpledialog.askstring("Pawmate", "Open which app?", parent=self.root)
        if query:
            self._open_app_query(query)

    def _open_app_query(self, query: str):
        query = query.strip()
        if not query:
            return
        kind, payload = self.app_index.resolve(query)
        if kind == "exact":
            self._launch_resolved(payload)
        elif kind == "fuzzy":
            name = payload["name"]
            if messagebox.askyesno("Pawmate", f'No app called "{query}" — did you mean "{name}"?'):
                self._launch_resolved(payload)
        else:  # "none"
            suggestions = payload
            msg = f'No app found matching "{query}".'
            if suggestions:
                msg += "\nClosest matches: " + ", ".join(suggestions)
            elif not self.app_index.ready.is_set():
                msg += "\n(still indexing installed apps — try again in a moment)"
            messagebox.showinfo("Pawmate", msg)

    def _launch_resolved(self, entry: dict):
        try:
            self.app_index.launch(entry)
            self._play_action("jump", duration=0.7)  # long enough for the arc to read as an arc
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Pawmate", f'Couldn\'t open "{entry["name"]}":\n{exc}')

    # -- app close (live running windows, confirm, fuzzy match) --
    def _close_app_confirm(self, hwnd: int, title: str):
        # Destructive action = human click, per the plan's principle.
        if not messagebox.askyesno("Pawmate — confirm", f'Close "{title}"?'):
            return
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            self._play_action("swipe", duration=0.45)  # a swipe is quick, not a slow flicker
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Pawmate", f"Couldn't close that window:\n{exc}")

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
            if messagebox.askyesno("Pawmate", f'No open window matching "{substring}" — did you mean "{title}"?'):
                self._close_app_confirm(titles[title], title)
            return
        messagebox.showinfo("Pawmate", f'No open window matching "{substring}".')

    # -- one-shot action animation (jump on open, swipe on close) --
    def _play_action(self, name: str, duration: float = 0.9):
        st = self.state
        st.action = name
        st.action_phase = 0.0
        st.action_duration = duration

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
            self._open_app_query(target)
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
                self.root.after(TICK_MS, self._tick)
                return

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
                    # pick the closer-matching directional sprite by dominant axis
                    if abs(dx) >= abs(dy):
                        st.walk_dir = "walk_e"
                        st.facing_left = dx < 0
                    else:
                        st.walk_dir = "walk_s" if dy > 0 else "walk_n"
                        st.facing_left = False
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

            # Walking's leg-frames need to cycle fast relative to how fast
            # the window glides, or the two motions visually disagree and
            # it reads as skating on ice instead of stepping. Idle/sit get
            # a small vertical bob for the same reason at rest: two frames
            # swapped only once every couple of seconds, with the window
            # otherwise perfectly still, looks like a slideshow, not a cat.
            if st.pose == "walk":
                st.phase = (st.phase + dt * WALK_CYCLE_HZ) % 1.0
                bob = 0.0
            else:
                st.phase = (st.phase + dt * IDLE_CYCLE_HZ) % 1.0
                bob = math.sin(st.phase * 2 * math.pi) * IDLE_BOB_PX if st.pose in ("idle", "sit") else 0.0
            self.root.geometry(f"+{int(st.x)}+{int(st.y - bob)}")

        render_pose = st.walk_dir if st.pose == "walk" else st.pose
        img = self.sprites.get(render_pose, st.phase, st.facing_left)
        self.canvas.itemconfig(self.image_id, image=img)
        self._current_image_ref = img  # keep a reference so Tk doesn't GC it

        self.root.after(TICK_MS, self._tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = PawmatePrototype()
    app.run()
