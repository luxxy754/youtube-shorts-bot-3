"""
Publish-time planner.

Har run har channel ke liye ek video banata hai aur use YouTube par SCHEDULE karta hai
(publishAt). Publish time fixed nahi hota: har weekday ka apna prime-time window hai,
us window ke andar har channel ka time random (date se seeded) nikalta hai, aur do
channels kabhi ek dosre ke 40+ minute ke andar nahi aate.

Sab windows PKT (UTC+5) mein hain. Audience: Pakistan + India (IST = PKT + 30 min).
Weekday: 0=Mon ... 6=Sun
"""

import os
import random
from datetime import datetime, timedelta, timezone

PKT = timezone(timedelta(hours=5))

# slot "A" = dopehar / lunch-break, slot "B" = evening prime time.  (start_h:m, end_h:m)
SLOT_WINDOWS = {
    "A": {
        0: ("12:10", "13:40"),   # Mon
        1: ("13:00", "14:30"),   # Tue
        2: ("11:30", "13:00"),   # Wed
        3: ("12:30", "14:15"),   # Thu
        4: ("10:00", "11:50"),   # Fri  (Jummah se pehle - 12 se 2:30 skip)
        5: ("11:00", "13:15"),   # Sat
        6: ("10:30", "12:45"),   # Sun
    },
    "B": {
        0: ("19:00", "21:10"),   # Mon
        1: ("20:00", "22:15"),   # Tue
        2: ("18:30", "20:40"),   # Wed
        3: ("20:30", "22:40"),   # Thu
        4: ("18:15", "20:30"),   # Fri  (Jummah ke baad)
        5: ("20:00", "22:45"),   # Sat
        6: ("17:00", "19:45"),   # Sun
    },
}

MIN_GAP_MIN = 40        # ek hi slot mein do channels ke beech kam se kam gap
MIN_LEAD_MIN = 25       # publish time "ab" se kam se kam itna aage hona chahiye

# GitHub cron string -> slot.  (run.yml ke cron lines se match karna chahiye)
CRON_TO_SLOT = {
    "7 2 * * *": "A",
    "23 9 * * *": "B",
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

    # chhoti window mein 3 channels fit karne ke liye gap automatically ghata do
    gap_min = max(15, min(MIN_GAP_MIN, (end_m - start_m) // max(1, n_channels) - 3))

    chosen = []
    for _ in range(n_channels):
        pick = None
        for _try in range(200):
            minute = rng.randint(start_m, end_m)
            # round numbers (:00 / :30) se bacho - thoda "human" lage
            if minute % 30 == 0:
                minute += rng.choice([-3, -2, 2, 3, 4, 7])
            cand = midnight + timedelta(minutes=minute)
            if cand < earliest:
                continue
            if all(abs((cand - c).total_seconds()) >= gap_min * 60 for c in chosen):
                pick = cand
                break
        if pick is None:
            # window nikal gayi (run late hua) ya jagah nahi bachi -> last chosen ke baad stagger
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
