"""
Caption MODES - six genuinely different caption looks, picked at random per video.

The old captions always had the same skeleton (mixed fonts, word pops). Changing font or
position is not enough: the *template* is what viewers (and reviewers) recognise. Each mode
below is a different on-screen design:

  pop       the original: every word its own font/size, active word pops          (captions.py)
  box       short phrase, the spoken word sits in a coloured rounded box          (Submagic style)
  single    ONE giant word at a time in the middle                                (hype / edit style)
  subtitle  clean 2-line subtitle on a soft dark bar, spoken word turns coloured  (documentary style)
  karaoke   whole phrase visible, words light up one by one while spoken          (lyrics style)
  stack     words stacked vertically, one per line, growing as they are spoken    (poster style)

Pure PIL. Fonts: Google Fonts (OFL, free) - downloaded by captions.ensure_fonts(); if a font
cannot be fetched the system bold font is used, so nothing ever fails.
"""

import random

from PIL import Image, ImageDraw

TARGET_W = 1080
TARGET_H = 1920

# mode -> weight used when a video picks its mode
MODE_WEIGHTS = {"pop": 2, "box": 2, "single": 1, "subtitle": 1, "karaoke": 1, "stack": 1}
NO_ROTATE = {"subtitle", "karaoke", "single"}

_BASE_SIZE = {"box": 88, "single": 170, "subtitle": 62, "karaoke": 80, "stack": 112}


def pick_mode(rnd):
    modes = list(MODE_WEIGHTS)
    return rnd.choices(modes, weights=[MODE_WEIGHTS[m] for m in modes], k=1)[0]


def words_per_chunk(mode, style):
    base = style.get("words_per_chunk") or 2
    return {"single": 1, "box": max(2, min(3, base)), "subtitle": 5,
            "karaoke": 5, "stack": 3}.get(mode, base)


def caption_y_ratio(mode, style):
    default = style.get("caption_y_ratio", 0.62)
    if mode == "subtitle":
        return 0.76
    if mode == "stack":
        return max(0.55, default - 0.04)
    return default


# ------------------------------------------------------------------ styling --
def style_chunks_mode(chunks, mode, style, seed=None):
    from modules import captions as C
    fonts = C.ensure_fonts()
    fallback = C._fallback_font()
    rng = random.Random(seed)

    display = [k for k, s in C.FONT_SPECS.items() if s[2] and k in fonts]
    normal = [k for k, s in C.FONT_SPECS.items() if not s[2] and k in fonts]
    pool = (normal or display) if mode in ("subtitle", "karaoke") else (display or normal)
    pref = style.get("font_family")
    key = pref if pref in pool else (rng.choice(pool) if pool else None)
    path = fonts.get(key) if key else fallback

    accents = style.get("accents") or C.ACCENTS
    base = _BASE_SIZE.get(mode, 88)
    ai = rng.randrange(len(accents))
    for chunk in chunks:
        imp = C._importance(chunk["words"])
        for j, w in enumerate(chunk["words"]):
            w["font_path"] = path
            hero = imp[j] == 2
            w["hero"] = hero
            w["size"] = base * (1.18 if (hero and mode in ("stack", "single")) else 1.0)
            w["accent"] = accents[ai % len(accents)]
            ai += 1
    return chunks


# ---------------------------------------------------------------- rendering --
def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _text(img, xy, text, font, fill, stroke_w=0, stroke_fill=(0, 0, 0, 255), anchor="ms"):
    """Draw (possibly semi-transparent) text on its own layer and alpha-composite it."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(xy, text, font=font, fill=fill, stroke_width=stroke_w,
           stroke_fill=stroke_fill, anchor=anchor)
    img.alpha_composite(layer)


def _shadow_text(img, cx, base, text, font, fill, stroke_w, shadow=(4, 8)):
    _text(img, (cx + shadow[0], base + shadow[1]), text, font, (0, 0, 0, 200),
          stroke_w, (0, 0, 0, 200))
    _text(img, (cx, base), text, font, fill, stroke_w, (0, 0, 0, 255))


def _render_box(chunk, idx, pop, style):
    from modules import captions as C
    if "layout" not in chunk:
        chunk["layout"] = C._layout(chunk)
    placed, height = chunk["layout"]
    img = Image.new("RGBA", (TARGET_W, height), (0, 0, 0, 0))
    dummy = ImageDraw.Draw(img)
    for j, w in enumerate(chunk["words"]):
        active = j == idx
        scale = (1.10 * (1.07 if pop else 1.0)) if active else 1.0
        font = C._load_font(w["font_path"], w["size"] * scale)
        cx, base = placed[j]["cx"], placed[j]["baseline"]
        asc, desc = font.getmetrics()
        tw = dummy.textlength(w["text"], font=font)
        if active:
            pad = 15
            dummy_box = (cx - tw / 2 - pad, base - asc + 2, cx + tw / 2 + pad, base + desc + 2)
            ImageDraw.Draw(img).rounded_rectangle(
                dummy_box, radius=30, fill=tuple(w["accent"]) + (255,))
            txt = (15, 15, 15, 255) if _lum(w["accent"]) > 140 else (255, 255, 255, 255)
            _text(img, (cx, base), w["text"], font, txt)
        else:
            alpha = 255 if j < idx else 150
            _shadow_text(img, cx, base, w["text"], font, (255, 255, 255, alpha),
                         max(5, int(w["size"] * 0.07)))
    return img


def _render_karaoke(chunk, idx, pop, style):
    from modules import captions as C
    if "layout" not in chunk:
        chunk["layout"] = C._layout(chunk)
    placed, height = chunk["layout"]
    img = Image.new("RGBA", (TARGET_W, height), (0, 0, 0, 0))
    for j, w in enumerate(chunk["words"]):
        active = j == idx
        scale = (1.12 * (1.06 if pop else 1.0)) if active else 1.0
        font = C._load_font(w["font_path"], w["size"] * scale)
        cx, base = placed[j]["cx"], placed[j]["baseline"]
        stroke = max(5, int(w["size"] * 0.08))
        if active:
            _shadow_text(img, cx, base, w["text"], font, tuple(w["accent"]) + (255,), stroke)
        elif j < idx:
            _shadow_text(img, cx, base, w["text"], font, (255, 255, 255, 255), stroke)
        else:
            _text(img, (cx, base), w["text"], font, (255, 255, 255, 105),
                  max(3, stroke // 2), (0, 0, 0, 110))
    return img


def _render_subtitle(chunk, idx, pop, style):
    from modules import captions as C
    if "layout" not in chunk:
        chunk["layout"] = C._layout(chunk)
    placed, height = chunk["layout"]
    img = Image.new("RGBA", (TARGET_W, height), (0, 0, 0, 0))
    dummy = ImageDraw.Draw(img)
    xs0, xs1 = [], []
    for j, w in enumerate(chunk["words"]):
        xs0.append(placed[j]["cx"] - placed[j]["width"] / 2)
        xs1.append(placed[j]["cx"] + placed[j]["width"] / 2)
    pad_x, pad_y = 38, 22
    left, right = max(10, min(xs0) - pad_x), min(TARGET_W - 10, max(xs1) + pad_x)
    ImageDraw.Draw(img).rounded_rectangle(
        (left, 18, right, height - 18), radius=34, fill=(0, 0, 0, 120))
    for j, w in enumerate(chunk["words"]):
        font = C._load_font(w["font_path"], w["size"])
        cx, base = placed[j]["cx"], placed[j]["baseline"]
        fill = tuple(w["accent"]) + (255,) if j == idx else (255, 255, 255, 255)
        _text(img, (cx, base), w["text"], font, fill, max(3, int(w["size"] * 0.06)),
              (0, 0, 0, 255))
    return img


def _render_single(chunk, idx, pop, style):
    from modules import captions as C
    w = chunk["words"][idx]
    size = w["size"] * (1.10 if pop else 1.0)
    font = C._load_font(w["font_path"], size)
    dummy = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    tw = dummy.textlength(w["text"], font=font)
    max_w = TARGET_W - 120
    if tw > max_w:
        size = size * max_w / tw
        font = C._load_font(w["font_path"], size)
    asc, desc = font.getmetrics()
    height = int(asc + desc + 90)
    img = Image.new("RGBA", (TARGET_W, height), (0, 0, 0, 0))
    fill = tuple(w["accent"]) + (255,) if w["hero"] else (255, 255, 255, 255)
    _shadow_text(img, TARGET_W / 2, 45 + asc, w["text"], font, fill,
                 max(7, int(size * 0.09)), shadow=(5, 10))
    return img


def _render_stack(chunk, idx, pop, style):
    from modules import captions as C
    words = chunk["words"][: idx + 1]
    dummy = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    rows, y = [], 36
    for j, w in enumerate(words):
        active = j == idx
        size = w["size"] * ((1.08 * (1.06 if pop else 1.0)) if active else 0.92)
        font = C._load_font(w["font_path"], size)
        tw = dummy.textlength(w["text"], font=font)
        if tw > TARGET_W - 120:
            size *= (TARGET_W - 120) / tw
            font = C._load_font(w["font_path"], size)
        asc, desc = font.getmetrics()
        rows.append((w, font, size, y + asc))
        y += asc + desc * 0.6 + 6
    height = int(y + 60)
    img = Image.new("RGBA", (TARGET_W, max(height, 200)), (0, 0, 0, 0))
    for j, (w, font, size, base) in enumerate(rows):
        fill = tuple(w["accent"]) + (255,) if j == idx else (255, 255, 255, 255)
        _shadow_text(img, TARGET_W / 2, base, w["text"], font, fill,
                     max(6, int(size * 0.09)))
    return img


_RENDERERS = {"box": _render_box, "karaoke": _render_karaoke, "subtitle": _render_subtitle,
              "single": _render_single, "stack": _render_stack}


def render_frame(mode, chunk, idx, pop, style):
    return _RENDERERS[mode](chunk, idx, pop, style)
