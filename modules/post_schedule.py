"""
Per-channel daily scheduler - ONE post per channel per day, random hour.

Design:
  * Each channel has its own random target hour per ET date
    (hash-based, so all wake-ups in a day agree on the same hour).
  * A workflow can be scheduled every hour; the `gate` command decides
    whether each channel should post *now*.
  * Per-channel state file tracks last_post_date_et_<channel> so each
    channel posts exactly ONCE per day.
  * Random minute delay (0-45 min) is added by the workflow itself so
    the upload minute is also random.

Env:
  POST_WINDOW_START    default 10  (10:00 ET)
  POST_WINDOW_END      default 22  (22:00 ET, exclusive)
  POST_MAX_DELAY_MIN   default 45
  POST_TZ              default America/New_York
  SCHEDULE_SALT        any text (private seed; put in GitHub Secrets)

CLI:
  python3 modules/post_schedule.py gate                       -> prints go/delay for whole run
  python3 modules/post_schedule.py channel_gate <index> <name> -> prints go for ONE channel
"""

import hashlib
import json
import os
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

STATE_FILE = os.getenv("POST_STATE_FILE", "post_state.json")
TZ = ZoneInfo(os.getenv("POST_TZ", "America/New_York"))
WINDOW_START = int(os.getenv("POST_WINDOW_START", "10"))
WINDOW_END = int(os.getenv("POST_WINDOW_END", "22"))
MAX_DELAY_MIN = int(os.getenv("POST_MAX_DELAY_MIN", "45"))
SALT = os.getenv("SCHEDULE_SALT") or os.getenv("GITHUB_REPOSITORY") or "shorts"


def _now(now=None):
    return (now or datetime.now(TZ)).astimezone(TZ)


def _today(now=None):
    return _now(now).strftime("%Y-%m-%d")


def _target_hour_for_channel(date_str, channel_index):
    """
    Deterministic-but-unpredictable target hour for a given date+channel.
    Same date + same channel index -> same hour all day.
    Different channels -> different hours.
    """
    seed_text = f"{SALT}:{date_str}:ch{channel_index}"
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest(), 16)
    rnd = random.Random(seed)
    # avoid two channels getting the same hour by giving each a small slice
    span = WINDOW_END - WINDOW_START
    if span < 1:
        return WINDOW_START
    return WINDOW_START + rnd.randrange(span)


def _load_state(path=None):
    try:
        with open(path or STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data, path=None):
    try:
        with open(path or STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[schedule] state save failed: {e}")


def _channel_posted_today(state, channel_name, today):
    key = f"last_post_date_et_{channel_name}"
    return state.get(key) == today


def channel_should_post_now(channel_index, channel_name, now=None, path=None):
    """
    For a single channel: returns (go: bool, reason: str).
    - Already posted today -> False.
    - Current ET hour == target hour -> True.
    - Current ET hour > target hour AND not yet posted today AND still in window -> True (catch up).
    - Otherwise -> False.
    """
    n = _now(now)
    today = _today(n)
    state = _load_state(path)
    if _channel_posted_today(state, channel_name, today):
        return False, f"{channel_name}: already posted today"

    target = _target_hour_for_channel(today, channel_index)

    if not (WINDOW_START <= n.hour < WINDOW_END):
        return False, (f"{channel_name}: outside window (ET hour {n.hour}, "
                       f"target {target})")
    if n.hour == target:
        return True, f"{channel_name}: target hour {target} ET reached"
    if n.hour > target:
        return True, (f"{channel_name}: target hour {target} ET passed, "
                      f"catching up (now {n.hour})")
    return False, f"{channel_name}: waiting (target {target}, now {n.hour})"


def mark_channel_posted(channel_name, video_id="", now=None, path=None):
    """Record a successful post for one channel."""
    n = _now(now)
    state = _load_state(path)
    today = _today(n)
    state[f"last_post_date_et_{channel_name}"] = today
    state[f"last_post_time_et_{channel_name}"] = n.strftime("%H:%M")
    state[f"last_video_id_{channel_name}"] = video_id
    history = state.get("history", [])
    history.append({
        "date": today,
        "time": n.strftime("%H:%M"),
        "channel": channel_name,
        "video_id": video_id,
    })
    state["history"] = history[-120:]
    _save_state(state, path)


def mark_posted(video_id="", now=None, path=None, channel_name=None):
    """Backward-compat wrapper: routes to mark_channel_posted when a name is given."""
    if channel_name:
        return mark_channel_posted(channel_name, video_id, now, path)
    n = _now(now)
    state = _load_state(path)
    state["last_post_date_et"] = _today(n)
    state["last_post_time_et"] = n.strftime("%H:%M")
    state["video_id"] = video_id
    _save_state(state, path)


# ---------------------------------------------------------------- CLI ----
def _gate_cli():
    """
    Overall gate (used to decide whether the workflow should proceed).
    Returns go=True whenever ANY channel could still post today.
    """
    manual = os.getenv("EVENT_NAME", "") == "workflow_dispatch"
    if manual:
        print("[gate] manual run -> go=True")
        _write_output("true", 0)
        return

    n = _now()
    today = _today(n)
    state = _load_state()

    # Which channels are configured?
    channel_names = ["Channel 1"]
    for i in range(2, 11):
        if os.getenv(f"YOUTUBE_REFRESH_TOKEN_{i}"):
            channel_names.append(f"Channel {i}")

    any_go = False
    any_reason = ""
    for i, name in enumerate(channel_names):
        go, reason = channel_should_post_now(i, name, now=n)
        print(f"[gate] ch{i} ({name}): go={go} :: {reason}")
        if go:
            any_go = True
            any_reason = reason
            break

    delay = random.randint(0, MAX_DELAY_MIN * 60) if any_go else 0
    if not any_reason:
        any_reason = "no channel ready"
    print(f"[gate] overall go={any_go} delay={delay}s :: {any_reason}")
    _write_output("true" if any_go else "false", delay)


def _channel_gate_cli():
    """
    Per-channel gate. Called as: python3 modules/post_schedule.py channel_gate <index> <name>
    Prints go/delay to GITHUB_OUTPUT.
    """
    if len(sys.argv) < 4:
        print("[channel_gate] usage: channel_gate <index> <name>")
        _write_output("false", 0)
        return
    try:
        index = int(sys.argv[2])
    except ValueError:
        index = 0
    name = sys.argv[3]

    manual = os.getenv("EVENT_NAME", "") == "workflow_dispatch"
    if manual:
        print(f"[channel_gate] ch{index} ({name}): manual -> go=True")
        _write_output("true", 0)
        return

    go, reason = channel_should_post_now(index, name)
    delay = random.randint(0, MAX_DELAY_MIN * 60) if go else 0
    print(f"[channel_gate] ch{index} ({name}): go={go} delay={delay}s :: {reason}")
    _write_output("true" if go else "false", delay)


def _write_output(go_str, delay):
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        try:
            with open(out, "a") as f:
                f.write(f"go={go_str}\ndelay={delay}\n")
        except Exception as e:
            print(f"[schedule] could not write GITHUB_OUTPUT: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "gate":
        _gate_cli()
    elif len(sys.argv) > 1 and sys.argv[1] == "channel_gate":
        _channel_gate_cli()
    else:
        n = _now()
        print(f"ET now: {n.strftime('%Y-%m-%d %H:%M')}")
        for i in range(3):
            name = f"Channel {i+1}"
            target = _target_hour_for_channel(_today(n), i)
            state = _load_state()
            posted = _channel_posted_today(state, name, _today(n))
            print(f"  ch{i} {name}: target={target}:00 ET, posted_today={posted}")
