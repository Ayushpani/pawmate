"""Activity tracking + local store — the actual point of the product.

Implements a prototype-scale version of the master plan's T1/T2 (time per
app + window title, idle detection), T3 (categories) and R1 (daily stats +
a deterministic productivity score).

Everything stays on this machine in a local SQLite file. Nothing is sent
anywhere — there is no network code in this module at all.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import sqlite3
import sys
import threading
import time

IS_WIN = sys.platform == "win32"
try:
    import win32gui
    import win32process
    HAVE_WIN32 = True
except ImportError:
    HAVE_WIN32 = False

POLL_SECONDS = 2.0
IDLE_THRESHOLD_S = 120.0        # master plan default: 2 minutes
FLUSH_SECONDS = 20.0
MIN_SEGMENT_S = 1.0             # don't record alt-tab flickers

DEEP_WORK_TARGET_MIN = 180.0
DEEP_WORK_BLOCK_MIN = 25.0

# ---------------------------------------------------------------------------
# Categorisation (T3). Deliberately small and editable — first match wins.
# ---------------------------------------------------------------------------

PRODUCTIVE = "productive"
NEUTRAL = "neutral"
DISTRACTING = "distracting"

_EXE_RULES: list[tuple[str, str]] = [
    (r"code|devenv|idea|pycharm|sublime|vim|cursor|windsurf", PRODUCTIVE),
    (r"windowsterminal|cmd|powershell|wt\.exe|conhost|bash", PRODUCTIVE),
    (r"excel|winword|powerpnt|onenote|notion|obsidian|acrobat", PRODUCTIVE),
    (r"teams|outlook|slack|zoom", NEUTRAL),
    (r"explorer|settings|taskmgr|snippingtool", NEUTRAL),
    (r"steam|epicgames|discord|spotify|vlc", DISTRACTING),
]

_TITLE_RULES: list[tuple[str, str]] = [
    (r"youtube|netflix|prime video|hotstar|instagram|reddit|twitter|\bx\.com\b|tiktok", DISTRACTING),
    (r"tutorial|course|lecture|documentation|docs|stack overflow|github", PRODUCTIVE),
    (r"gmail|mail|calendar", NEUTRAL),
]


def categorise(exe: str, title: str) -> str:
    """Rule order per the plan: title beats exe for mixed-use apps (a browser
    is productive on docs and distracting on YouTube — the exe can't tell)."""
    t = (title or "").lower()
    for pattern, cat in _TITLE_RULES:
        if re.search(pattern, t):
            return cat
    e = (exe or "").lower()
    for pattern, cat in _EXE_RULES:
        if re.search(pattern, e):
            return cat
    return NEUTRAL


# ---------------------------------------------------------------------------
# Windows foreground / idle probes
# ---------------------------------------------------------------------------

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds() -> float:
    if not IS_WIN:
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        return max(0.0, (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0)
    except Exception:  # noqa: BLE001
        return 0.0


def foreground_app() -> tuple[str, str]:
    """(exe_name, window_title) of whatever is focused right now."""
    if not HAVE_WIN32:
        return ("", "")
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ("", "")
        title = win32gui.GetWindowText(hwnd) or ""
        exe = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # LIMITED_INFORMATION
            if h:
                try:
                    buf = ctypes.create_unicode_buffer(512)
                    size = wt.DWORD(512)
                    if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                        exe = os.path.basename(buf.value)
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass
        return (exe, title)
    except Exception:  # noqa: BLE001
        return ("", "")


# ---------------------------------------------------------------------------
# Local store
# ---------------------------------------------------------------------------

def default_db_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "Pawmate")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "pawmate.db")


class Store:
    """SQLite, one connection per thread (sqlite3 objects aren't shareable)."""

    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        self._local = threading.local()
        self._init()

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5)
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    def _init(self):
        c = self.conn
        c.executescript("""
            CREATE TABLE IF NOT EXISTS app_segments(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_ts REAL NOT NULL, end_ts REAL NOT NULL,
                exe TEXT, app_name TEXT, title TEXT,
                category TEXT, idle INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_seg_start ON app_segments(start_ts);
            CREATE TABLE IF NOT EXISTS water_log(ts REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(ts REAL NOT NULL, kind TEXT, detail TEXT);
        """)
        c.commit()

    # -- writes ------------------------------------------------------------
    def add_segments(self, rows: list[tuple]):
        if not rows:
            return
        self.conn.executemany(
            "INSERT INTO app_segments(start_ts,end_ts,exe,app_name,title,category,idle) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        self.conn.commit()

    def log_water(self, ts: float | None = None):
        self.conn.execute("INSERT INTO water_log(ts) VALUES (?)", (ts or time.time(),))
        self.conn.commit()

    def log_event(self, kind: str, detail: str = ""):
        self.conn.execute("INSERT INTO events(ts,kind,detail) VALUES (?,?,?)",
                          (time.time(), kind, detail))
        self.conn.commit()

    # -- reads -------------------------------------------------------------
    @staticmethod
    def day_bounds(day_offset: int = 0) -> tuple[float, float]:
        now = time.time()
        lt = time.localtime(now - day_offset * 86400)
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return start, start + 86400

    def segments(self, day_offset: int = 0) -> list[sqlite3.Row]:
        a, b = self.day_bounds(day_offset)
        return list(self.conn.execute(
            "SELECT * FROM app_segments WHERE end_ts>=? AND start_ts<? ORDER BY start_ts", (a, b)))

    def water_count(self, day_offset: int = 0) -> int:
        a, b = self.day_bounds(day_offset)
        return self.conn.execute(
            "SELECT COUNT(*) FROM water_log WHERE ts>=? AND ts<?", (a, b)).fetchone()[0]

    def stats(self, day_offset: int = 0) -> dict:
        return summarise(self.segments(day_offset), self.water_count(day_offset))


# ---------------------------------------------------------------------------
# Stats + score (R1)
# ---------------------------------------------------------------------------

def summarise(rows, water: int = 0) -> dict:
    by_app: dict[str, float] = {}
    by_cat: dict[str, float] = {PRODUCTIVE: 0.0, NEUTRAL: 0.0, DISTRACTING: 0.0}
    active = idle = 0.0
    switches = 0
    last_app = None
    blocks: list[float] = []          # contiguous productive runs, in seconds
    run = 0.0

    for r in rows:
        dur = max(0.0, r["end_ts"] - r["start_ts"])
        if r["idle"]:
            idle += dur
            if run:
                blocks.append(run)
                run = 0.0
            continue
        active += dur
        name = r["app_name"] or r["exe"] or "unknown"
        by_app[name] = by_app.get(name, 0.0) + dur
        cat = r["category"] or NEUTRAL
        by_cat[cat] = by_cat.get(cat, 0.0) + dur
        if name != last_app:
            switches += 1
            last_app = name
        if cat == PRODUCTIVE:
            run += dur
        elif run:
            blocks.append(run)
            run = 0.0
    if run:
        blocks.append(run)

    deep_min = sum(b for b in blocks if b >= DEEP_WORK_BLOCK_MIN * 60) / 60.0
    active_min = active / 60.0
    p = (by_cat[PRODUCTIVE] / active) if active else 0.0
    dw = min(deep_min / DEEP_WORK_TARGET_MIN, 1.0)
    b_ratio = 1.0                                   # breaks not tracked yet -> neutral
    sw_per_hr = (switches / (active_min / 60.0)) if active_min > 5 else 0.0
    s = max(0.0, min((sw_per_hr - 20.0) / 40.0, 1.0))
    score = round(100 * (0.50 * p + 0.30 * dw + 0.20 * b_ratio) * (1 - 0.25 * s))

    return {
        "active_min": active_min,
        "idle_min": idle / 60.0,
        "productive_min": by_cat[PRODUCTIVE] / 60.0,
        "neutral_min": by_cat[NEUTRAL] / 60.0,
        "distracting_min": by_cat[DISTRACTING] / 60.0,
        "deep_work_min": deep_min,
        "switches": switches,
        "switches_per_hour": sw_per_hr,
        "score": max(0, min(100, score)),
        "water": water,
        "top_apps": sorted(by_app.items(), key=lambda kv: -kv[1])[:8],
        "by_cat": by_cat,
    }


# ---------------------------------------------------------------------------
# The tracker
# ---------------------------------------------------------------------------

class ActivityTracker:
    """Samples the foreground window and coalesces it into segments."""

    def __init__(self, store: Store):
        self.store = store
        self.rules = None            # set by the app: user category overrides
        self.settings = None         # set by the app: live idle threshold etc.
        self.idle_threshold = IDLE_THRESHOLD_S
        self.paused_until = 0.0
        self.excluded: list[str] = ["keepass", "bitwarden", "1password", "lastpass"]
        self._pending: list[tuple] = []
        self._cur: dict | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.current_label = ""

    # -- control -----------------------------------------------------------
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._close_current(time.time())
        self.flush()

    def pause(self, seconds: float):
        self.paused_until = time.time() + seconds
        self._close_current(time.time())

    def resume(self):
        self.paused_until = 0.0

    @property
    def paused(self) -> bool:
        return time.time() < self.paused_until

    # -- loop --------------------------------------------------------------
    def _loop(self):
        last_flush = time.time()
        while not self._stop.is_set():
            try:
                self._sample()
                if time.time() - last_flush >= FLUSH_SECONDS:
                    self.flush()
                    last_flush = time.time()
            except Exception as exc:  # noqa: BLE001 - tracking must never kill the pet
                print(f"[track] sample failed: {exc}")
            self._stop.wait(POLL_SECONDS)

    def _sample(self):
        now = time.time()
        if self.paused:
            self._close_current(now)
            self.current_label = "paused"
            return

        is_idle = idle_seconds() >= self.idle_threshold
        exe, title = foreground_app()
        if any(x in (exe or "").lower() for x in self.excluded):
            self._close_current(now)
            self.current_label = "excluded"
            return

        app = _friendly(exe)
        key = ("idle", "", "") if is_idle else (app, exe, title)
        self.current_label = "idle" if is_idle else (app or "?")

        cur = self._cur
        if cur and cur["key"] == key:
            cur["end"] = now
            return
        self._close_current(now)
        self._cur = {"key": key, "start": now, "end": now,
                     "exe": exe, "app": app, "title": title, "idle": int(is_idle)}

    def _close_current(self, now: float):
        cur = self._cur
        self._cur = None
        if not cur:
            return
        dur = max(0.0, cur["end"] - cur["start"])
        if dur < MIN_SEGMENT_S:
            return
        cat = "" if cur["idle"] else categorise_with(self.rules, cur["exe"], cur["title"])
        with self._lock:
            self._pending.append((cur["start"], cur["end"], cur["exe"], cur["app"],
                                  cur["title"], cat, cur["idle"]))

    def reload_settings(self):
        if self.settings:
            try:
                self.idle_threshold = float(self.settings.get("idle_threshold_s") or IDLE_THRESHOLD_S)
            except (TypeError, ValueError):
                pass

    def flush(self):
        with self._lock:
            rows, self._pending = self._pending, []
        try:
            self.store.add_segments(rows)
        except Exception as exc:  # noqa: BLE001
            print(f"[track] flush failed: {exc}")
            with self._lock:
                self._pending = rows + self._pending

    def live_stats(self, day_offset: int = 0) -> dict:
        """Stats including whatever hasn't been flushed yet."""
        self._close_current(time.time())
        self.flush()
        return self.store.stats(day_offset)


_FRIENDLY = {
    "msedge.exe": "Edge", "chrome.exe": "Chrome", "firefox.exe": "Firefox",
    "brave.exe": "Brave", "code.exe": "VS Code", "windowsterminal.exe": "Terminal",
    "explorer.exe": "File Explorer", "notepad.exe": "Notepad", "teams.exe": "Teams",
    "outlook.exe": "Outlook", "slack.exe": "Slack", "spotify.exe": "Spotify",
    "discord.exe": "Discord", "pycharm64.exe": "PyCharm", "idea64.exe": "IntelliJ",
    "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
}


def _friendly(exe: str) -> str:
    if not exe:
        return ""
    return _FRIENDLY.get(exe.lower(), os.path.splitext(exe)[0].title())


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


# ---------------------------------------------------------------------------
# Settings, user category overrides, and the day's todo list.
#
# Added as a second layer on top of the tracker above. Kept in the same
# SQLite file so there's one thing to back up and one thing to delete.
# ---------------------------------------------------------------------------

import json as _json

SETTINGS_DEFAULTS = {
    "water_every_min": 45,
    "break_every_min": 50,
    "eye_every_min": 20,          # 20-20-20 rule
    "eye_duration_s": 20,
    "blocking_breaks": True,      # break overlay holds the screen
    "water_blocking": True,
    "hotkey": "ctrl+grave",       # deliberately not a Windows default
    "idle_threshold_s": 120,
    "deep_work_target_min": 180,
    "water_goal": 8,
}


class Settings:
    """Key/value settings with typed defaults, persisted in SQLite."""

    def __init__(self, store: "Store"):
        self.store = store
        store.conn.execute(
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        store.conn.commit()
        self._cache: dict = dict(SETTINGS_DEFAULTS)
        for row in store.conn.execute("SELECT key,value FROM settings"):
            try:
                self._cache[row["key"]] = _json.loads(row["value"])
            except Exception:  # noqa: BLE001
                pass

    def get(self, key: str):
        return self._cache.get(key, SETTINGS_DEFAULTS.get(key))

    def set(self, key: str, value):
        self._cache[key] = value
        self.store.conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, _json.dumps(value)))
        self.store.conn.commit()

    def all(self) -> dict:
        return dict(self._cache)

    def reset(self):
        self._cache = dict(SETTINGS_DEFAULTS)
        self.store.conn.execute("DELETE FROM settings")
        self.store.conn.commit()


class UserRules:
    """User category overrides (plan T3: one-click re-tag).

    No heuristic can tell a YouTube lecture from a YouTube time-sink
    reliably. So instead of guessing harder: let the user correct it once,
    on the actual window title, and remember that forever. User rules are
    consulted before any built-in rule.
    """

    def __init__(self, store: "Store"):
        self.store = store
        store.conn.execute("""
            CREATE TABLE IF NOT EXISTS user_rules(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,           -- 'title' | 'exe'
                pattern TEXT,        -- lowercase substring
                category TEXT,
                created_ts REAL)""")
        store.conn.commit()
        self.reload()

    def reload(self):
        self.rules = [(r["kind"], r["pattern"], r["category"])
                      for r in self.store.conn.execute(
                          "SELECT kind,pattern,category FROM user_rules ORDER BY LENGTH(pattern) DESC")]

    def add(self, kind: str, pattern: str, category: str):
        pattern = (pattern or "").strip().lower()
        if not pattern:
            return
        self.store.conn.execute(
            "DELETE FROM user_rules WHERE kind=? AND pattern=?", (kind, pattern))
        self.store.conn.execute(
            "INSERT INTO user_rules(kind,pattern,category,created_ts) VALUES(?,?,?,?)",
            (kind, pattern, category, time.time()))
        self.store.conn.commit()
        self.reload()

    def remove(self, kind: str, pattern: str):
        self.store.conn.execute("DELETE FROM user_rules WHERE kind=? AND pattern=?",
                                (kind, pattern.lower()))
        self.store.conn.commit()
        self.reload()

    def match(self, exe: str, title: str) -> str | None:
        t, e = (title or "").lower(), (exe or "").lower()
        for kind, pattern, cat in self.rules:
            if kind == "title" and pattern in t:
                return cat
            if kind == "exe" and pattern in e:
                return cat
        return None

    def list_all(self) -> list[tuple[str, str, str]]:
        return list(self.rules)


# Broader built-in hints for study/learning content, so the common case is
# right before any correction is needed. Still only a prior — the user rule
# above always wins.
_LEARNING_HINTS = (r"lecture|tutorial|course|lesson|chapter|exam|revision|"
                   r"documentation|\bdocs\b|stack overflow|github|leetcode|"
                   r"khan academy|coursera|udemy|edx|nptel|freecodecamp|"
                   r"conference talk|keynote|how to|explained|crash course")


def categorise_with(rules: "UserRules | None", exe: str, title: str) -> str:
    """Categorise, consulting user overrides first."""
    if rules:
        hit = rules.match(exe, title)
        if hit:
            return hit
    t = (title or "").lower()
    if re.search(_LEARNING_HINTS, t):
        return PRODUCTIVE
    return categorise(exe, title)


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------

def today_key(day_offset: int = 0) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - day_offset * 86400))


class Todos:
    """The day's task list, plus which one is currently being worked on."""

    def __init__(self, store: "Store"):
        self.store = store
        store.conn.executescript("""
            CREATE TABLE IF NOT EXISTS todos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL, text TEXT NOT NULL,
                done INTEGER DEFAULT 0, created_ts REAL, done_ts REAL,
                est_min INTEGER DEFAULT 0, active INTEGER DEFAULT 0,
                spent_s REAL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_todo_day ON todos(day);
        """)
        store.conn.commit()

    def add(self, text: str, est_min: int = 0, day: str | None = None) -> int:
        cur = self.store.conn.execute(
            "INSERT INTO todos(day,text,created_ts,est_min) VALUES(?,?,?,?)",
            (day or today_key(), text.strip(), time.time(), est_min))
        self.store.conn.commit()
        return cur.lastrowid

    def list(self, day: str | None = None) -> list:
        return list(self.store.conn.execute(
            "SELECT * FROM todos WHERE day=? ORDER BY done, id", (day or today_key(),)))

    def set_done(self, todo_id: int, done: bool = True):
        self.store.conn.execute(
            "UPDATE todos SET done=?, done_ts=?, active=0 WHERE id=?",
            (int(done), time.time() if done else None, todo_id))
        self.store.conn.commit()

    def delete(self, todo_id: int):
        self.store.conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        self.store.conn.commit()

    def set_active(self, todo_id: int | None):
        """Exactly one task can be 'what I'm working on right now'."""
        self.store.conn.execute("UPDATE todos SET active=0 WHERE day=?", (today_key(),))
        if todo_id:
            self.store.conn.execute("UPDATE todos SET active=1, done=0 WHERE id=?", (todo_id,))
        self.store.conn.commit()

    def active(self):
        return self.store.conn.execute(
            "SELECT * FROM todos WHERE day=? AND active=1 LIMIT 1", (today_key(),)).fetchone()

    def add_spent(self, todo_id: int, seconds: float):
        self.store.conn.execute("UPDATE todos SET spent_s=spent_s+? WHERE id=?",
                                (seconds, todo_id))
        self.store.conn.commit()

    def find(self, needle: str):
        """Fuzzy-ish lookup so chat can say 'done milk' for 'buy the milk'."""
        needle = needle.strip().lower()
        if not needle:
            return None
        rows = self.list()
        for r in rows:
            if r["text"].lower() == needle:
                return r
        for r in rows:
            if needle in r["text"].lower():
                return r
        import difflib
        names = {r["text"].lower(): r for r in rows}
        close = difflib.get_close_matches(needle, names.keys(), n=1, cutoff=0.6)
        return names[close[0]] if close else None

    def progress(self, day: str | None = None) -> tuple[int, int]:
        rows = self.list(day)
        return sum(1 for r in rows if r["done"]), len(rows)
