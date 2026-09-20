"""Daily digest — turns tracked data into something you'd actually send.

The point isn't another chart. It's the paragraph you write by hand every
morning for standup, or at month end for an invoice, generated from what
actually happened: repos and branches with time and commits, tasks closed,
meeting load, focus windows, and where the day leaked.

Written as plain text so it pastes into Slack, a commit message, a timesheet
or an email without reformatting.
"""
from __future__ import annotations

import os
import time

from pawmate_tracking import fmt_minutes

MEETING_HINTS = ("teams", "zoom", "meet", "webex", "skype", "slack huddle",
                 "google meet", "gotomeeting")


def _hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def meeting_minutes(segments) -> float:
    total = 0.0
    for r in segments:
        if r["idle"]:
            continue
        hay = f"{(r['exe'] or '').lower()} {(r['title'] or '').lower()}"
        if any(h in hay for h in MEETING_HINTS):
            total += (r["end_ts"] - r["start_ts"])
    return total / 60.0


def focus_blocks(segments, min_minutes: float = 25.0) -> list[dict]:
    """Contiguous productive runs, allowing brief interruptions.

    A 40-second glance at Slack shouldn't shatter an hour of deep work, so
    short non-productive gaps are absorbed rather than ending the block.
    """
    blocks: list[dict] = []
    cur = None
    GAP_TOLERANCE = 90.0
    for r in segments:
        dur = r["end_ts"] - r["start_ts"]
        productive = (not r["idle"]) and r["category"] == "productive"
        if productive:
            if cur and r["start_ts"] - cur["end"] <= GAP_TOLERANCE:
                cur["end"] = r["end_ts"]
            else:
                if cur:
                    blocks.append(cur)
                cur = {"start": r["start_ts"], "end": r["end_ts"]}
        elif cur and dur > GAP_TOLERANCE and not r["idle"]:
            blocks.append(cur)
            cur = None
        elif cur and r["idle"] and dur > 300:
            blocks.append(cur)
            cur = None
    if cur:
        blocks.append(cur)
    out = [b for b in blocks if (b["end"] - b["start"]) / 60.0 >= min_minutes]
    for b in out:
        b["minutes"] = (b["end"] - b["start"]) / 60.0
    return out


def peak_window(segments, bucket_minutes: int = 60) -> tuple[int, float] | None:
    """Hour of day with the most productive time. Feeds 'protect this hour'."""
    buckets: dict[int, float] = {}
    for r in segments:
        if r["idle"] or r["category"] != "productive":
            continue
        hour = time.localtime(r["start_ts"]).tm_hour
        buckets[hour] = buckets.get(hour, 0.0) + (r["end_ts"] - r["start_ts"])
    if not buckets:
        return None
    hour, secs = max(buckets.items(), key=lambda kv: kv[1])
    return hour, secs / 60.0


def build(stats: dict, segments, repo_report: list[dict], todos_done: list,
          todos_open: list, day_start: float) -> dict:
    """Assemble every part of the digest. Rendering is separate."""
    blocks = focus_blocks(segments)
    meetings = meeting_minutes(segments)
    peak = peak_window(segments)
    commits = [c for r in repo_report for c in r["commits"]]

    first = next((r["start_ts"] for r in segments if not r["idle"]), None)
    last = next((r["end_ts"] for r in reversed(list(segments)) if not r["idle"]), None)

    distractions: dict[str, float] = {}
    for r in segments:
        if r["idle"] or r["category"] != "distracting":
            continue
        name = r["app_name"] or r["exe"] or "unknown"
        distractions[name] = distractions.get(name, 0.0) + (r["end_ts"] - r["start_ts"])

    return {
        "date": time.strftime("%A %d %B", time.localtime(day_start)),
        "stats": stats,
        "repos": repo_report,
        "commits": commits,
        "focus_blocks": blocks,
        "longest_block": max((b["minutes"] for b in blocks), default=0.0),
        "meeting_min": meetings,
        "peak": peak,
        "todos_done": todos_done,
        "todos_open": todos_open,
        "span": (first, last),
        "distractions": sorted(distractions.items(), key=lambda kv: -kv[1])[:3],
    }


def render_standup(d: dict) -> str:
    """The paste-into-Slack version. Past tense, no filler, no emoji."""
    lines: list[str] = []
    for r in d["repos"][:5]:
        if r["secs"] < 300:
            continue
        branches = sorted(r["branches"].items(), key=lambda kv: -kv[1])
        branch = branches[0][0] if branches else ""
        bit = f"{r['name']}"
        if branch and branch not in ("HEAD", "main", "master"):
            bit += f" ({branch})"
        bit += f" — {fmt_minutes(r['secs'] / 60)}"
        if r["commits"]:
            bit += f", {len(r['commits'])} commit{'s' if len(r['commits']) != 1 else ''}"
        lines.append(bit)

    for t in d["todos_done"][:6]:
        lines.append(f"{t['text']}")

    if not lines:
        lines.append("No tracked project work.")

    out = ["Yesterday/today:"]
    out += [f"  - {l}" for l in lines]

    if d["todos_open"]:
        out.append("Next:")
        out += [f"  - {t['text']}" for t in d["todos_open"][:4]]

    if d["meeting_min"] >= 15:
        out.append(f"Meetings: {fmt_minutes(d['meeting_min'])}")
    return "\n".join(out)


def render_detail(d: dict) -> str:
    """The full record — for a timesheet, invoice or personal log."""
    s = d["stats"]
    out = [d["date"], ""]
    first, last = d["span"]
    if first and last:
        out.append(f"At the desk   {_hhmm(first)} - {_hhmm(last)}")
    out.append(f"Active        {fmt_minutes(s['active_min'])}"
               f"   (deep work {fmt_minutes(s['deep_work_min'])})")
    out.append(f"Focus score   {s['score']}")
    if d["longest_block"]:
        out.append(f"Longest unbroken stretch   {fmt_minutes(d['longest_block'])}")
    if d["meeting_min"] >= 5:
        out.append(f"Meetings      {fmt_minutes(d['meeting_min'])}")
    if d["peak"]:
        hour, mins = d["peak"]
        out.append(f"Sharpest hour {hour:02d}:00 - {hour + 1:02d}:00"
                   f"   ({fmt_minutes(mins)} productive)")
    out.append("")

    if d["repos"]:
        out.append("PROJECTS")
        for r in d["repos"]:
            if r["secs"] < 120:
                continue
            out.append(f"  {r['name']}   {fmt_minutes(r['secs'] / 60)}")
            for br, secs in sorted(r["branches"].items(), key=lambda kv: -kv[1])[:4]:
                out.append(f"      {br}   {fmt_minutes(secs / 60)}")
            for c in r["commits"][:6]:
                out.append(f"      {_hhmm(c['ts'])}  {c['sha']}  {c['subject'][:60]}")
        out.append("")

    if d["todos_done"]:
        out.append("COMPLETED")
        out += [f"  {t['text']}" for t in d["todos_done"]]
        out.append("")
    if d["todos_open"]:
        out.append("STILL OPEN")
        out += [f"  {t['text']}" for t in d["todos_open"]]
        out.append("")

    if d["distractions"]:
        out.append("WHERE IT LEAKED")
        for name, secs in d["distractions"]:
            out.append(f"  {name}   {fmt_minutes(secs / 60)}")
        out.append("")

    apps = s.get("top_apps") or []
    if apps:
        out.append("APPLICATIONS")
        for name, secs in apps[:6]:
            out.append(f"  {name}   {fmt_minutes(secs / 60)}")
    return "\n".join(out)


def insights(d: dict) -> list[str]:
    """A few honest observations. Only things the data actually supports."""
    out: list[str] = []
    s = d["stats"]
    active = s["active_min"]

    if active < 20:
        return ["Not enough tracked time yet to say anything useful."]

    if d["meeting_min"] > 0 and active > 0:
        share = d["meeting_min"] / active
        if share > 0.4:
            out.append(f"Meetings took {round(share * 100)}% of your active time. "
                       f"Deep work got {fmt_minutes(s['deep_work_min'])}.")

    if d["longest_block"] >= 50:
        out.append(f"Longest unbroken stretch was {fmt_minutes(d['longest_block'])} — "
                   f"that's a real focus block.")
    elif s["switches_per_hour"] > 45:
        out.append(f"{round(s['switches_per_hour'])} app switches an hour. "
                   f"Nothing got a long runway today.")

    if d["peak"]:
        hour, mins = d["peak"]
        if mins >= 25:
            out.append(f"You were sharpest around {hour:02d}:00. "
                       f"Worth defending that hour.")

    if d["distractions"]:
        name, secs = d["distractions"][0]
        if secs / 60 >= 20:
            out.append(f"{name} took {fmt_minutes(secs / 60)}.")

    if d["commits"]:
        late = [c for c in d["commits"] if time.localtime(c["ts"]).tm_hour >= 20]
        if late and len(late) >= max(2, len(d["commits"]) // 2):
            out.append(f"{len(late)} of {len(d['commits'])} commits landed after 20:00.")

    done, openn = len(d["todos_done"]), len(d["todos_open"])
    if done or openn:
        out.append(f"{done} task{'s' if done != 1 else ''} closed, {openn} still open.")
    return out[:5]
