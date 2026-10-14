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

PALETTE_SIZES = [4, 5, 6, 7]
TRANSITION_DURATIONS = [0.20, 0.24, 0.28, 0.32, 0.36, 0.42]

# Per-channel bias: Channel 1 fast, Channel 2 cinematic, Channel 3 vintage.
# This ensures the 3 channels don't accidentally use identical palettes.
CHANNEL_BIAS = {
    0: "fast",
    1: "slow",
    2: "medium",
}

CATEGORY_POOL = {
    "fast":   ["native_fast", "glitch", "zoom"],
    "medium": ["native_all", "zoom", "light", "style"],
    "slow":   ["native_slow", "light", "style", "glitch"],
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

    # channel index from style (set by main.py) → bias
    channel_idx = style.get("channel_index", 0)
    bias = CHANNEL_BIAS.get(channel_idx, style.get("transition_palette_bias", "medium"))
    style["transition_palette_bias"] = bias

    size = rnd.choice(PALETTE_SIZES)
    cats = CATEGORY_POOL.get(bias, CATEGORY_POOL["medium"])

    n_custom = rnd.choice([1, 2])
    n_native = max(1, size - n_custom)

    native_pool = []
    for c in cats:
        if c.startswith("native"):
            native_pool += _category_modes(c)

    custom_pool = []
    for c in cats:
        if not c.startswith("native"):
            custom_pool += _category_modes(c)

    rnd.shuffle(native_pool)
    # Keep every custom effect eligible regardless of channel pacing bias.
    # This includes paper shuffle on fast channels, where it was previously
    # impossible to select because the style category was excluded.
    custom_pool += [m for m in STYLE_MODES if m not in custom_pool]
    rnd.shuffle(custom_pool)

    seen, palette = set(), []
    for m in native_pool:
        if m not in seen:
            seen.add(m)
            palette.append(m)
        if len(palette) >= n_native:
            break
    for m in custom_pool:
        if m not in seen:
            seen.add(m)
            palette.append(m)
        if len(palette) >= size:
            break

    rnd.shuffle(palette)
    print("[transitions] palette: " + ", ".join(palette) +
          f" | bias={bias} | density={style.get('transition_density', 0.55):.2f}")
    return palette


def pick_transitions(n_joins, style, seed=None):
    """
    Returns list of length n_joins. Each item is a mode name OR None (hard cut).
    Same mode appears at most once per video for max variety.
    """
    if n_joins <= 0:
        return []
    density = float(style.get("transition_density", 0.80))
    # Keep some natural hard cuts, but avoid silently turning most joins into
    # cuts when a channel style supplies an overly low transition density.
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
    for _ in range(n_joins):
        if rnd.random() < hard_weight:
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
        # A native FFmpeg slice transition is reliable across FFmpeg builds.
        # Keep the paper-rustle SFX mapped to this distinct visual transition.
        return "", "", _native_filter("hlslice", t_len)

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
    """Map any transition to a broadly supported native xfade for retry."""
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


def apply_transitions_ffmpeg(scene_paths, durations, transitions,
                             scene_gap, out_path, transition_time=None,
                             style=None):
    """Join scenes with randomized transitions, matching SFX, and safe retry.

    First attempt uses the full custom filter pack. If a custom filter fails on
    the runner's FFmpeg build, retry with native xfade equivalents while keeping
    the original transition names for the matching sound effects.
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
    inputs = []
    for path in scene_paths:
        inputs += ["-i", path]

    # First: full effects. Second: robust native transitions if FFmpeg rejects
    # any of the custom filters. Audio remains joined in both attempts.
    for attempt in ("full", "safe"):
        parts = []
        for i in range(n):
            parts.append(f"[{i}:v]fps=30,setsar=1,format=yuv420p[v_in{i}]")
        cur_v, cur_a = "v_in0", "0:a"
        total_offset = float(durations[0])
        join_times, join_modes, join_kinds = [], [], []

        for i, original_mode in enumerate(transitions):
            mode = original_mode if attempt == "full" else _safe_mode(original_mode)
            next_v, next_a = f"v_in{i+1}", f"{i+1}:a"
            real_t = 0.04 if original_mode is None else max(
                0.12, min(0.5, t_len_default * rng.uniform(0.9, 1.1)))
            if attempt == "safe":
                pre_a, pre_b, xf = "", "", _native_filter(
                    "fade" if original_mode is None else _safe_mode(original_mode), real_t)
            else:
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
            offset = max(0.0, total_offset - real_t)
            parts.append(f"[{cur_v}][{next_v}]{xf.replace('%OFFSET%', _fmt(offset))}[xv{i}]")
            parts.append(f"[{cur_a}][{next_a}]acrossfade=d={_fmt(real_t)}:c1=tri:c2=tri[xa{i}]")
            if original_mode is not None:
                join_times.append(offset + real_t * 0.5)
                join_modes.append(original_mode)
                join_kinds.append(_mode_kind(original_mode))
            cur_v, cur_a = f"xv{i}", f"xa{i}"
            total_offset = offset + float(durations[i + 1])

        graph = ";".join(parts)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph,
               "-map", f"[{cur_v}]", "-map", f"[{cur_a}]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               "-c:a", "aac", "-b:a", "192k", out_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except Exception as e:
            print(f"[transitions] {attempt} attempt error: {e}")
            continue
        if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
            print(f"[transitions] {attempt} attempt failed:")
            print((r.stderr or "")[-1800:])
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            continue

        sfx_list = []
        for t_join, mode, kind in zip(join_times, join_modes, join_kinds):
            audio, sfx_dur = build_transition_sfx(mode, rng=rng)
            if audio is None:
                continue
            per_kind = {
                "fade": 0.55, "wipe": 0.65, "slide": 0.65, "paper": 0.78,
                "glitch": 0.70, "scanline": 0.55, "light": 0.60,
                "filmburn": 0.75, "flash": 0.70, "whipzoom": 0.75,
                "spin": 0.60, "shake": 0.75, "zoomblur": 0.60,
                "inkdrip": 0.70, "punch": 0.80,
            }.get(kind, 0.60)
            sfx_list.append({"t": round(t_join, 3), "mode": mode, "kind": kind,
                             "audio": audio, "gain": per_kind * sfx_master_gain})
        result.update(ok=True, cuts=[round(t, 3) for t in join_times], transition_sfx=sfx_list)
        print(f"[transitions] {attempt} pipeline applied; {len(join_modes)} visual transitions, "
              f"{len(sfx_list)} matched SFX")
        if attempt == "safe":
            print("[transitions] NOTE: safe native-xfade fallback was used")
        return result

    return result

