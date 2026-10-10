"""
Word-by-word English captions (Submagic / Hormozi style) - STYLE-AWARE.

Changes in this version
-----------------------
- Captions accept a `style` dict (from modules.style_variation.get_style).
  Every video gets its own caption Y position, X offset, rotation, size range,
  accent palette, font family preference, words-per-chunk and stroke thickness.

- The spoken word is highlighted (accent colour + bigger). Already-spoken words
  of the same phrase stay white.

- Every word gets its own font + size. Important words (long / "power" words)
  are drawn BIG in a loud display font, filler words (the, a, of ...) small.

- Pure PIL rendering. Google Fonts auto-downloaded into assets/fonts on first
  run; if a download fails the code falls back to the system bold font.

Timing: if the voiceover step saved REAL word timings (<mp3>.words.json) they
are used directly -> frame-accurate sync.
"""

import os
import random
import re

from PIL import Image, ImageDraw, ImageFont

TARGET_W = 1080
TARGET_H = 1920

DEFAULT_CAPTION_CENTER_RATIO = 0.62
MAX_LINE_W = TARGET_W - 120
DEFAULT_WORDS_PER_CHUNK = 2
WORD_GAP = 28
DEFAULT_ACTIVE_SCALE = 1.15
POP_SCALE = 1.14
POP_TIME = 0.07

FONT_DIR = os.path.join("assets", "fonts")
_GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_SPECS = {
    "anton":    ("Anton-Regular.ttf",         _GF + "anton/Anton-Regular.ttf", True),
    "bangers":  ("Bangers-Regular.ttf",       _GF + "bangers/Bangers-Regular.ttf", True),
    "luckiest": ("LuckiestGuy-Regular.ttf",   _GF + "luckiestguy/LuckiestGuy-Regular.ttf", True),
    "bebas":    ("BebasNeue-Regular.ttf",     _GF + "bebasneue/BebasNeue-Regular.ttf", False),
    "lilita":   ("LilitaOne-Regular.ttf",     _GF + "lilitaone/LilitaOne-Regular.ttf", False),
    "marker":   ("PermanentMarker-Regular.ttf", _GF + "permanentmarker/PermanentMarker-Regular.ttf", False),
    "poppins":  ("Poppins-ExtraBold.ttf",     _GF + "poppins/Poppins-ExtraBold.ttf", False),
}

SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

ACCENTS = [
    (255, 221, 0),
    (0, 255, 140),
]

STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "am", "of", "to", "in", "on",
    "at", "and", "or", "but", "it", "its", "it's", "this", "that", "for", "with", "as", "by",
    "you", "your", "i", "we", "they", "he", "she", "has", "have", "had", "do", "does", "did",
    "so", "if", "from", "than", "then", "there", "their", "them", "us", "our", "my", "me",
    "can", "will", "just", "up", "out", "into", "over", "when", "what", "which", "who",
}
POWER_WORDS = {
    "never", "secret", "secrets", "truth", "lie", "lies", "dead", "die", "dies", "deadly",
    "danger", "dangerous", "impossible", "shocking", "alive", "brain", "heart", "money",
    "gold", "world", "first", "last", "only", "fastest", "biggest", "strongest", "hidden",
    "crazy", "insane", "million", "billion", "nobody", "everyone", "why", "how", "stop",
    "warning", "real", "fake", "myth", "kill", "killed", "fire", "ice", "space", "ocean",
    "speed", "power", "fast", "time", "twist", "wrong", "mystery", "forever", "zero",
    "tickle", "ticklish", "laugh", "laughing", "touch", "skin", "nerve", "nerves",
    "cerebellum", "reflex", "yourself", "you're", "can't", "won't", "feel", "feels",
}

_font_cache = {}
_available = None


# ---------------------------------------------------------------- fonts ----
def _looks_like_font(data):
    return len(data) > 10_000 and data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def ensure_fonts():
    global _available
    if _available is not None:
        return _available

    os.makedirs(FONT_DIR, exist_ok=True)
    found = {}
    for key, (fname, url, _display) in FONT_SPECS.items():
        path = os.path.join(FONT_DIR, fname)
        if not os.path.exists(path):
            try:
                import requests
                r = requests.get(url, timeout=25)
                if r.status_code == 200 and _looks_like_font(r.content):
                    with open(path, "wb") as f:
                        f.write(r.content)
                    print("Caption font downloaded: " + fname)
                else:
                    print("Caption font download failed (" + str(r.status_code) + "): " + fname)
            except Exception as e:
                print("Caption font download error " + fname + ": " + str(e))
        if os.path.exists(path):
            found[key] = path

    _available = found
    print("Caption fonts ready: " + (", ".join(sorted(found)) or "NONE (system fallback)"))
    return found


def _fallback_font():
    for f in SYSTEM_FALLBACKS:
        if os.path.exists(f):
            return f
    try:
        import subprocess
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if out and os.path.exists(out):
            return out
    except Exception:
        pass
    return None


def _load_font(path, size):
    key = (path, int(size))
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(path, int(size)) if path else ImageFont.load_default()
        except Exception:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# --------------------------------------------------------------- planning ----
def _clean_display(word):
    word = re.sub(r"[^\w'\u2019-]", "", word, flags=re.UNICODE)
    return word.upper()


def _spoken_weight(tok):
    letters = len(re.findall(r"[A-Za-z0-9]", tok))
    w = float(max(letters, 2))
    if re.search(r"[,;:]$", tok):
        w += 2.5
    if re.search(r"[.?!]$", tok):
        w += 4.0
    return w


def plan_scene_words(narration, _unused=None):
    words = []
    for tok in str(narration).split():
        disp = _clean_display(tok)
        if not disp:
            continue
        words.append((disp, _spoken_weight(tok), bool(re.search(r"[,.?!]$", tok))))
    return words


def _timed_from_real(real, start, scene_end):
    timed = []
    for w in real:
        disp = _clean_display(w.get("text", ""))
        if not disp:
            continue
        timed.append({"text": disp, "t0": start + float(w["start"]), "t1": start + float(w["end"])})
    for k, w in enumerate(timed):
        nxt = timed[k + 1]["t0"] if k + 1 < len(timed) else None
        w["ends"] = nxt is None or (nxt - w["t1"]) > 0.15
        if nxt is None:
            w["t1"] = max(w["t1"], min(scene_end, w["t1"] + 0.25))
    return timed


def _timed_estimated(narration, start, dur, scene_end):
    words = plan_scene_words(narration)
    if not words:
        return []
    total_w = sum(w[1] for w in words)
    cum = 0.0
    timed = []
    for k, (text, wt, ends) in enumerate(words):
        t0 = start + dur * cum / total_w
        cum += wt
        t1 = start + dur * cum / total_w
        if k == len(words) - 1:
            t1 = max(t1, min(scene_end, t1 + 0.35))
        timed.append({"text": text, "t0": t0, "t1": t1, "ends": ends})
    return timed


def plan_word_events(scenes, scene_timings, total_duration,
                     word_timings=None, words_per_chunk=None):
    wpc = words_per_chunk or DEFAULT_WORDS_PER_CHUNK
    chunks = []
    count = min(len(scenes), len(scene_timings))
    for i in range(count):
        start, dur = scene_timings[i]
        scene_end = scene_timings[i + 1][0] if i + 1 < count else total_duration
        real = word_timings[i] if (word_timings and i < len(word_timings)) else None
        timed = _timed_from_real(real, start, scene_end) if real else []
        if not timed:
            timed = _timed_estimated(scenes[i].get("narration", ""), start, dur, scene_end)
        if not timed:
            continue
        cur = []
        for w in timed:
            cur.append(w)
            if len(cur) >= wpc or w["ends"]:
                chunks.append({"words": cur})
                cur = []
        if cur:
            chunks.append({"words": cur})
    return chunks


# ---------------------------------------------------------------- styling ----
def _importance(words):
    importance = []
    for w in words:
        low = w["text"].lower()
        if low in STOP_WORDS:
            importance.append(0)
        elif low in POWER_WORDS or len(low) >= 7:
            importance.append(2)
        else:
            importance.append(1)
    if max(importance) < 2:
        best = max(range(len(words)), key=lambda j: (importance[j], len(words[j]["text"])))
        if importance[best] >= 1:
            importance[best] = 2
    return importance


def hero_word_times(scenes, scene_timings, total_duration,
                    word_timings=None, style=None):
    """Start times of every hero caption word -> used for 'pop' sound effects."""
    style = style or {}
    times = []
    for chunk in plan_word_events(scenes, scene_timings, total_duration,
                                  word_timings,
                                  words_per_chunk=style.get("words_per_chunk")):
        imp = _importance(chunk["words"])
        for w, level in zip(chunk["words"], imp):
            if level == 2 and w["t0"] < total_duration - 0.2:
                times.append(round(w["t0"], 3))
    return times


def _style_chunk(chunk, rng, fonts, fallback, state, style):
    words = chunk["words"]
    display_keys = [k for k, s in FONT_SPECS.items() if s[2] and k in fonts]
    normal_keys = [k for k, s in FONT_SPECS.items() if not s[2] and k in fonts]

    preferred = style.get("font_family")
    pref_pool_display, pref_pool_normal = [], []
    if preferred and preferred in fonts:
        if FONT_SPECS[preferred][2]:
            pref_pool_display = [preferred]
        else:
            pref_pool_normal = [preferred]

    all_keys = display_keys + normal_keys
    size_range = style.get("size_range", (68, 94, 128))
    accents = style.get("accents", ACCENTS)

    importance = _importance(words)

    for j, w in enumerate(words):
        imp = importance[j]
        if imp == 2:
            pool = (pref_pool_display * 3) + display_keys or all_keys
        elif imp == 1:
            pool = (pref_pool_normal * 2) + normal_keys or all_keys
        else:
            pool = normal_keys or all_keys
        pool = [k for k in pool if k != state.get("last_font")] or pool
        key = rng.choice(pool) if pool else None
        state["last_font"] = key
        w["font_path"] = fonts.get(key) if key else fallback
        base = {2: size_range[2], 1: size_range[1], 0: size_range[0]}[imp]
        w["size"] = base + rng.randint(-8, 8)
        w["accent"] = accents[state["accent_i"] % len(accents)]
        state["accent_i"] += 1
        w["hero"] = imp == 2


def style_chunks(chunks, style=None, seed=None):
    style = style or {}
    fonts = ensure_fonts()
    fallback = _fallback_font()
    rng = random.Random(seed)
    state = {"last_font": None,
             "accent_i": rng.randrange(len(style.get("accents", ACCENTS)))}
    for c in chunks:
        _style_chunk(c, rng, fonts, fallback, state, style)
    return chunks


# -------------------------------------------------------------- rendering ----
def _layout(chunk):
    dummy = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    metrics = []
    for w in chunk["words"]:
        font = _load_font(w["font_path"], w["size"])
        width = dummy.textlength(w["text"], font=font)
        asc, desc = font.getmetrics()
        metrics.append((width, asc, desc))

    lines, cur, cur_w = [], [], 0.0
    for j, (width, _a, _d) in enumerate(metrics):
        add = width + (WORD_GAP if cur else 0)
        if cur and cur_w + add > MAX_LINE_W:
            lines.append(cur)
            cur, cur_w = [], 0.0
            add = width
        cur.append(j)
        cur_w += add
    if cur:
        lines.append(cur)

    pad = 40
    y = pad
    placed = [None] * len(chunk["words"])
    for line in lines:
        line_w = sum(metrics[j][0] for j in line) + WORD_GAP * (len(line) - 1)
        max_asc = max(metrics[j][1] for j in line)
        max_desc = max(metrics[j][2] for j in line)
        baseline = y + max_asc
        x = (TARGET_W - line_w) / 2
        for j in line:
            width = metrics[j][0]
            placed[j] = {"cx": x + width / 2, "baseline": baseline, "width": width}
            x += width + WORD_GAP
        y = baseline + max_desc + 14
    return placed, int(y + pad)


def render_chunk_frame(chunk, active_idx, pop=False, style=None):
    style = style or {}
    if "layout" not in chunk:
        chunk["layout"] = _layout(chunk)
    placed, height = chunk["layout"]

    img = Image.new("RGBA", (TARGET_W, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    active_scale = style.get("active_scale", DEFAULT_ACTIVE_SCALE)
    stroke_scale = style.get("stroke_scale", 0.10)

    for j in range(active_idx + 1):
        w = chunk["words"][j]
        active = (j == active_idx)
        size = w["size"] * ((active_scale * (POP_SCALE if pop else 1.0)) if active else 1.0)
        font = _load_font(w["font_path"], size)
        stroke = max(6, int(size * stroke_scale))
        fill = w["accent"] if active else (255, 255, 255)
        cx, base = placed[j]["cx"], placed[j]["baseline"]

        draw.text((cx + 4, base + 8), w["text"], font=font, fill=(0, 0, 0, 220),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 220), anchor="ms")
        draw.text((cx, base), w["text"], font=font, fill=fill + (255,),
                  stroke_width=stroke, stroke_fill=(0, 0, 0, 255), anchor="ms")
    return img


def build_word_caption_clips(scenes, scene_timings, total_duration,
                             seed=None, word_timings=None, style=None):
    """
    Return moviepy ImageClips ready for CompositeVideoClip.
    Every spoken word -> a short 'pop' frame followed by the normal highlighted frame.
    """
    import numpy as np
    from moviepy.editor import ImageClip

    style = style or {}

    events = plan_word_events(scenes, scene_timings, total_duration, word_timings,
                              words_per_chunk=style.get("words_per_chunk"))
    chunks = style_chunks(events, style=style, seed=seed)
    clips = []

    y_ratio = style.get("caption_y_ratio", DEFAULT_CAPTION_CENTER_RATIO)
    x_jit = style.get("caption_x_jitter", 0)
    rotation = style.get("caption_rotation", 0)

    def _add(frame, t0, dur):
        if dur <= 0.02:
            return
        clip = ImageClip(np.array(frame), transparent=True).set_start(t0).set_duration(dur)
        y = int(TARGET_H * y_ratio - frame.height / 2)
        if x_jit:
            x = int(TARGET_W // 2 + x_jit - frame.shape[1] / 2)
            clip = clip.set_position((x, y))
        else:
            clip = clip.set_position(("center", y))
        if rotation:
            try:
                clip = clip.rotate(rotation, resample="bilinear", expand=False)
            except Exception:
                pass
        clips.append(clip)

    for chunk in chunks:
        for idx, w in enumerate(chunk["words"]):
            t0 = w["t0"]
            t1 = chunk["words"][idx + 1]["t0"] if idx + 1 < len(chunk["words"]) else w["t1"]
            dur = t1 - t0
            if dur <= 0.03 or t0 >= total_duration:
                continue
            dur = min(dur, total_duration - t0)
            pop = min(POP_TIME, dur * 0.5)
            _add(render_chunk_frame(chunk, idx, pop=True, style=style), t0, pop)
            _add(render_chunk_frame(chunk, idx, pop=False, style=style),
                 t0 + pop, dur - pop)

    print(str(len(clips)) + " word-caption frames built "
          + ("(real word timings)" if word_timings and any(word_timings) else "(estimated timings)"))
    return clips


# ------------------------------------------------- comment-bait overlay ----
def _pop_scale(t, pop_dur=0.11):
    import math
    if t >= pop_dur:
        return 1.0
    p = t / pop_dur
    return 0.80 + 0.20 * p + 0.12 * math.sin(math.pi * p)


def build_comment_cta_clip(text, scene_timings, total_duration, style=None):
    """
    Pill with a question at the end of the video. Style-aware: position, colours,
    pill fill and border are randomized per video.
    """
    import numpy as np
    from moviepy.editor import ImageClip

    style = style or {}
    text = " ".join(str(text or "").split())
    if not text:
        return None
    fonts = ensure_fonts()
    font_path = fonts.get(style.get("font_family", "")) or fonts.get("lilita") \
        or fonts.get("anton") or _fallback_font()
    if not font_path or not scene_timings:
        return None
    try:
        label = "COMMENT YOUR ANSWER"
        q_font = ImageFont.truetype(font_path, 74)
        l_font = ImageFont.truetype(font_path, 40)
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        max_w = TARGET_W - 220

        lines, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if cur and probe.textlength(trial, font=q_font) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        lines = lines[:2]

        q_h, l_h = 92, 56
        pad_x, pad_y = 52, 30
        box_w = int(max([probe.textlength(label, font=l_font)] +
                        [probe.textlength(l, font=q_font) for l in lines])) + pad_x * 2
        box_h = l_h + q_h * len(lines) + pad_y * 2

        fill = style.get("comment_pill_fill", (0, 0, 0, 190))
        border = style.get("comment_pill_border", (255, 214, 0, 255))

        img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=40,
                            fill=fill, outline=border, width=5)
        lw = probe.textlength(label, font=l_font)
        d.text(((box_w - lw) / 2, pad_y), label, font=l_font, fill=border)
        y = pad_y + l_h
        for line in lines:
            w = probe.textlength(line, font=q_font)
            d.text(((box_w - w) / 2, y), line, font=q_font, fill=(255, 255, 255, 255),
                   stroke_width=3, stroke_fill=(0, 0, 0, 255))
            y += q_h

        show = style.get("comment_duration", 2.8)
        last_start = scene_timings[-1][0]
        start = max(last_start, total_duration - show)
        start = min(start, max(0.0, total_duration - 1.0))
        arr = np.array(img)
        h0 = arr.shape[0]
        y_ratio = style.get("comment_y_ratio", 0.79)
        yc = TARGET_H * y_ratio
        clip = ImageClip(arr, transparent=True).set_start(start).set_duration(max(0.05, total_duration - start))
        clip = clip.resize(_pop_scale)
        return clip.set_position(lambda t: ("center", int(yc - h0 * _pop_scale(t) / 2.0)))
    except Exception as e:
        print(f"Comment CTA render failed (skipped): {e}")
        return None
