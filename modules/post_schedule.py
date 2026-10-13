"""
Per-channel random daily scheduler: every channel posts ONE Short per day,
each at its OWN random time (random hour AND random minute) inside US waking hours.

How it works
------------
GitHub Actions cron only "wakes up" once an hour (the `gate` step below). Nothing is
stored in advance: today's schedule is derived from a hash of (SALT + US-Eastern date),
so every wake-up computes the SAME plan for that day, and it changes every day.

  * The posting window (default 10:00-22:00 ET = 7 AM-7 PM Pacific, i.e. everybody in the
    US is awake) is cut into N equal slices, one per channel.
  * Each slice gets one random minute (kept POST_EDGE_MIN away from the slice edges,
    so two channels are never closer than ~2*POST_EDGE_MIN minutes).
  * Which channel gets which slice is shuffled every day, so channel 1 is not always
    "the morning channel".

Each hourly wake-up asks: "which channels are due?"
  * a channel is due when its target time is at most POST_LEAD_MIN (default 90) minutes
    away, or already passed, and it has not posted today yet;
  * main.py then builds the video (takes ~10-25 min) and uploads it with YouTube's
    `publishAt`, so it goes public at the EXACT random minute, even if GitHub's cron was
    late. If the target already passed, it is published immediately (catch-up/retry).
  * after each successful upload main.py calls mark_posted() -> post_state.json
    (committed back to the repo by the workflow), so nothing is ever posted twice a day.

Pure standard library. Env overrides:
  CHANNEL_COUNT (default 3), POST_TZ (America/New_York),
  POST_WINDOW_START (default 10), POST_WINDOW_END (default 22, exclusive),
  POST_EDGE_MIN (default 30), POST_LEAD_MIN (default 90), POST_CATCHUP_MIN (default 60),
  SCHEDULE_SALT (any text, makes the daily pattern private).
"""

import hashlib
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

STATE_FILE = os.getenv("POST_STATE_FILE", "post_state.json")
TZ = ZoneInfo(os.getenv("POST_TZ", "America/New_York"))
CHANNEL_COUNT = int(os.getenv("CHANNEL_COUNT", "3"))
WINDOW_START = int(os.getenv("POST_WINDOW_START", "10"))
WINDOW_END = int(os.getenv("POST_WINDOW_END", "22"))        # exclusive
EDGE_MIN = int(os.getenv("POST_EDGE_MIN", "30"))
LEAD_MIN = int(os.getenv("POST_LEAD_MIN", "90"))
CATCHUP_MIN = int(os.getenv("POST_CATCHUP_MIN", "60"))      # retry allowed this long after window end
SALT = os.getenv("SCHEDULE_SALT") or os.getenv("GITHUB_REPOSITORY") or "shorts"


def _now(now=None):
    return (now or datetime.now(TZ)).astimezone(TZ)


def _today(now=None):
    return _now(now).strftime("%Y-%m-%d")


def _at(date_str, minute_of_day):
    """Aware ET datetime for `minute_of_day` on `date_str`."""
    base = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    return base + timedelta(minutes=minute_of_day)


def day_schedule(date_str, n=None):
    """{channel_index: minute_of_day} - deterministic for that ET date, random across days."""
    n = n or CHANNEL_COUNT
    seed = int(hashlib.sha256(f"{SALT}:{date_str}".encode()).hexdigest(), 16)
    rng = random.Random(seed)
    start, end = WINDOW_START * 60, WINDOW_END * 60
    span = (end - start) / n
    slots = []
    for i in range(n):
        lo = int(start + i * span + EDGE_MIN)
        hi = int(start + (i + 1) * span - EDGE_MIN)
        slots.append(rng.randint(lo, max(lo, hi)))
    order = list(range(n))
    rng.shuffle(order)
    return {order[i]: slots[i] for i in range(n)}


def _load_state(path=None):
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def posted_channels(now=None, path=None):
    state = _load_state(path)
    if state.get("date") != _today(now):
        return {}
    return state.get("posted", {})


def due_channels(now=None, n=None, path=None):
    """Returns ({channel_index: target_datetime_ET}, reasons[])."""
    n = n or CHANNEL_COUNT
    nw = _now(now)
    date = _today(nw)
    done = posted_channels(nw, path)
    sched = day_schedule(date, n)
    cutoff = _at(date, WINDOW_END * 60 + CATCHUP_MIN)
    due, reasons = {}, []
    for ch in range(n):
        target = _at(date, sched[ch])
        tag = f"ch{ch + 1} target {target.strftime('%H:%M')} ET"
        if str(ch) in done:
            reasons.append(f"{tag}: already posted today")
        elif nw > cutoff:
            reasons.append(f"{tag}: day is over, skipped")
        elif nw + timedelta(minutes=LEAD_MIN) >= target:
            due[ch] = target
            reasons.append(f"{tag}: DUE" + (" (late, catching up)" if nw >= target else ""))
        else:
            reasons.append(f"{tag}: waiting")
    return due, reasons


def mark_posted(channel_index, video_id="", now=None, path=None):
    n = _now(now)
    date = _today(n)
    state = _load_state(path)
    if state.get("date") != date:
        state = {"date": date, "posted": {}}
    state.setdefault("posted", {})[str(channel_index)] = {
        "time_et": n.strftime("%H:%M"), "video_id": video_id,
    }
    with open(path or STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def to_utc_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gate_cli():
    due, reasons = due_channels()
    for r in reasons:
        print(f"[gate] {r}")
    plan = {str(ch): to_utc_iso(t) for ch, t in due.items()}
    print(f"[gate] go={bool(plan)} plan={plan}")
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"go={'true' if plan else 'false'}\n")
            f.write(f"plan={json.dumps(plan)}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "gate":
        _gate_cli()
    else:
        # preview: python -m modules.post_schedule
        n = _now()
        print("ET now:", n.strftime("%Y-%m-%d %H:%M"))
        for ch, m in sorted(day_schedule(_today(n)).items()):
            print(f"  channel {ch + 1}: {_at(_today(n), m).strftime('%H:%M')} ET")
        print(due_channels()[1])
