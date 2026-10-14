"""
Randomized scene-to-scene transitions - FULL ADVANCED PACK.

Design:
  * NO fixed order. NO fixed sequence. Every video picks a random palette.
  * 5 categories: native xfade, zoom, glitch, light, stylized.
  * Each transition carries its own SFX (from transition_sfx.py).
  * Returns dict with cuts + matched SFX so the composer can layer them.

Apply returns:
  {
    "ok": bool,
    "cuts": [t_sec, ...],
    "transition_sfx": [
       {"t": t_sec, "mode": str, "kind": str, "audio": np.ndarray, "gain": g},
       ...
    ],
  }
"""

import os
import random
import re
import subprocess

import numpy as np

from modules.transition_sfx import build_transition_sfx, _mode_kind


# ============================================================
# ALL TRANSITION MODES
# ============================================================
XFADE_MODES = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circleopen", "circleclose", "radial",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "dissolve", "pixelize", "distance", "fadeblack", "fadewhite",
    "horzopen", "horzclose", "vertopen", "vertclose",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice",
]

ZOOM_MODES = ["whipzoom_in", "whipzoom_out", "punchzoom", "spinzoom"]
GLITCH_MODES = ["rgbsplit", "scanline", "pixelsort", "datamosh"]
LIGHT_MODES = ["lightleak", "filmburn", "flash_white", "flash_color"]
STYLE_MODES = ["papershuffle", "inkdrip", "spin", "shake", "zoomblur"]

ALL_MODES = XFADE_MODES + ZOOM_MODES + GLITCH_MODES + LIGHT_MODES + STYLE_MODES
CUSTOM_MODES = set(ZOOM_MODES + GLITCH_MODES + LIGHT_MODES + STYLE_MODES)

# Modes that MUST appear in every palette (the "hero" effects)
MUST_HAVE_MODES = ["papershuffle"]

PALETTE_SIZES = [5, 6, 7, 8]
TRANSITION_DURATIONS = [0.20, 0.24, 0.28, 0.32, 0.36, 0.42]

# Per-channel bias: Channel 1 fast, Channel 2 cinematic, Channel 3 vintage.
CHANNEL_BIAS = {
    0: "fast",
    1: "slow",
    2: "medium",
}

CATEGORY_POOL = {
    "fast":   ["native_fast", "glitch", "zoom", "style"],
    "medium": ["native_all", "zoom", "light", "style", "glitch"],
    "slow":   ["native_slow", "light", "style", "glitch", "zoom"],
}

NATIVE_FAST_PREFIX = ("wipe", "slide", "smooth", "diag", "hl", "hr", "vu", "vd")
NATIVE_SLOW_MODES = ("fade", "dissolve", "distance", "fadeblack", "fadewhite",
                     "circleopen", "circleclose", "radial", "horz", "vert")


def _native_fast():
    return [m for m in XFADE_MODES if m.startswith(NATIVE_FAST_PREFIX)]


def _native_slow():
    return [m for m in XFADE_MODES if m in NATIVE_SLOW_MODES]


def _native_all():
    return list(XFADE_MODES)


def _category_modes(cat):
    if cat == "native_fast":
        return _native_fast()
    if cat == "native_slow":
        return _native_slow()
    if cat == "native_all":
        return _native_all()
    if cat == "zoom":
        return list(ZOOM_MODES)
    if cat == "glitch":
        return list(GLITCH_MODES)
    if cat == "light":
        return list(LIGHT_MODES)
    if cat == "style":
        return list(STYLE_MODES)
    return _native_all()


def pick_palette(style, seed=None):
    if seed is None:
        seed = random.SystemRandom().randrange(0, 2**32)
    rnd = random.Random(seed)

    channel_idx = style.get("channel_index", 0)
    bias = CHANNEL_BIAS.get(channel_idx, style.get("transition_palette_bias", "medium"))
    style["transition_palette_bias"] = bias

    size = rnd.choice(PALETTE_SIZES)
    cats = CATEGORY_POOL.get(bias, CATEGORY_POOL["medium"])

    # Build separate pools for native vs custom modes
    native_pool = []
    custom_pool = []
    for c in cats:
        if c.startswith("native"):
            native_pool += _category_modes(c)
        else:
            custom_pool += _category_modes(c)

    # ALWAYS add every style/glitch/light/zoom mode to the custom pool so
    # papershuffle and friends can never be excluded by category bias.
    for m in ZOOM_MODES + GLITCH_MODES + LIGHT_MODES + STYLE_MODES:
        if m not in custom_pool:
            custom_pool.append(m)

    rnd.shuffle(native_pool)
    rnd.shuffle(custom_pool)

    # Reserve slots: at least 2 custom, at least 1 native, plus must-have modes
    n_custom = max(2, size - 3)
    n_native = max(1, size - n_custom)

    palette = []
    seen = set()

    # 1) Must-have modes first (papershuffle etc.)
    for m in MUST_HAVE_MODES:
        if m not in seen:
            palette.append(m)
            seen.add(m)

    # 2) Fill custom slots
    for m in custom_pool:
        if len(palette) >= size:
            break
        if m not in seen:
            palette.append(m)
            seen.add(m)

    # 3) Fill native slots
    for m in native_pool:
        if len(palette) >= size:
            break
        if m not in seen:
            palette.append(m)
            seen.add(m)

    # 4) If still short, pull from ALL_MODES
    if len(palette) < size:
        leftovers = [m for m in ALL_MODES if m not in seen]
        rnd.shuffle(leftovers)
        for m in leftovers:
            if len(palette) >= size:
                break
            palette.append(m)
            seen.add(m)

    rnd.shuffle(palette)
    print("[transitions] palette: " + ", ".join(palette) +
          f" | bias={bias} | density={style.get('transition_density', 0.80):.2f} "
          f"| size={size}")
    return palette


def pick_transitions(n_joins, style, seed=None):
    """
    Returns list of length n_joins. Each item is a mode name OR None (hard cut).
    Same mode appears at most once per video for max variety.
    Papershuffle is guaranteed to be used at least once if n_joins >= 3.
    """
    if n_joins <= 0:
        return []

    density = float(style.get("transition_density", 0.80))
    # Clamp: at least 82% xfade, at most 100%
    density = max(0.82, min(1.0, density))
    hard_weight = 1.0 - density

    palette = pick_palette(style, seed=seed)

    if seed is None:
        seed = random.SystemRandom().randrange(0, 2**32)
    rnd = random.Random(seed ^
                        int(style.get("caption_rotation", 0) * 100) ^
                        int(style.get("hook_tilt", 0) * 1000))

    used_once = set()
    out = []
    last = None

    # Force must-have modes into the output if there are enough joins
    forced = []
    if n_joins >= 3:
        for m in MUST_HAVE_MODES:
            if m in palette:
                forced.append(m)

    for i in range(n_joins):
        # Place forced modes in the first half of the video
        if forced and i < max(1, n_joins // 2):
            pick = forced.pop(0)
            if pick != last:
                out.append(pick)
                used_once.add(pick)
                last = pick
                continue

        if rnd.random() < hard_weight and not forced:
            out.append(None)
            last = None
            continue

        fresh = [m for m in palette if m not in used_once and m != last]
        if not fresh:
            fresh = [m for m in palette if m != last] or palette
        pick = rnd.choice(fresh)
        out.append(pick)
        used_once.add(pick)
        last = pick

    return out


def pick_duration(style, seed=None):
    if seed is None:
        seed = random.SystemRandom().randrange(0, 2**32)
    rnd = random.Random(seed ^ int(style.get("grain_strength", 4) * 37))
    return rnd.choice(TRANSITION_DURATIONS)


# ============================================================
# FILTER BUILDERS
# ============================================================
def _fmt(x):
    return ("%.3f" % x).rstrip("0").rstrip(".")


def _native_filter(mode, t):
    return f"xfade=transition={mode}:duration={_fmt(t)}:offset=%OFFSET%"


def _join_filters(mode, t_len):
    """Return (pre_a, pre_b, xfade_filter_with_%OFFSET%)."""
    if mode is None:
        return "", "", "xfade=transition=fade:duration=0.04:offset=%OFFSET%"

    if mode in XFADE_MODES:
        return "", "", _native_filter(mode, t_len)

    # ---- ZOOM ----
    if mode == "whipzoom_in":
        pre_a = (f"scale=iw*'1+0.5*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1+0.5*min(t/{_fmt(t_len)},1)':eval=frame,"
                 "crop=1080:1920,gblur=sigma=16")
        pre_b = (f"scale=iw*'1.5-0.5*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1.5-0.5*min(t/{_fmt(t_len)},1)':eval=frame,"
                 "crop=1080:1920,gblur=sigma=16")
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "whipzoom_out":
        pre_a = (f"scale=iw*'1.5-0.5*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1.5-0.5*min(t/{_fmt(t_len)},1)':eval=frame,"
                 "crop=1080:1920,gblur=sigma=16")
        pre_b = (f"scale=iw*'1+0.5*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1+0.5*min(t/{_fmt(t_len)},1)':eval=frame,"
                 "crop=1080:1920,gblur=sigma=16")
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "punchzoom":
        pre_a = (f"scale=iw*'1+0.35*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1+0.35*min(t/{_fmt(t_len)},1)':eval=frame,crop=1080:1920")
        pre_b = (f"scale=iw*'1.35-0.35*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1.35-0.35*min(t/{_fmt(t_len)},1)':eval=frame,crop=1080:1920")
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "spinzoom":
        pre_a = (f"scale=iw*'1+0.3*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1+0.3*min(t/{_fmt(t_len)},1)':eval=frame,crop=1080:1920,"
                 f"rotate='0.3*min(t/{_fmt(t_len)},1)':fillcolor=black")
        pre_b = (f"scale=iw*'1.3-0.3*min(t/{_fmt(t_len)},1)':"
                 f"ih*'1.3-0.3*min(t/{_fmt(t_len)},1)':eval=frame,crop=1080:1920,"
                 f"rotate='-0.3*min(t/{_fmt(t_len)},1)':fillcolor=black")
        return pre_a, pre_b, _native_filter("fade", t_len)

    # ---- GLITCH ----
    if mode == "rgbsplit":
        pre_a = "rgbashift=rh=-10:bh=10:gv=3"
        return pre_a, "", _native_filter("fade", t_len * 0.7)

    if mode == "scanline":
        pre_a = "noise=alls=40:allf=t+u,eq=brightness=0.05"
        pre_b = "noise=alls=40:allf=t+u"
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "pixelsort":
        pre_a = "scale=64:114:flags=neighbor,scale=1080:1920:flags=neighbor"
        pre_b = "scale=64:114:flags=neighbor,scale=1080:1920:flags=neighbor"
        return pre_a, pre_b, _native_filter("fade", t_len * 0.7)

    if mode == "datamosh":
        pre_a = "tblend=all_mode=difference,eq=contrast=1.3"
        return pre_a, "", _native_filter("fade", t_len)

    # ---- LIGHT ----
    if mode == "lightleak":
        pre_a = "colorbalance=rs=0.3:gs=0.1:bs=-0.2"
        pre_b = "colorbalance=rs=0.2:gs=0.05:bs=-0.15"
        return pre_a, pre_b, _native_filter("fade", t_len * 1.4)

    if mode == "filmburn":
        pre_a = "eq=brightness=0.15:contrast=1.2,vignette=angle=PI/3"
        pre_b = "eq=brightness=0.10,vignette=angle=PI/4"
        return pre_a, pre_b, _native_filter("fadewhite", t_len * 0.8)

    if mode == "flash_white":
        return "", "", _native_filter("fadewhite", t_len * 0.5)

    if mode == "flash_color":
        pre_a = "eq=saturation=1.5"
        return pre_a, "", _native_filter("fadewhite", t_len * 0.5)

    # ---- STYLE ----
    if mode == "papershuffle":
        # Paper shuffle gets its own visual: a horizontal slice wipe
        # combined with a slight brightness shift, so it does NOT look
        # like a generic native xfade. SFX = paper rustle (in transition_sfx.py).
        pre_a = "eq=brightness=0.04"
        pre_b = "eq=brightness=-0.02"
        return pre_a, pre_b, _native_filter("hlslice", t_len * 1.1)

    if mode == "inkdrip":
        pre_a = "eq=brightness=-0.1:contrast=1.15"
        pre_b = "eq=brightness=-0.05"
        return pre_a, pre_b, _native_filter("circleopen", t_len * 1.3)

    if mode == "spin":
        pre_a = f"rotate='2*PI*min(t/{_fmt(t_len)},1)':fillcolor=black"
        pre_b = f"rotate='-2*PI*min(t/{_fmt(t_len)},1)':fillcolor=black"
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "shake":
        pre_a = ("crop=iw*0.95:ih*0.95:"
                 "'(iw-ow)/2+6*sin(50*t)':'(ih-oh)/2+6*cos(60*t)',scale=1080:1920")
        pre_b = ("crop=iw*0.95:ih*0.95:"
                 "'(iw-ow)/2+6*sin(50*t+3.14)':'(ih-oh)/2+6*cos(60*t+3.14)',"
                 "scale=1080:1920")
        return pre_a, pre_b, _native_filter("fade", t_len)

    if mode == "zoomblur":
        pre_a = "gblur=sigma=20"
        pre_b = "gblur=sigma=20"
        return pre_a, pre_b, _native_filter("fade", t_len)

    return "", "", _native_filter("fade", t_len)


# ============================================================
# MAIN APPLY
# ============================================================
def _safe_mode(mode):
    """Map any custom transition to a native xfade that FFmpeg definitely supports."""
    if mode in XFADE_MODES:
        return mode
    mapping = {
        "papershuffle": "hlslice", "inkdrip": "circleopen", "spin": "radial",
        "shake": "slideleft", "zoomblur": "fade", "whipzoom_in": "zoomin",
        "whipzoom_out": "fade", "punchzoom": "zoomin", "spinzoom": "radial",
        "rgbsplit": "pixelize", "scanline": "fade", "pixelsort": "pixelize",
        "datamosh": "pixelize", "lightleak": "fadewhite", "filmburn": "fadewhite",
        "flash_white": "fadewhite", "flash_color": "fadewhite",
    }
    return mapping.get(mode, "fade")


def _try_ffmpeg(cmd, out_path, label=""):
    """Run FFmpeg, return True on success."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        print(f"[transitions] {label} ffmpeg error: {e}")
        return False
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
        print(f"[transitions] {label} ffmpeg failed:")
        print((r.stderr or "")[-1500:])
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError:
            pass
        return False
    return True


def apply_transitions_ffmpeg(scene_paths, durations, transitions,
                             scene_gap, out_path, transition_time=None,
                             style=None):
    """
    Join scenes with randomized transitions, matching SFX, and safe retry.

    Strategy:
      1) Try the full custom filter graph.
      2) If it fails, find the individual joins that fail and replace ONLY those
         with native equivalents. All other joins keep their custom effect.
      3) As a last resort, fall back to all-native.

    Returns dict: {ok, cuts, transition_sfx}
    """
    result = {"ok": False, "cuts": [], "transition_sfx": []}
    n = len(scene_paths)
    if n < 2 or len(transitions) != n - 1 or len(durations) != n:
        return result
    if any(not os.path.isfile(p) for p in scene_paths):
        print("[transitions] one or more scene files are missing")
        return result

    t_len_default = float(transition_time or 0.26)
    style = style or {}
    rng = np.random.default_rng()
    sfx_master_gain = float(style.get("sfx_transition_gain", 0.62))

    # ---------- attempt list: full -> per-join downgrade -> all safe ----------
    attempts = []
    attempts.append(("full", list(transitions)))

    # Per-join downgrade: replace each custom mode one at a time
    if any(m in CUSTOM_MODES for m in transitions if m is not None):
        downgraded = [_safe_mode(m) if (m is not None and m in CUSTOM_MODES) else m
                      for m in transitions]
        attempts.append(("downgraded", downgraded))

    # All-safe fallback
    all_safe = [_safe_mode(m) if m is not None else None for m in transitions]
    if all_safe != list(transitions):
        attempts.append(("all_safe", all_safe))

    # ---------- run attempts ----------
    final_attempt = None
    final_modes = None
    for label, modes in attempts:
        inputs = []
        for path in scene_paths:
            inputs += ["-i", path]

        parts = []
        for i in range(n):
            parts.append(f"[{i}:v]fps=30,setsar=1,format=yuv420p[v_in{i}]")

        cur_v, cur_a = "v_in0", "0:a"
        total_offset = float(durations[0])
        join_times, join_modes, join_kinds = [], [], []
        ok_so_far = True

        for i, mode in enumerate(modes):
            next_v, next_a = f"v_in{i+1}", f"{i+1}:a"

            real_t = 0.04 if mode is None else max(
                0.12, min(0.5, t_len_default * rng.uniform(0.9, 1.1)))

            pre_a, pre_b, xf = _join_filters(mode, real_t)
            xf = re.sub(r"duration=[\d.]+", f"duration={_fmt(real_t)}", xf)

            if pre_a:
                a_out = f"pa{i}"
                parts.append(f"[{cur_v}]{pre_a}[{a_out}]")
                cur_v = a_out
            if pre_b:
                b_out = f"pb{i}"
                parts.append(f"[{next_v}]{pre_b}[{b_out}]")
                next_v = b_out

            # FIXED OFFSET MATH: subtract the transition overlap
            offset = max(0.0, total_offset - real_t)
            parts.append(
                f"[{cur_v}][{next_v}]{xf.replace('%OFFSET%', _fmt(offset))}[xv{i}]"
            )
            parts.append(
                f"[{cur_a}][{next_a}]acrossfade=d={_fmt(real_t)}:c1=tri:c2=tri[xa{i}]"
            )

            if mode is not None:
                join_times.append(offset + real_t * 0.5)
                join_modes.append(mode)
                join_kinds.append(_mode_kind(mode))

            cur_v, cur_a = f"xv{i}", f"xa{i}"
            # FIXED: next scene starts at offset + real_t, so total timeline
            # advances by (next_scene_len - real_t) after the overlap.
            total_offset = offset + real_t + (float(durations[i + 1]) - real_t)

        graph = ";".join(parts)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph,
               "-map", f"[{cur_v}]", "-map", f"[{cur_a}]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               "-c:a", "aac", "-b:a", "192k", out_path]

        if _try_ffmpeg(cmd, out_path, label=label):
            final_attempt = label
            final_modes = modes
            break
        else:
            print(f"[transitions] attempt '{label}' failed, trying next...")

    if final_attempt is None:
        print("[transitions] ALL attempts failed - returning ok=False")
        return result

    # ---------- build matched SFX for the modes that actually ran ----------
    sfx_list = []
    for t_join, mode, kind in zip(join_times, join_modes, join_kinds):
        audio, sfx_dur = build_transition_sfx(mode, rng=rng)
        if audio is None:
            continue
        per_kind = {
            "fade": 0.55, "wipe": 0.65, "slide": 0.65, "paper": 0.85,
            "glitch": 0.78, "scanline": 0.60, "light": 0.65,
            "filmburn": 0.80, "flash": 0.75, "whipzoom": 0.80,
            "spin": 0.65, "shake": 0.80, "zoomblur": 0.65,
            "inkdrip": 0.75, "punch": 0.85,
        }.get(kind, 0.60)
        sfx_list.append({"t": round(t_join, 3), "mode": mode, "kind": kind,
                         "audio": audio, "gain": per_kind * sfx_master_gain})

    result.update(
        ok=True,
        cuts=[round(t, 3) for t in join_times],
        transition_sfx=sfx_list,
    )
    custom_used = sum(1 for m in final_modes if m in CUSTOM_MODES)
    native_used = sum(1 for m in final_modes if m in XFADE_MODES)
    hard_cuts = sum(1 for m in final_modes if m is None)
    print(
        f"[transitions] '{final_attempt}' pipeline applied: "
        f"{custom_used} custom, {native_used} native xfade, {hard_cuts} hard cuts, "
        f"{len(sfx_list)} matched SFX"
    )
    if final_attempt != "full":
        print(f"[transitions] NOTE: some custom filters were downgraded to native "
              f"equivalents by FFmpeg fallback.")
    return result
