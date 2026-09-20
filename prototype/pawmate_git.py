"""Git-aware tracking: which repo and branch the time actually went to.

"VS Code — 3h" is close to useless. "feature/payments — 3h12m, 4 commits"
is a thing you can put in a standup, an invoice, or a retro.

Repos are discovered by scanning a few likely roots (shallow, so it stays
fast), then matched against the foreground window title: editors and
terminals nearly always put the project folder name in the title, which is
enough to attribute a segment without hooking into the editor itself.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

SCAN_ROOTS_ENV = "PAWMATE_CODE_ROOTS"
DEFAULT_ROOT_NAMES = ("source", "src", "code", "dev", "projects", "repos",
                      "workspace", "git", "Documents", "Desktop", "Downloads")
MAX_DEPTH = 3
RESCAN_SECONDS = 900
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str], cwd: str, timeout: float = 6.0) -> str:
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, creationflags=_NO_WINDOW)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - git may be missing entirely
        return ""


def git_available() -> bool:
    try:
        p = subprocess.run(["git", "--version"], capture_output=True,
                           timeout=6, creationflags=_NO_WINDOW)
        return p.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def candidate_roots() -> list[str]:
    override = os.environ.get(SCAN_ROOTS_ENV)
    if override:
        return [p for p in override.split(os.pathsep) if os.path.isdir(p)]
    home = os.path.expanduser("~")
    roots = [home]
    for name in DEFAULT_ROOT_NAMES:
        p = os.path.join(home, name)
        if os.path.isdir(p):
            roots.append(p)
    return roots


def discover_repos(roots: list[str] | None = None, max_depth: int = MAX_DEPTH,
                   limit: int = 400) -> dict[str, str]:
    """{folder_name_lower: repo_path}. Shallow walk, prunes heavy dirs."""
    found: dict[str, str] = {}
    skip = {"node_modules", "venv", ".venv", "env", "__pycache__", "dist",
            "build", "target", "AppData", "Windows", "Program Files",
            "Program Files (x86)", ".cache", "site-packages", "Temp"}
    for root in (roots or candidate_roots()):
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, _files in os.walk(root):
            if len(found) >= limit:
                return found
            depth = dirpath.count(os.sep) - base_depth
            if depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d not in skip and not d.startswith(".") or d == ".git"]
            if ".git" in dirnames:
                name = os.path.basename(dirpath)
                if name:
                    found.setdefault(name.lower(), dirpath)
                dirnames[:] = []           # don't descend into a repo
    return found


class GitWatcher:
    """Maps window titles to repos, and reads branch + commit activity."""

    def __init__(self, store=None):
        self.store = store
        self.repos: dict[str, str] = {}
        self.available = False
        self._branch_cache: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        if store is not None:
            store.conn.executescript("""
                CREATE TABLE IF NOT EXISTS repo_segments(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_ts REAL, end_ts REAL,
                    repo TEXT, branch TEXT);
                CREATE INDEX IF NOT EXISTS idx_repo_start ON repo_segments(start_ts);
            """)
            store.conn.commit()

    def start(self):
        threading.Thread(target=self._scan_loop, daemon=True).start()

    def _scan_loop(self):
        while True:
            try:
                self.available = git_available()
                if self.available:
                    repos = discover_repos()
                    with self._lock:
                        self.repos = repos
                    if not self._ready.is_set():
                        print(f"[git] tracking {len(repos)} repositories")
                else:
                    print("[git] git not found on PATH — repo attribution disabled")
            except Exception as exc:  # noqa: BLE001
                print(f"[git] scan failed: {exc}")
            self._ready.set()
            time.sleep(RESCAN_SECONDS)

    # -- attribution -------------------------------------------------------
    def repo_for_title(self, title: str, exe: str = "") -> str | None:
        """Best-effort repo match from a window title.

        Editors format titles like "file.py - myproject - Visual Studio Code"
        and terminals like "myproject - pwsh". Matching on the longest repo
        name present avoids 'api' hijacking 'api-gateway'.
        """
        if not title:
            return None
        low = title.lower()
        with self._lock:
            repos = dict(self.repos)
        best = None
        for name, path in repos.items():
            if len(name) < 3:
                continue
            if name in low:
                if best is None or len(name) > len(best[0]):
                    best = (name, path)
        return best[1] if best else None

    def branch(self, repo_path: str, max_age: float = 20.0) -> str:
        now = time.time()
        hit = self._branch_cache.get(repo_path)
        if hit and now - hit[0] < max_age:
            return hit[1]
        b = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_path) or ""
        self._branch_cache[repo_path] = (now, b)
        return b

    def record(self, start_ts: float, end_ts: float, repo_path: str, branch: str):
        if not self.store or end_ts <= start_ts:
            return
        self.store.conn.execute(
            "INSERT INTO repo_segments(start_ts,end_ts,repo,branch) VALUES(?,?,?,?)",
            (start_ts, end_ts, repo_path, branch))
        self.store.conn.commit()

    # -- reporting ---------------------------------------------------------
    def commits_since(self, repo_path: str, since_ts: float, until_ts: float | None = None
                      ) -> list[dict]:
        """Commits by anyone in the window — filtered to the local user below."""
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since_ts))
        args = ["git", "log", "--all", "--no-merges", f"--since={since}",
                "--pretty=format:%H\x1f%an\x1f%ct\x1f%s"]
        if until_ts:
            args.append("--until=" + time.strftime("%Y-%m-%dT%H:%M:%S",
                                                   time.localtime(until_ts)))
        out = _run(args, repo_path)
        me = _run(["git", "config", "user.name"], repo_path).strip().lower()
        rows = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha, author, ts, subject = parts
            if me and author.strip().lower() != me:
                continue          # other people's commits aren't your day
            rows.append({"sha": sha[:8], "author": author, "ts": float(ts),
                         "subject": subject, "repo": repo_path})
        return rows

    def day_report(self, day_start: float, day_end: float) -> list[dict]:
        """Per-repo time and commits for the window, newest activity first."""
        if not self.store:
            return []
        rows = self.store.conn.execute(
            "SELECT repo, branch, SUM(end_ts-start_ts) AS secs, "
            "MIN(start_ts) AS first_ts, MAX(end_ts) AS last_ts "
            "FROM repo_segments WHERE end_ts>=? AND start_ts<? "
            "GROUP BY repo, branch ORDER BY secs DESC", (day_start, day_end)).fetchall()
        by_repo: dict[str, dict] = {}
        for r in rows:
            entry = by_repo.setdefault(r["repo"], {
                "repo": r["repo"], "name": os.path.basename(r["repo"]),
                "secs": 0.0, "branches": {}, "commits": [], "last_ts": 0.0})
            entry["secs"] += r["secs"] or 0.0
            if r["branch"]:
                entry["branches"][r["branch"]] = entry["branches"].get(r["branch"], 0.0) + (r["secs"] or 0.0)
            entry["last_ts"] = max(entry["last_ts"], r["last_ts"] or 0.0)
        for path, entry in by_repo.items():
            if os.path.isdir(path):
                try:
                    entry["commits"] = self.commits_since(path, day_start, day_end)
                except Exception:  # noqa: BLE001
                    entry["commits"] = []
        return sorted(by_repo.values(), key=lambda e: -e["secs"])
