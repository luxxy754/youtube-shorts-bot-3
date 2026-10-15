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


import json
import math

STATE_FILE = os.environ.get("TRANSITION_STATE_FILE", "transition_state.json")
# Paper shuffle is ALWAYS used (once per video, at a random join). It is kept
# out of the rotating bag so it never gets "used up".
MUST_HAVE = ["papershuffle"]


def _load_bag():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        bag = [m for m in data.get("bag", []) if m in ALL_MODES and m not in MUST_HAVE]
        return bag
    except Exception:
        return []


def _save_bag(bag):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"bag": bag, "total_modes": len(ALL_MODES)}, f)
    except Exception as e:
        print(f"[transitions] could not save {STATE_FILE}: {e}")


def _draw_from_bag(k, rnd):
    """
    Draw k DIFFERENT modes from a persistent shuffle-bag containing every
    transition. Every transition gets used once before any is repeated, so over
    a few runs ALL ~50 effects show up. Order is always random.
    """
    full = [m for m in ALL_MODES if m not in MUST_HAVE]
    bag = _load_bag()
    out = []
    while len(out) < k:
        if not bag:
            bag = list(full)
            rnd.shuffle(bag)
            # do not start a refill with something we just drew
            bag.sort(key=lambda m: m in out)
        m = bag.pop(0)
        if m in out:
            continue
        out.append(m)
    _save_bag(bag)
    return out


def pick_palette(style, seed=None):
    """Kept for backward compatibility - returns the full mode list."""
    return list(ALL_MODES)


def pick_transitions(n_joins, style, seed=None):
    """
    Returns list of length n_joins. Each item is a mode name OR None (hard cut).

    * papershuffle is guaranteed at least once per video (random join).
    * every other join gets a different effect from the persistent bag, so all
      transitions rotate through over time. Order is random every video.
    * a small number of hard cuts is kept for natural pacing.
    """
    if n_joins <= 0:
        return []
    if seed is None:
        seed = random.SystemRandom().randrange(0, 2**32)
    rnd = random.Random(seed)

    out = [None] * n_joins
    # every join gets an effect; at most ONE hard cut per video (only on
    # longer videos) so the pacing still breathes. Never on the paper join.
    effect_idx = list(range(n_joins))
    if n_joins >= 6 and rnd.random() < 0.5:
        effect_idx.remove(rnd.randrange(n_joins))

    paper_at = rnd.choice(effect_idx)
    out[paper_at] = "papershuffle"
    others = [i for i in effect_idx if i != paper_at]
    picks = _draw_from_bag(len(others), rnd) if others else []

    # avoid two neighbours of the same SFX family (e.g. wipe, wipe)
    for pos, m in zip(others, picks):
        out[pos] = m
    for i in range(1, n_joins):
        if out[i] and out[i - 1] and _mode_kind(out[i]) == _mode_kind(out[i - 1]):
            for j in range(i + 1, n_joins):
                if out[j] and out[j] != "papershuffle" and \
                        _mode_kind(out[j]) != _mode_kind(out[i - 1]) and \
                        i != paper_at:
                    out[i], out[j] = out[j], out[i]
                    break
    print("[transitions] this video: " +
          ", ".join(m or "cut" for m in out))
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
        # Paper-sheet feel: the outgoing "sheet" tilts and lifts away while
        # the incoming one settles from the opposite tilt, joined by a slice
        # reveal (like cards / sheets being shuffled).
        T = _fmt(t_len)
        pre_a = (f"rotate='-0.14*min(t/{T},1)':ow=iw:oh=ih:fillcolor=black,"
                 f"scale=iw*'1+0.10*min(t/{T},1)':ih*'1+0.10*min(t/{T},1)':eval=frame,"
                 "crop=1080:1920")
        pre_b = (f"rotate='0.14*(1-min(t/{T},1))':ow=iw:oh=ih:fillcolor=black,"
                 f"scale=iw*'1.10-0.10*min(t/{T},1)':ih*'1.10-0.10*min(t/{T},1)':eval=frame,"
                 "crop=1080:1920")
        return pre_a, pre_b, _native_filter("hrslice", t_len)

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
# MAIN APPLY  (windowed, time-preserving)
# ============================================================
# How it works
# ------------
# The old version applied the custom effects (zoom, spin, blur, pixelate ...)
# to the WHOLE accumulated video, so the entire clip got rotated / blurred and
# the render was extremely slow (-> timeout -> fallback -> "no transitions").
#
# Now every scene is cut into  [head | body | tail].  Only the tiny tail of
# scene i and the tiny head of scene i+1 go through the effect + xfade; the
# body is passed through untouched.  The transition is centred ON the scene
# boundary (the missing half is a frozen frame), so the total video length is
# exactly the sum of the scene lengths -> voice, captions, SFX never drift.
FPS = 30


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


def _probe(path):
    """(video_seconds, has_audio) for a scene file."""
    import json as _json
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,duration:format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60)
        j = _json.loads(r.stdout or "{}")
        vdur, has_a = None, False
        for s in j.get("streams", []):
            if s.get("codec_type") == "video" and vdur is None:
                try:
                    vdur = float(s.get("duration"))
                except (TypeError, ValueError):
                    vdur = None
            if s.get("codec_type") == "audio":
                has_a = True
        if vdur is None:
            vdur = float(j.get("format", {}).get("duration", 0) or 0)
        return vdur, has_a
    except Exception:
        return 0.0, False


def _xfade_dur(xf, default):
    m = re.search(r"duration=([\d.]+)", xf)
    return float(m.group(1)) if m else default


def _build_graph(n, frames, join_defs, has_audio):
    """
    frames    : list[int]   frame count of every scene
    join_defs : list[dict|None]  per join: {F, pre_a, pre_b, xf} or None (hard cut)
    """
    parts = []
    half = [0 if jd is None else jd["F"] // 2 for jd in join_defs]

    for i in range(n):
        h_in = half[i - 1] if i > 0 else 0
        h_out = half[i] if i < n - 1 else 0
        need_head, need_tail = h_in > 0, h_out > 0
        outs = ["s%db" % i] + (["s%dh" % i] if need_head else []) + \
               (["s%dt" % i] if need_tail else [])
        parts.append(f"[{i}:v]fps={FPS},setsar=1,format=yuv420p,"
                     f"split={len(outs)}" + "".join(f"[{o}]" for o in outs))
        parts.append(f"[s{i}b]trim=start_frame={h_in}:end_frame={frames[i] - h_out},"
                     f"setpts=PTS-STARTPTS[body{i}]")
        if need_tail:
            F = join_defs[i]["F"]
            parts.append(
                f"[s{i}t]trim=start_frame={frames[i] - h_out}:end_frame={frames[i]},"
                f"setpts=PTS-STARTPTS,tpad=stop={h_out}:stop_mode=clone,"
                f"trim=end_frame={F},setpts=PTS-STARTPTS[ta{i}]")
        if need_head:
            F = join_defs[i - 1]["F"]
            parts.append(
                f"[s{i}h]trim=start_frame=0:end_frame={h_in},setpts=PTS-STARTPTS,"
                f"tpad=start={h_in}:start_mode=clone,"
                f"trim=end_frame={F},setpts=PTS-STARTPTS[hb{i - 1}]")

    order = []
    for i in range(n):
        order.append(f"[body{i}]")
        if i < n - 1 and join_defs[i] is not None:
            jd = join_defs[i]
            ca, cb = f"ta{i}", f"hb{i}"
            if jd["pre_a"]:
                parts.append(f"[{ca}]{jd['pre_a']},setsar=1,format=yuv420p[pa{i}]")
                ca = f"pa{i}"
            if jd["pre_b"]:
                parts.append(f"[{cb}]{jd['pre_b']},setsar=1,format=yuv420p[pb{i}]")
                cb = f"pb{i}"
            parts.append(f"[{ca}][{cb}]{jd['xf']},setsar=1,format=yuv420p[seg{i}]")
            order.append(f"[seg{i}]")
    parts.append("".join(order) + f"concat=n={len(order)}:v=1:a=0[outv]")

    if has_audio:
        for i in range(n):
            dur = frames[i] / FPS
            parts.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,"
                         f"apad=whole_dur={dur:.4f},atrim=0:{dur:.4f},"
                         f"asetpts=PTS-STARTPTS[aa{i}]")
        parts.append("".join(f"[aa{i}]" for i in range(n)) +
                     f"concat=n={n}:v=0:a=1[outa]")
    return ";".join(parts)


def apply_transitions_ffmpeg(scene_paths, durations, transitions,
                             scene_gap, out_path, transition_time=None,
                             style=None):
    """Join scenes with randomized transitions + matching SFX (safe retry).

    Returns {"ok", "cuts", "transition_sfx"}. Total video length equals the sum
    of the scene lengths, and every transition is centred on its scene boundary.
    """
    result = {"ok": False, "cuts": [], "transition_sfx": []}
    n = len(scene_paths)
    if n < 2 or len(transitions) != n - 1 or len(durations) != n:
        return result
    if any(not os.path.isfile(p) for p in scene_paths):
        print("[transitions] one or more scene files are missing")
        return result

    style = style or {}
    t_base = float(transition_time or 0.26)
    rng = np.random.default_rng()
    sfx_master_gain = float(style.get("sfx_transition_gain", 0.62))

    # real scene lengths (what is actually inside the files)
    frames, has_audio_all = [], True
    for p, d in zip(scene_paths, durations):
        vd, ha = _probe(p)
        has_audio_all = has_audio_all and ha
        use = vd if vd > 0.3 else float(d)
        frames.append(max(6, int(round(use * FPS))))

    inputs = []
    for p in scene_paths:
        inputs += ["-i", p]

    for attempt in ("full", "safe"):
        join_defs, join_modes = [], []
        for i, original in enumerate(transitions):
            if original is None:
                join_defs.append(None)
                continue
            real_t = max(0.12, min(0.5, t_base * float(rng.uniform(0.9, 1.1))))
            if attempt == "safe":
                pre_a, pre_b = "", ""
                xf = _native_filter(_safe_mode(original), real_t)
            else:
                pre_a, pre_b, xf = _join_filters(original, real_t)
            dur = _xfade_dur(xf, real_t)
            F = 2 * int(math.ceil(dur * FPS / 2.0))      # even number of frames
            # a transition may never eat more than ~35% of a neighbouring scene
            cap = int(min(frames[i], frames[i + 1]) * 0.35)
            F = max(4, min(F, 2 * (cap // 2)))
            dur = min(dur, F / FPS)
            xf = re.sub(r"duration=[\d.]+", f"duration={_fmt(dur)}", xf)
            xf = xf.replace("%OFFSET%", "0")
            join_defs.append({"F": F, "pre_a": pre_a, "pre_b": pre_b, "xf": xf})

        graph = _build_graph(n, frames, join_defs, has_audio_all)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[outv]"]
        if has_audio_all:
            cmd += ["-map", "[outa]"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
        if has_audio_all:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += [out_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1500)
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

        # ---- matched SFX, centred on each scene boundary ----
        sfx_list, cuts, boundary = [], [], 0
        for i, original in enumerate(transitions):
            boundary += frames[i]
            b = boundary / FPS
            if original is None:
                continue
            cuts.append(round(b, 3))
            kind = _mode_kind(original)
            audio, sfx_dur = build_transition_sfx(original, rng=rng)
            if audio is None:
                continue
            per_kind = {
                "fade": 0.55, "wipe": 0.65, "slide": 0.65, "paper": 0.85,
                "glitch": 0.70, "scanline": 0.55, "light": 0.60,
                "filmburn": 0.75, "flash": 0.70, "whipzoom": 0.75,
                "spin": 0.60, "shake": 0.75, "zoomblur": 0.60,
                "inkdrip": 0.70, "punch": 0.80, "click": 0.80,
            }.get(kind, 0.60)
            half_t = join_defs[i]["F"] / FPS / 2.0
            if kind in ("punch", "flash", "shake", "glitch", "click"):
                start = b - 0.03                    # hit exactly on the cut
            else:
                start = b - half_t                  # swell with the transition
            sfx_list.append({"t": round(start + 0.05, 3), "mode": original,
                             "kind": kind, "audio": audio,
                             "gain": per_kind * sfx_master_gain})
        result.update(ok=True, cuts=cuts, transition_sfx=sfx_list)
        n_fx = sum(1 for t in transitions if t)
        print(f"[transitions] {attempt} pipeline applied; {n_fx} visual transitions, "
              f"{len(sfx_list)} matched SFX | " +
              ", ".join(t or "cut" for t in transitions))
        if attempt == "safe":
            print("[transitions] NOTE: safe native-xfade fallback was used")
        return result
    return result
