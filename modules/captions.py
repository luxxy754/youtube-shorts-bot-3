"""
Animated word-by-word captions (Alex Hormozi style) + on-screen comment-bait.

- Big bold UPPERCASE text, thick black outline + drop shadow, 2 words (CAPTION_WORDS) at a time.
- The word being spoken is highlighted (yellow / green, alternating per chunk) and a bit bigger.
- Every new chunk "pops" in (small overshoot scale), like CapCut / Hormozi captions.
- Timing comes from edge-tts WordBoundary data (scene["word_times"]); if it is missing, the
  words are spread evenly over the voice line.
- PIL only (no ImageMagick). Fonts are downloaded once into assets/caption_fonts; if the
  download fails we fall back to DejaVu / Liberation Bold so captions never break the video.

Public API used by composer.py:
    ensure_fonts()                                  -> {"anton": path, "lilita": path}
    plan_caption_chunks(scenes, scene_timings, total_duration)
    build_word_caption_clips(scenes, scene_timings, total_duration, chunks=None)
    build_comment_cta_clip(text, scene_timings, total_duration)
"""

import os
import re

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip

TARGET_W = 1080
TARGET_H = 1920

CAPTION_WORDS = max(1, min(3, int(os.getenv("CAPTION_WORDS", "2") or 2)))
CAPTION_FONT_SIZE = int(os.getenv("CAPTION_FONT_SIZE", "122"))
CAPTION_CENTER_Y = 0.60          # vertical centre of the captions (ratio of height)
CAPTION_MAX_W = TARGET_W - 120
ACTIVE_SCALE = 1.10              # spoken word is slightly bigger
POP_DURATION = 0.11              # pop-in animation of a new chunk (seconds)

HIGHLIGHT_COLORS = [(255, 214, 0), (0, 235, 120)]   # yellow, green
WHITE = (255, 255, 255)

FONT_DIR = os.path.join("assets", "caption_fonts")
FONT_URLS = {
    "anton": [
        "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
    ],
    "lilita": [
        "https://github.com/google/fonts/raw/main/ofl/lilitaone/LilitaOne-Regular.ttf",
        "https://raw.githubusercontent.com/google/fonts/main/ofl/lilitaone/LilitaOne-Regular.ttf",
    ],
}
SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_FONT_CACHE = None


def _system_font():
    for f in SYSTEM_FALLBACKS:
        if os.path.exists(f):
            return f
    return None


def ensure_fonts():
    """Download Anton + Lilita One once. Returns only fonts that really exist on disk."""
    global _FONT_CACHE
    if _FONT_CACHE is not None:
        return _FONT_CACHE
    os.makedirs(FONT_DIR, exist_ok=True)
    found = {}
    for name, urls in FONT_URLS.items():
        path = os.path.join(FONT_DIR, os.path.basename(urls[0]))
        if not (os.path.exists(path) and os.path.getsize(path) > 10000):
            for url in urls:
                try:
                    r = requests.get(url, timeout=25)
                    if r.status_code == 200 and len(r.content) > 10000:
                        with open(path, "wb") as f:
                            f.write(r.content)
                        print(f"Caption font downloaded: {name}")
                        break
                except Exception as e:
                    print(f"Caption font {name} download failed: {e}")
        if os.path.exists(path) and os.path.getsize(path) > 10000:
            found[name] = path
    _FONT_CACHE = found
    return found


def _caption_font_path():
    fonts = ensure_fonts()
    return fonts.get("anton") or fonts.get("lilita") or _system_font()


# ------------------------------------------------------------ planning ----
def _clean_token(text):
    text = str(text or "").strip()
    text = re.sub(r"^[^\w']+", "", text)
    text = re.sub(r"[^\w'?!]+$", "", text)
    return text.upper()


def _scene_words(scene, voice_dur):
    """[(text, start, end)] relative to the scene's voice start."""
    out = []
    wt = scene.get("word_times") or []
    for w in wt:
        tok = _clean_token(w.get("text"))
        if tok:
            out.append((tok, float(w.get("start", 0.0)), float(w.get("end", 0.0))))
    if out:
        return out

    tokens = [_clean_token(t) for t in str(scene.get("narration", "")).split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    weights = [len(t) + 2 for t in tokens]
    total_w = float(sum(weights))
    usable = max(0.3, voice_dur - 0.08)
    t = 0.04
    for tok, wgt in zip(tokens, weights):
        d = usable * wgt / total_w
        out.append((tok, t, t + d))
        t += d
    return out


def plan_caption_chunks(scenes, scene_timings, total_duration):
    """
    Returns a list of chunks:
      {"start": abs_sec, "end": abs_sec, "words": [(text, abs_start, abs_end)], "index": n}
    Chunks never cross a scene boundary. Deterministic (no randomness) so the audio pass
    (pop SFX) and the video pass see exactly the same timing.
    """
    chunks = []
    n = 0
    for i, scene in enumerate(scenes):
        if i >= len(scene_timings):
            break
        base, voice_dur = scene_timings[i]
        words = _scene_words(scene, voice_dur)
        for j in range(0, len(words), CAPTION_WORDS):
            group = words[j:j + CAPTION_WORDS]
            abs_words = [(t, base + s, base + e) for (t, s, e) in group]
            start = abs_words[0][1]
            nxt = words[j + CAPTION_WORDS] if j + CAPTION_WORDS < len(words) else None
            end = (base + nxt[1]) if nxt else (abs_words[-1][2] + 0.15)
            end = min(end, total_duration)
            if end - start < 0.12:
                continue
            chunks.append({"start": start, "end": end, "words": abs_words, "index": n})
            n += 1
    return chunks


# ----------------------------------------------------------- rendering ----
def _render_chunk_states(chunk, font_path):
    """One RGBA array per spoken word (that word highlighted). All share one canvas size."""
    size = CAPTION_FONT_SIZE
    words = [w[0] for w in chunk["words"]]

    def load(sz):
        try:
            return ImageFont.truetype(font_path, int(sz))
        except Exception:
            return ImageFont.load_default()

    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    # shrink if the chunk is wider than the safe area (very long words)
    while size > 60:
        font = load(size)
        gap = size * 0.26
        total = sum(probe.textlength(w, font=font) for w in words) + gap * (len(words) - 1)
        if total <= CAPTION_MAX_W:
            break
        size -= 6
    font = load(size)
    font_big = load(size * ACTIVE_SCALE)
    gap = size * 0.26
    stroke = max(7, int(size * 0.085))
    shadow = int(size * 0.06)

    widths = [probe.textlength(w, font=font) for w in words]
    line_w = sum(widths) + gap * (len(words) - 1)
    ascent, descent = font_big.getmetrics()
    pad = stroke + shadow + 14
    canvas_w = int(line_w + pad * 2 + size * 0.2)
    canvas_h = int(ascent + descent + pad * 2)
    baseline = pad + ascent

    color = HIGHLIGHT_COLORS[chunk["index"] % len(HIGHLIGHT_COLORS)]
    states = []
    for active in range(len(words)):
        img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        x = (canvas_w - line_w) / 2.0
        for k, w in enumerate(words):
            is_active = (k == active)
            f = font_big if is_active else font
            fill = color if is_active else WHITE
            wx = x
            if is_active:  # centre the bigger word on its normal slot
                wx = x - (probe.textlength(w, font=font_big) - widths[k]) / 2.0
            # drop shadow, then thick outline + fill
            draw.text((wx, baseline + shadow), w, font=f, fill=(0, 0, 0, 170),
                      anchor="ls", stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
            draw.text((wx, baseline), w, font=f, fill=fill + (255,), anchor="ls",
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            x += widths[k] + gap
        states.append(np.array(img))
    return states


def _pop_scale(t):
    if t >= POP_DURATION:
        return 1.0
    p = t / POP_DURATION
    return 0.80 + 0.20 * p + 0.12 * np.sin(np.pi * p)


def _make_clip(arr, start, end, center_y_ratio, pop):
    h0 = arr.shape[0]
    yc = TARGET_H * center_y_ratio
    clip = ImageClip(arr, transparent=True).set_start(start).set_duration(max(0.05, end - start))
    if pop:
        clip = clip.resize(_pop_scale)
        clip = clip.set_position(lambda t: ("center", int(yc - h0 * _pop_scale(t) / 2.0)))
    else:
        clip = clip.set_position(("center", int(yc - h0 / 2.0)))
    return clip


def build_word_caption_clips(scenes, scene_timings, total_duration, chunks=None):
    font_path = _caption_font_path()
    if not font_path:
        print("No caption font available - word captions skipped")
        return []
    chunks = chunks if chunks is not None else plan_caption_chunks(scenes, scene_timings, total_duration)

    clips = []
    for chunk in chunks:
        try:
            states = _render_chunk_states(chunk, font_path)
        except Exception as e:
            print(f"Caption chunk render failed (skipped): {e}")
            continue
        words = chunk["words"]
        for k, arr in enumerate(states):
            s = chunk["start"] if k == 0 else words[k][1]
            e = chunk["end"] if k == len(states) - 1 else words[k + 1][1]
            e = min(e, total_duration)
            if e - s < 0.04:
                continue
            clips.append(_make_clip(arr, s, e, CAPTION_CENTER_Y, pop=(k == 0)))
    print(f"Word captions: {len(chunks)} chunks, {len(clips)} frames-states")
    return clips


# ------------------------------------------------- comment-bait overlay ----
def build_comment_cta_clip(text, scene_timings, total_duration):
    """
    Pill with a question at the end of the video ('Have you noticed this? Comment below').
    It is on-screen text only: the spoken last line stays the loop line, so the replay is seamless.
    """
    text = " ".join(str(text or "").split())
    if not text:
        return None
    fonts = ensure_fonts()
    font_path = fonts.get("lilita") or fonts.get("anton") or _system_font()
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
        img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=40, fill=(0, 0, 0, 190),
                            outline=(255, 214, 0, 255), width=5)
        lw = probe.textlength(label, font=l_font)
        d.text(((box_w - lw) / 2, pad_y), label, font=l_font, fill=(255, 214, 0, 255))
        y = pad_y + l_h
        for line in lines:
            w = probe.textlength(line, font=q_font)
            d.text(((box_w - w) / 2, y), line, font=q_font, fill=(255, 255, 255, 255),
                   stroke_width=3, stroke_fill=(0, 0, 0, 255))
            y += q_h

        last_start = scene_timings[-1][0]
        start = max(last_start, total_duration - 2.8)
        start = min(start, max(0.0, total_duration - 1.0))
        arr = np.array(img)
        return _make_clip(arr, start, total_duration, 0.79, pop=True)
    except Exception as e:
        print(f"Comment CTA render failed (skipped): {e}")
        return None
