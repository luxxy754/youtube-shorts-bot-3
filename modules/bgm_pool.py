"""
Per-video BGM picker with per-channel rotation.

Why this exists
---------------
The old code did random.choice(files) from assets/bgm/. With 3 channels
running on the same day, two channels could easily pick the SAME track,
and small pools meant listeners heard the same song several times a week.
This module:

  * picks a track that has NOT been used recently by ANY channel (LRU across
    a small JSON state file, so it works across GitHub Actions runs),
  * prefers tracks whose filename starts with the channel's prefix
    (e.g. ch0_, ch1_, ch2_) so each channel has its own flavour,
  * falls back to any unused track if the channel has no matching prefix,
  * and if every track was used recently it just picks the least-recently-used
    one instead of a fresh repeat.

Also exposes gain windows so SFX / voice levels can vary per video without
touching the audio module.

Legality note: this module only reads files. It does NOT download anything.
Use ROYALTY-FREE / CC0 tracks. Do NOT use tracks from the YouTube Audio
Library if the channel is not verified - those only work inside YouTube's
own uploader, not via the API.
"""

import json
import os
import random
import re

BGM_DIRS = [
    os.path.join("assets", "bgm"),
    os.path.join("modules", "bgm"),
]

STATE_FILE = os.path.join("assets", "bgm_state.json")

AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".aac")


def _list_tracks():
    tracks = []
    for d in BGM_DIRS:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(AUDIO_EXTS):
                tracks.append(os.path.join(d, f))
    return tracks


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"history": []}   # most recent last


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception as e:
        print(f"[bgm] state save failed: {e}")


def _channel_prefix_match(path, channel_index):
    """Does the file name look like it belongs to this channel?"""
    name = os.path.basename(path).lower()
    keys = [f"ch{channel_index}", f"channel{channel_index + 1}",
            f"c{channel_index + 1}_"]
    return any(k in name for k in keys)


def pick_bgm(channel_index, max_history=8):
    """
    Return a path to a background track for this channel, or None if the pool
    is empty. Uses a tiny LRU so the same track is not picked twice in a row
    across the whole pool, and prefers the channel's own prefix when present.
    """
    tracks = _list_tracks()
    if not tracks:
        print("[bgm] pool empty - no background music will be used")
        return None

    state = _load_state()
    history = state.get("history", [])[-max_history:]
    recent_set = set(history)

    preferred = [t for t in tracks if _channel_prefix_match(t, channel_index)]
    fallback_pool = preferred or tracks

    unused_preferred = [t for t in fallback_pool if t not in recent_set]
    if unused_preferred:
        pick = random.choice(unused_preferred)
    else:
        # Everything used recently - pick least-recently-used in the whole pool
        ordered = sorted(tracks, key=lambda t: history.index(t) if t in history
                         else -1)
        pick = ordered[0] if ordered else random.choice(tracks)

    history.append(pick)
    state["history"] = history[-max_history:]
    state["last_channel"] = channel_index
    _save_state(state)

    print(f"[bgm] ch{channel_index + 1} -> {os.path.basename(pick)} "
          f"(pool={len(tracks)}, preferred={len(preferred)})")
    return pick


def sfx_gain_window(style):
    """
    Small helper other modules can call if they want a per-video SFX level.
    Keeps the actual numbers inside the style dict when available, otherwise
    falls back to safe defaults.
    """
    style = style or {}
    return {
        "whoosh": style.get("sfx_whoosh_gain", 0.5),
        "pop":    style.get("sfx_pop_gain", 0.18),
        "click":  style.get("sfx_click_gain", 0.33),
    }
