"""Adaptive categorisation — learns instead of matching fixed rules.

The static regex table it replaces could never be right: "Chrome" is
productive on documentation and distracting on the same site an hour later,
and no list of patterns shipped by me knows what *your* work looks like.

So this is a naive-Bayes-style token classifier over window titles and
executable names. The old patterns survive only as weak priors to bootstrap
a cold start; every piece of real evidence outweighs them, and the priors
are outvoted within a handful of corrections.

Evidence comes from two places:

  Explicit — you re-tagged something. Heavy weight, and it is never
  overwritten by inference.

  Implicit — behaviour that is informative on its own:
    · an app used during a focus session, or while a todo was active and
      you stayed on it, leans productive
    · an app you opened and abandoned inside a few seconds is noise and is
      not learned from at all
    · an app you were on when you ignored a break and kept typing leans
      productive (you were in something)
    · an app open during a long unbroken stretch leans productive; one you
      bounce in and out of repeatedly leans distracting

Every verdict carries a confidence and the tokens that drove it, so the UI
can show its working rather than asserting a category.
"""
from __future__ import annotations

import json
import math
import re
import time

PRODUCTIVE = "productive"
NEUTRAL = "neutral"
DISTRACTING = "distracting"
CATEGORIES = (PRODUCTIVE, NEUTRAL, DISTRACTING)

EXPLICIT_WEIGHT = 12.0      # a correction is worth many observations
IMPLICIT_WEIGHT = 1.0
PRIOR_WEIGHT = 2.0          # seeds only; real evidence overtakes quickly
DECAY_HALFLIFE_DAYS = 45.0  # habits drift; old evidence should fade

_SPLIT = re.compile(r"[^a-z0-9+#]+")
_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "you", "your", "our",
    "new", "tab", "page", "window", "microsoft", "google", "mozilla", "app",
    "exe", "com", "www", "http", "https", "net", "org", "html", "at", "in",
    "on", "of", "to", "a", "an", "is", "it", "my", "me", "home", "untitled",
}


def tokens(exe: str, title: str) -> list[str]:
    """Feature extraction. Exe tokens are namespaced so 'code' the program
    can't be confused with 'code' appearing in a page title."""
    out: list[str] = []
    for t in _SPLIT.split((title or "").lower()):
        if len(t) > 2 and t not in _STOP and not t.isdigit():
            out.append(t)
    base = re.sub(r"\.exe$", "", (exe or "").lower())
    for t in _SPLIT.split(base):
        if len(t) > 1 and t not in _STOP:
            out.append("exe:" + t)
    return out[:40]


# Weak priors only. These are starting guesses, not rules — one correction
# from the user outweighs any of them.
_PRIORS: list[tuple[str, str]] = [
    (r"lecture|tutorial|course|lesson|documentation|\bdocs\b|reference|"
     r"stack overflow|github|gitlab|leetcode|coursera|udemy|nptel|edx|"
     r"khan academy|freecodecamp|manual|api|spec", PRODUCTIVE),
    (r"visual studio|pycharm|intellij|sublime|neovim|terminal|powershell|"
     r"jupyter|postman|figma|excel|word|powerpoint|notion|obsidian|jira|"
     r"confluence|linear", PRODUCTIVE),
    (r"teams|outlook|slack|zoom|meet|calendar|gmail|mail|inbox", NEUTRAL),
    (r"explorer|settings|control panel|task manager|finder", NEUTRAL),
    (r"netflix|prime video|hotstar|disney|instagram|tiktok|reddit|twitch|"
     r"9gag|pinterest|shorts|memes|steam|epic games", DISTRACTING),
]


class AdaptiveClassifier:
    """Token -> category weights, persisted in the same SQLite store."""

    def __init__(self, store):
        self.store = store
        store.conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_weights(
                token TEXT NOT NULL,
                category TEXT NOT NULL,
                weight REAL DEFAULT 0,
                explicit INTEGER DEFAULT 0,
                updated_ts REAL,
                PRIMARY KEY (token, category));
            CREATE TABLE IF NOT EXISTS classify_log(
                ts REAL, exe TEXT, title TEXT, category TEXT,
                confidence REAL, source TEXT);
        """)
        store.conn.commit()
        self._w: dict[str, dict[str, float]] = {}
        self._explicit: set[str] = set()
        self.reload()

    # -- persistence -------------------------------------------------------
    def reload(self):
        self._w = {}
        self._explicit = set()
        now = time.time()
        for r in self.store.conn.execute(
                "SELECT token,category,weight,explicit,updated_ts FROM token_weights"):
            age_days = max(0.0, (now - (r["updated_ts"] or now)) / 86400.0)
            decay = 0.5 ** (age_days / DECAY_HALFLIFE_DAYS)
            w = (r["weight"] or 0.0) * (1.0 if r["explicit"] else decay)
            self._w.setdefault(r["token"], {})[r["category"]] = w
            if r["explicit"]:
                self._explicit.add(r["token"])

    def _bump(self, token: str, category: str, amount: float, explicit: bool):
        self.store.conn.execute(
            "INSERT INTO token_weights(token,category,weight,explicit,updated_ts) "
            "VALUES(?,?,?,?,?) ON CONFLICT(token,category) DO UPDATE SET "
            "weight = token_weights.weight + excluded.weight, "
            "explicit = MAX(token_weights.explicit, excluded.explicit), "
            "updated_ts = excluded.updated_ts",
            (token, category, amount, int(explicit), time.time()))
        self._w.setdefault(token, {})
        self._w[token][category] = self._w[token].get(category, 0.0) + amount
        if explicit:
            self._explicit.add(token)

    # -- learning ----------------------------------------------------------
    def teach(self, exe: str, title: str, category: str, explicit: bool = True,
              weight: float | None = None, focus_tokens: list[str] | None = None):
        """Learn that this window is `category`.

        focus_tokens narrows learning to specific words — used by the re-tag
        UI so correcting one YouTube channel doesn't relabel all of YouTube.
        """
        if category not in CATEGORIES:
            return
        w = weight if weight is not None else (EXPLICIT_WEIGHT if explicit else IMPLICIT_WEIGHT)
        toks = focus_tokens if focus_tokens else tokens(exe, title)
        if not toks:
            return
        # spread the evidence so a 30-word title doesn't swamp a 3-word one
        share = w / math.sqrt(len(toks))
        for t in toks:
            self._bump(t, category, share, explicit)
            if explicit:                      # push competing categories down
                for other in CATEGORIES:
                    if other != category:
                        self._bump(t, other, -share * 0.5, False)
        self.store.conn.commit()

    def observe(self, exe: str, title: str, duration_s: float, *,
                in_focus_session: bool = False, on_active_todo: bool = False,
                switched_away_fast: bool = False, category_hint: str | None = None):
        """Implicit learning from how a window was actually used."""
        if switched_away_fast or duration_s < 20:
            return                            # too little signal; ignore entirely
        toks = tokens(exe, title)
        if not toks:
            return
        if any(t in self._explicit for t in toks):
            return                            # user has spoken; don't drift off it

        cat, w = None, IMPLICIT_WEIGHT
        if in_focus_session or on_active_todo:
            cat = PRODUCTIVE
            w = IMPLICIT_WEIGHT * (1.6 if in_focus_session else 1.2)
        elif duration_s >= 25 * 60:
            cat = PRODUCTIVE                  # sustained attention
            w = IMPLICIT_WEIGHT * 0.8
        elif category_hint:
            cat = category_hint
            w = IMPLICIT_WEIGHT * 0.4
        if cat:
            self.teach(exe, title, cat, explicit=False, weight=w)

    def forget(self, token: str):
        self.store.conn.execute("DELETE FROM token_weights WHERE token=?", (token,))
        self.store.conn.commit()
        self.reload()

    # -- inference ---------------------------------------------------------
    def _prior(self, exe: str, title: str) -> dict[str, float]:
        hay = f"{(title or '').lower()} {(exe or '').lower()}"
        scores = {c: 0.0 for c in CATEGORIES}
        for pattern, cat in _PRIORS:
            if re.search(pattern, hay):
                scores[cat] += PRIOR_WEIGHT
        return scores

    def classify(self, exe: str, title: str) -> dict:
        """Return category, confidence 0..1, and the evidence behind it."""
        toks = tokens(exe, title)
        scores = self._prior(exe, title)
        evidence: dict[str, float] = {}
        explicit_hit = False

        for t in toks:
            tw = self._w.get(t)
            if not tw:
                continue
            if t in self._explicit:
                explicit_hit = True
            best = max(tw.items(), key=lambda kv: kv[1], default=None)
            for cat, val in tw.items():
                scores[cat] = scores.get(cat, 0.0) + val
            if best and best[1] > 0:
                evidence[t] = evidence.get(t, 0.0) + best[1]

        pos = {c: max(0.0, v) for c, v in scores.items()}
        total = sum(pos.values())
        if total <= 0:
            return {"category": NEUTRAL, "confidence": 0.0, "evidence": [],
                    "source": "unknown", "scores": scores}

        cat = max(pos.items(), key=lambda kv: kv[1])[0]
        conf = pos[cat] / total
        # two close contenders shouldn't read as certainty
        ordered = sorted(pos.values(), reverse=True)
        if len(ordered) > 1 and ordered[0] > 0:
            conf *= (1.0 - min(0.9, ordered[1] / ordered[0]) * 0.45)
        top = [t for t, _ in sorted(evidence.items(), key=lambda kv: -kv[1])[:4]]
        return {
            "category": cat,
            "confidence": round(min(1.0, conf), 3),
            "evidence": top,
            "source": "you" if explicit_hit else ("learned" if evidence else "prior"),
            "scores": scores,
        }

    def categorise(self, exe: str, title: str) -> str:
        return self.classify(exe, title)["category"]

    # -- introspection for the UI -----------------------------------------
    def learned_terms(self, limit: int = 60) -> list[tuple[str, str, float, bool]]:
        rows = []
        for token, cats in self._w.items():
            if not cats:
                continue
            cat, w = max(cats.items(), key=lambda kv: kv[1])
            if w <= 0.2:
                continue
            rows.append((token, cat, round(w, 2), token in self._explicit))
        rows.sort(key=lambda r: (-int(r[3]), -r[2]))
        return rows[:limit]

    def stats(self) -> dict:
        return {
            "terms": len(self._w),
            "explicit": len(self._explicit),
        }

    def export(self) -> str:
        return json.dumps({t: c for t, c in self._w.items()}, indent=2)
