"""
Publish-time planner.

Har run har channel ke liye ek video banata hai aur use YouTube par SCHEDULE karta hai
(publishAt). Publish time fixed nahi hota: har weekday ka apna prime-time window hai,
us window ke andar har channel ka time random (date se seeded) nikalta hai, aur do
channels kabhi ek dosre ke 40+ minute ke andar nahi aate.

NAYA: Slots ab US trending hours ke hisaab se set hain (US ET audience ke liye best time):
  - Slot A: ~16:00-19:00 PKT  ==  6:00-9:00 AM ET   (US morning trending)
  - Slot B: ~04:00-08:00 PKT  ==  6:00-10:00 PM ET  (US evening prime time, prev day)

Sab windows PKT (UTC+5) mein hain.
Weekday: 0=Mon ... 6=Sun
"""

import os
import random
from datetime import datetime, timedelta, timezone

PKT = timezone(timedelta(hours=5))

# slot "A" = US morning (US ET 6-9 AM), slot "B" = US evening prime time (US ET 6-10 PM).
SLOT_WINDOWS = {
    "A": {
        0: ("16:00", "19:00"),   # Mon  -> 6-9 AM ET
        1: ("16:30", "19:30"),   # Tue
        2: ("15:30", "18:30"),   # Wed
        3: ("16:00", "19:00"),   # Thu
        4: ("15:00", "18:00"),   # Fri
        5: ("16:00", "19:00"),   # Sat
        6: ("15:30", "18:30"),   # Sun
    },
    "B": {
        0: ("04:00", "08:00"),   # Mon  -> 6-10 PM ET (prev day)
        1: ("04:30", "08:30"),   # Tue
        2: ("03:30", "07:30"),   # Wed
        3: ("04:00", "08:00"),   # Thu
        4: ("03:00", "07:00"),   # Fri
        5: ("04:00", "08:00"),   # Sat
        6: ("03:30", "07:30"),   # Sun
    },
}

MIN_GAP_MIN = 40
MIN_LEAD_MIN = 25

# GitHub cron string -> slot.  (run.yml ke cron lines se match karna chahiye)
# PKT = UTC+5, to:
#   16:00 PKT = 11:00 UTC  -> Slot A
#   04:00 PKT = 23:00 UTC  -> Slot B
CRON_TO_SLOT = {
    "0 11 * * *": "A",
    "0 23 * * *": "B",
}


def slot_from_env():
    """Return 'A' / 'B' for scheduled runs, None for manual runs (=> publish now)."""
    if os.getenv("PUBLISH_NOW", "").lower() in ("1", "true", "yes"):
        return None
    cron = (os.getenv("SCHEDULE_CRON") or "").strip()
    if not cron:
        return None
    return CRON_TO_SLOT.get(cron)


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def plan_publish_times(n_channels, slot, now_utc=None):
    """
    Return a list of ISO-8601 UTC strings (len = n_channels), one per channel.
    The list is shuffled per day so channel 1 is not always the first one to go live.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_pkt = now_utc.astimezone(PKT)
    day = now_pkt.date()
    rng = random.Random(f"{day.isoformat()}-{slot}-{n_channels}")

    start_s, end_s = SLOT_WINDOWS[slot][day.weekday()]
    start_m, end_m = _hm(start_s), _hm(end_s)
    midnight = datetime(day.year, day.month, day.day, tzinfo=PKT)
    earliest = now_pkt + timedelta(minutes=MIN_LEAD_MIN)

    gap_min = max(15, min(MIN_GAP_MIN, (end_m - start_m) // max(1, n_channels) - 3))

    chosen = []
    for _ in range(n_channels):
        pick = None
        for _try in range(200):
            minute = rng.randint(start_m, end_m)
            if minute % 30 == 0:
                minute += rng.choice([-3, -2, 2, 3, 4, 7])
            cand = midnight + timedelta(minutes=minute)
            if cand < earliest:
                continue
            if all(abs((cand - c).total_seconds()) >= gap_min * 60 for c in chosen):
                pick = cand
                break
        if pick is None:
            base = max([earliest] + chosen)
            pick = base + timedelta(minutes=MIN_GAP_MIN + rng.randint(3, 25))
        chosen.append(pick)

    rng.shuffle(chosen)
    return [c.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") for c in chosen]


if __name__ == "__main__":
    for slot in ("A", "B"):
        for wd in range(7):
            fake = datetime(2026, 10, 5 + wd, 1 if slot == "A" else 8, 0, tzinfo=timezone.utc)
            times = plan_publish_times(3, slot, fake)
            pk = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                  .astimezone(PKT).strftime("%a %H:%M") for t in times]
            print(slot, wd, pk)
