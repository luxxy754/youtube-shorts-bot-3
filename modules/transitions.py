"""
Randomized scene-to-scene transitions.

Why
---
Hard cuts every single scene is one of the loudest "template" signals a video
can send. Real editors mix cuts with a handful of transitions - a wipe here,
a slide there, occasionally just a hard cut. This module gives every video its
OWN small palette of transition styles so joins stop looking mechanical.

What it does
------------
- `pick_transitions(n_joins, style)` returns a list of `n_joins` transition
  names (or None for a hard cut), drawn from a per-video palette.
- `apply_transitions_ffmpeg(...)` concatenates pre-rendered scene .mp4 files
  with ffmpeg's xfade filter. Fast, GPU-free, high quality.
"""

import os
import random
import subprocess

XFADE_MODES = [
    "fade",
    "wipeleft",
    "wiperight",
    "wipeup",
    "wipedown",
    "slideleft",
    "slideright",
    "slideup",
    "slidedown",
    "circleopen",
    "circleclose",
    "radial",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "dissolve",
    "pixelize",
    "distance",
    "fadeblack",
]

PALETTE_SIZES = [3, 4, 4, 5, 5, 6]
TRANSITION_DURATIONS = [0.18, 0.22, 0.26, 0.30, 0.35]


def pick_palette(style, seed=None):
    """Per-video transition palette, biased by the video's pacing."""
    rnd = random.Random(seed if seed is not None else
                        hash(str(style.get("caption_y_ratio", 0.62))) ^
                        int(style.get("scene_gap", 0.05) * 1000))
    size = rnd.choice(PALETTE_SIZES)
    pool = list(XFADE_MODES)
    rnd.shuffle(pool)

    bias = style.get("transition_palette_bias", "medium")
    if bias == "fast":
        want = ("wipe", "slide", "smooth")
    elif bias == "slow":
        want = ("fade", "dissolve", "distance", "fadeblack",
                "circleopen", "circleclose", "radial")
    else:
        want = tuple(XFADE_MODES)
    biased = [m for m in pool if m.startswith(want)] or pool
    palette = biased[:size]
    print("[transitions] palette: " + ", ".join(palette) +
          f" | bias={bias} | density={style.get('transition_density', 0.55):.2f}")
    return palette


def pick_transitions(n_joins, style, seed=None):
    """
    Return a list of length `n_joins`. Each item is either a mode name from
    XFADE_MODES or None (= hard cut).
    """
    if n_joins <= 0:
        return []
    density = style.get("transition_density", 0.55)   # xfade probability
    hard_weight = 1.0 - density
    palette = pick_palette(style, seed=seed)
    rnd = random.Random((seed if seed is not None else 0) ^
                        int(style.get("caption_rotation", 0) * 100) ^
                        int(style.get("hook_tilt", 0) * 1000))
    out = []
    last = None
    for _ in range(n_joins):
        if rnd.random() < hard_weight:
            out.append(None)
            last = None
            continue
        choices = [m for m in palette if m != last] or palette
        pick = rnd.choice(choices)
        out.append(pick)
        last = pick
    return out


def pick_duration(style, seed=None):
    rnd = random.Random((seed or 0) ^ int(style.get("grain_strength", 4) * 37))
    return rnd.choice(TRANSITION_DURATIONS)


def _fmt(x):
    return ("%.3f" % x).rstrip("0").rstrip(".")


def apply_transitions_ffmpeg(scene_paths, durations, transitions,
                             scene_gap, out_path, transition_time=None):
    """
    Concatenate pre-rendered scene .mp4 files with ffmpeg's xfade filter.

    scene_paths : list[str]   already 1080x1920, 30fps, WITH audio
    durations   : list[float] exact length of each scene file in seconds
    transitions : list[str|None] one per join (len == len(scene_paths)-1)
    scene_gap   : float       silent padding baked into each scene already
    out_path    : str         where to write the concatenated mp4

    Returns True on success. Never raises.
    """
    n = len(scene_paths)
    if n < 2:
        return False
    if len(transitions) != n - 1:
        return False

    t_len = transition_time or 0.24

    inputs = []
    for p in scene_paths:
        inputs += ["-i", p]

    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]fps=30,setsar=1,format=yuv420p[v{i}]")

    cur_v = "v0"
    cur_a = "0:a"
    total_offset = durations[0]

    for i, mode in enumerate(transitions):
        next_v = f"v{i+1}"
        next_a = f"{i+1}:a"

        if mode is None:
            real_mode = "fade"
            real_t = 0.04
        else:
            real_mode = mode
            real_t = t_len

        out_v = f"xv{i}"
        out_a = f"xa{i}"
        offset = total_offset - real_t

        parts.append(
            f"[{cur_v}][{next_v}]xfade=transition={real_mode}:"
            f"duration={real_t}:offset={_fmt(offset)}[{out_v}]"
        )
        parts.append(
            f"[{cur_a}][{next_a}]acrossfade=d={real_t}:c1=tri:c2=tri[{out_a}]"
        )

        cur_v = out_v
        cur_a = out_a
        total_offset = offset + real_t + (durations[i + 1] - real_t)

    graph = ";".join(parts)

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", graph,
           "-map", f"[{cur_v}]", "-map", f"[{cur_a}]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           "-c:a", "aac", "-b:a", "192k",
           out_path]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(out_path) or \
                os.path.getsize(out_path) < 10000:
            print("[transitions] ffmpeg xfade failed:")
            print((r.stderr or "")[-1200:])
            return False
        n_cuts = sum(1 for t in transitions if t is None)
        n_fades = n - 1 - n_cuts
        print(f"[transitions] applied: {n_fades} xfades, {n_cuts} hard cuts")
        return True
    except Exception as e:
        print("[transitions] error: " + str(e))
        return False
