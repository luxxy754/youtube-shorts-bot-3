"""
Word-matched stickers for typography captions (the "chubby 3D sticker pops next to the word" look).

Every caption word that has a matching sticker (brain -> 🧠, money -> 💰, alone -> 🥺 ...) gets a
sticker that POPS in with a bounce above the caption, floats a little, then shrinks away.

Where the stickers come from (all free + legal for monetised videos)
--------------------------------------------------------------------
  1. assets/stickers_custom/<word>.png   <- YOUR OWN files win (e.g. brain.png, money.png,
                                            or <emoji-codepoints>.png). Drop anything you are
                                            allowed to use there (own AI-made mascot, CC0 packs).
  2. Microsoft Fluent Emoji 3D  (MIT license)   - the chubby, clay-like 3D look.
  3. Google Noto Emoji          (Apache 2.0)    - fallback if (2) is unreachable.
Downloaded stickers are cached in assets/stickers/. If NOTHING can be fetched for a word the
sticker is silently skipped - captions/video never break because of stickers.

NOT included on purpose: Spider-Man, anime or any other studio characters. They are copyrighted;
using them in monetised Shorts is a fast way to get Content-ID claims / strikes.

Env: STICKERS=1 (on/off), STICKER_MAX (default 7 per video), STICKER_MIN_GAP (default 1.5 s),
     STICKER_SIZE (default 300 px).
"""

import os
import random
import re

import requests
from PIL import Image

TARGET_W = 1080
TARGET_H = 1920

STICKER_DIR = os.path.join("assets", "stickers")
CUSTOM_DIR = os.path.join("assets", "stickers_custom")

ENABLED = os.getenv("STICKERS", "1") == "1"
MAX_STICKERS = int(os.getenv("STICKER_MAX", "7"))
MIN_GAP = float(os.getenv("STICKER_MIN_GAP", "1.5"))
SIZE = int(os.getenv("STICKER_SIZE", "300"))
START_AFTER = 1.2      # keep the hook card at the very start clean
END_BEFORE = 2.0       # keep the end comment-pill area clean

# --------------------------------------------------------------------------------------
# word -> emoji.  Several words share one sticker.  Psychology / relationships / mind topics.
# --------------------------------------------------------------------------------------
_GROUPS = {
    "🧠": "brain mind mental psychology psychological thinking thought thoughts think overthink "
          "overthinking memory remember forget forgot smart intelligence genius subconscious "
          "unconscious cognitive habit habits",
    "❤": "heart love loved loving crush romantic romance affection attached attachment relationship",
    "💔": "heartbreak breakup betrayal betrayed hurt hurts pain painful rejected rejection ghosted "
          "ghosting broken cheating cheat",
    "😭": "cry cries crying sad sadness tears grief lonely loneliness depressed",
    "🥺": "alone ignored needy vulnerable miss missing missed abandoned",
    "😂": "laugh laughs laughing funny joke jokes hilarious ticklish tickle",
    "😍": "attractive beautiful gorgeous attraction",
    "😡": "angry anger mad furious rage hate hatred jealous jealousy envy",
    "😱": "shocking shocked scary scared fear afraid terrifying panic anxiety anxious shock",
    "😴": "sleep sleeping sleepy tired exhausted lazy bored boring dream dreams",
    "🤯": "mindblowing insane crazy wild unbelievable impossible blown",
    "🤔": "why wonder question curious doubt confused confusion maybe",
    "😏": "manipulate manipulation manipulative sneaky flirt flirting tease charming",
    "🤫": "secret secrets hidden hide hiding quiet silence silent private",
    "🤥": "lie lies lying liar fake pretend pretending dishonest",
    "🎭": "mask masks acting persona personality fake-self",
    "👀": "eye eyes look looking watch watching stare staring notice noticed attention observe",
    "👂": "listen listening hear hearing ears",
    "🗣": "talk talking speak speaking words say saying voice conversation",
    "💬": "text texting message messages reply replied comment comments",
    "📱": "phone phones scroll scrolling social instagram tiktok notification notifications screen",
    "💰": "money cash rich wealth wealthy dollars dollar salary pay paid expensive cost price",
    "💸": "spend spending broke waste wasted lose losing",
    "🔥": "fire hot burn burning intense passion lit viral trending",
    "⚡": "fast quick quickly instantly instant energy power electric shock-wave sudden suddenly",
    "⏰": "time clock late deadline hours minutes seconds wait waiting",
    "⏳": "forever years patience slowly",
    "⚠": "warning danger dangerous careful toxic red-flag redflag flags risk",
    "🚩": "flag flags redflags",
    "🚫": "never stop dont don't no nobody refuse reject banned avoid avoiding",
    "✅": "yes true truth real correct right proven fact facts",
    "❌": "wrong false myth mistake mistakes fail failure failed",
    "💪": "strong strength strongest confidence confident discipline muscle gym workout hard",
    "🏆": "win winner winning best success successful champion first top goal goals achieve",
    "🎯": "focus target aim purpose",
    "💡": "idea ideas trick tip tips hack hacks learn smart-tip insight realize realized",
    "🔑": "key secret-key unlock solution",
    "🔒": "lock locked trust trusted privacy control controlled",
    "👑": "king queen boss power-move leader leadership status respect",
    "💀": "dead die dies death kill killed deadly dying",
    "👻": "ghost ghosted disappear vanish",
    "🌍": "world global everyone everybody people society",
    "👥": "people crowd group friends friend social-circle friendship",
    "🤝": "deal agree agreement together teamwork cooperate cooperation help helping",
    "😊": "happy happiness smile smiling smiles joy kind kindness",
    "😎": "cool confident-look attitude swag",
    "🥶": "cold ice freeze freezing cold-hearted",
    "🥵": "sweat sweating nervous embarrassed embarrassing",
    "😳": "awkward blush blushing shy shyness embarrassed-face",
    "🙄": "annoying annoyed ignore eyeroll whatever",
    "🤡": "fool foolish stupid dumb idiot clown",
    "🧲": "attract attracts magnet magnetic pull",
    "🪞": "mirror reflection yourself self selfish ego narcissist narcissism",
    "🎁": "gift gifts surprise surprising reward rewards",
    "🍕": "food eat eating hungry hunger pizza",
    "☕": "coffee morning",
    "🌙": "night midnight late-night",
    "☀": "sun sunny day daylight",
    "🏃": "run running chase chasing escape escaping",
    "🧘": "calm relax relaxed relaxing peace peaceful meditation breathe breathing",
    "🔁": "again repeat repeating cycle loop pattern patterns",
    "📈": "grow growth increase improve improving better rise rising",
    "📉": "decrease drop dropping decline worse",
    "🎬": "movie movies film show scene",
    "🎵": "music song songs sing singing",
    "🧩": "puzzle piece pieces solve figure",
    "🧪": "experiment study studies research science scientists test",
    "👶": "baby babies child children kid kids childhood",
    "👨‍👩‍👧": "family parents mother father mom dad",
    "🤗": "hug hugs hugging comfort comforting",
    "💋": "kiss kissing kisses",
    "🫶": "care caring care-heart appreciate appreciated",
}

_WORD_TO_EMOJI = {}
for _emoji, _words in _GROUPS.items():
    for _w in _words.split():
        _WORD_TO_EMOJI.setdefault(_w, _emoji)

_SUFFIXES = ("ing", "ed", "es", "s", "ly", "er", "ness")


def _norm(word):
    return re.sub(r"[^a-z'-]", "", str(word).lower()).strip("'-")


def emoji_for_word(word):
    """Emoji for a caption word, or None."""
    w = _norm(word)
    if not w or len(w) < 3:
        return None
    if w in _WORD_TO_EMOJI:
        return _WORD_TO_EMOJI[w]
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)]
            for cand in (stem, stem + "e"):
                if cand in _WORD_TO_EMOJI:
                    return _WORD_TO_EMOJI[cand]
    return None


# --------------------------------------------------------------------------------------
# sticker files
# --------------------------------------------------------------------------------------
def _codepoints(emoji, keep_fe0f=True):
    cps = [ord(c) for c in emoji]
    if not keep_fe0f:
        cps = [c for c in cps if c != 0xFE0F]
    return cps


def _candidate_urls(emoji):
    out = []
    for keep in (True, False):
        cps = _codepoints(emoji, keep)
        dash = "-".join(f"{c:x}" for c in cps)
        under = "_".join(f"{c:x}" for c in cps)
        out.append(f"https://fluent-emoji.ciffelia.com/{dash}_3d.webp")
        out.append(f"https://cdn.jsdelivr.net/gh/googlefonts/noto-emoji@main/png/512/emoji_u{under}.png")
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


_failed = set()


def _prepare(img):
    """Trim transparent border, fit into SIZE x SIZE square."""
    img = img.convert("RGBA")
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def _custom_file(word, emoji):
    names = [_norm(word)]
    if emoji:
        names.append("-".join(f"{c:x}" for c in _codepoints(emoji, False)))
    for n in names:
        for ext in (".png", ".webp"):
            p = os.path.join(CUSTOM_DIR, n + ext)
            if n and os.path.isfile(p):
                return p
    return None


def get_sticker_image(word):
    """PIL RGBA sticker for the word, or None (never raises)."""
    try:
        emoji = emoji_for_word(word)
        custom = _custom_file(word, emoji)
        if custom:
            return _prepare(Image.open(custom))
        if not emoji:
            return None
        key = "-".join(f"{c:x}" for c in _codepoints(emoji, False))
        cache = os.path.join(STICKER_DIR, key + ".png")
        if os.path.isfile(cache):
            return Image.open(cache).convert("RGBA")
        if key in _failed:
            return None
        os.makedirs(STICKER_DIR, exist_ok=True)
        for url in _candidate_urls(emoji):
            try:
                r = requests.get(url, timeout=8)
                if r.status_code != 200 or len(r.content) < 500:
                    continue
                import io
                img = _prepare(Image.open(io.BytesIO(r.content)))
                img.save(cache)
                print(f"[stickers] {emoji} downloaded <- {url.split('/')[2]}")
                return img
            except Exception:
                continue
        _failed.add(key)
        print(f"[stickers] no source reachable for {emoji} ({word}) - skipped")
    except Exception as e:
        print(f"[stickers] error for '{word}': {e}")
    return None


# --------------------------------------------------------------------------------------
# planning + clips
# --------------------------------------------------------------------------------------
def plan_stickers(chunks, total_duration, seed=None):
    """
    chunks = caption chunks ({'words': [{'text','t0','t1'}...]}).
    Strongest words win (power words first), then they are spaced out: at most MAX_STICKERS,
    at least MIN_GAP seconds apart, never the same sticker twice in a row.
    Returns [{'word','t0','dur'}] sorted by time.
    """
    try:
        from modules.captions import POWER_WORDS
    except Exception:
        POWER_WORDS = set()

    cands = []
    for c in chunks:
        for w in c["words"]:
            if START_AFTER <= w["t0"] <= total_duration - END_BEFORE and emoji_for_word(w["text"]):
                low = _norm(w["text"])
                score = (2.0 if low in POWER_WORDS else 1.0) + min(len(low), 10) / 20.0
                cands.append((score, w))
    cands.sort(key=lambda x: (-x[0], x[1]["t0"]))

    chosen = []
    for _score, w in cands:
        if len(chosen) >= MAX_STICKERS:
            break
        if any(abs(w["t0"] - o["t0"]) < MIN_GAP for o in chosen):
            continue
        chosen.append(w)
    chosen.sort(key=lambda w: w["t0"])

    picked, last_emoji = [], None
    for w in chosen:
        e = emoji_for_word(w["text"])
        if e == last_emoji:
            continue
        picked.append({"word": w["text"], "t0": float(w["t0"]),
                       "dur": min(1.35, max(0.9, float(w["t1"]) - float(w["t0"]) + 0.7))})
        last_emoji = e
    return picked


def _scale_at(t, dur):
    """Bounce in (overshoot), gentle breathing, quick shrink out."""
    import math
    pop = 0.22
    out = 0.16
    if t < pop:
        p = t / pop
        s = 0.15 + 0.85 * p + 0.30 * math.sin(math.pi * p)   # overshoot ~1.15
    elif t > dur - out:
        s = max(0.05, (dur - t) / out)
    else:
        s = 1.0 + 0.04 * math.sin((t - pop) * 7.0)
    return max(0.05, s)


def build_sticker_clips(chunks, total_duration, caption_center_y, seed=None):
    """Returns moviepy clips (empty list when disabled / nothing matched). Never raises."""
    if not ENABLED:
        return []
    try:
        import numpy as np
        from moviepy.editor import ImageClip
    except Exception as e:
        print(f"[stickers] moviepy unavailable: {e}")
        return []

    rng = random.Random(seed)
    plan = plan_stickers(chunks, total_duration, seed)
    clips, side_cycle = [], [0, -1, 1]
    rng.shuffle(side_cycle)
    base_y = max(260, int(caption_center_y - 400))

    for i, p in enumerate(plan):
        img = get_sticker_image(p["word"])
        if img is None:
            continue
        try:
            side = side_cycle[i % 3]
            cx = TARGET_W // 2 + side * rng.randint(230, 300)
            cy = base_y + rng.randint(-40, 40)
            dur = p["dur"]
            base = img.width
            tilt = rng.choice([-1, 1])

            clip = ImageClip(np.array(img), transparent=True).set_start(p["t0"]).set_duration(dur)
            clip = clip.resize(lambda t, d=dur: _scale_at(t, d))

            def pos(t, d=dur, cx=cx, cy=cy, base=base, tilt=tilt):
                import math
                s = _scale_at(t, d)
                bob = 10 * math.sin(t * 5.0) * tilt
                return (cx - base * s / 2.0, cy - base * s / 2.0 + bob)

            clips.append(clip.set_position(pos))
        except Exception as e:
            print(f"[stickers] clip failed for '{p['word']}': {e}")
    print(f"[stickers] {len(clips)} sticker(s) added: "
          + ", ".join(f"{p['word']}@{p['t0']:.1f}s" for p in plan[:len(clips)]))
    return clips
