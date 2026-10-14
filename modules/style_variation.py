"""
Per-video randomized "editorial fingerprint".

Gives every video its own style DNA so no two videos share a template.
Adds transition-specific fields (density, palette bias, SFX gain) so
the advanced transitions stay random and varied across videos and channels.
"""

import os
import random
import hashlib
from datetime import datetime


ACCENT_PALETTES = [
    [(255, 221, 0), (0, 255, 140)],
    [(255, 255, 255), (255, 82, 82)],
    [(0, 229, 255), (255, 214, 0)],
    [(50, 255, 126), (255, 255, 255)],
    [(255, 105, 180), (255, 255, 0)],
    [(255, 255, 255), (140, 90, 255)],
    [(255, 200, 60), (255, 60, 60)],
    [(160, 255, 90), (255, 255, 255)],
    [(255, 240, 200), (255, 100, 0)],
    [(0, 255, 255), (255, 0, 200)],
]

HOOK_CARD_FILLS = [
    (0, 0, 0, 200),
    (12, 12, 30, 210),
    (30, 8, 8, 200),
    (8, 22, 40, 205),
    (0, 0, 0, 0),
]

HOOK_CARD_TEXT_COLORS = [
    (255, 235, 59, 255),
    (255, 255, 255, 255),
    (0, 255, 140, 255),
    (255, 120, 120, 255),
    (255, 200, 60, 255),
]

COMMENT_PILL_FILLS = [
    (0, 0, 0, 190),
    (10, 10, 25, 205),
    (25, 8, 8, 195),
    (8, 20, 35, 200),
]
COMMENT_PILL_BORDERS = [
    (255, 214, 0, 255),
    (0, 255, 140, 255),
    (255, 255, 255, 240),
    (255, 105, 180, 255),
    (0, 229, 255, 255),
]

PREFERRED_FONTS = ["anton", "bangers", "luckiest", "lilita", "bebas", "marker", "poppins"]
HOOK_FONT_SIZES = [96, 100, 104, 108, 112, 116]


def _seed_from_env():
    src = (
        os.getenv("GITHUB_RUN_ID", "")
        + os.getenv("GITHUB_RUN_NUMBER", "")
        + os.getenv("GITHUB_RUN_ATTEMPT", "")
        + datetime.utcnow().isoformat()
    )
    return int(hashlib.sha256(src.encode()).hexdigest(), 16) % (2**32)


def get_style(seed=None, channel_index=0):
    rnd = random.Random(seed if seed is not None else _seed_from_env())

    style = {
        # ---------- channel awareness ----------
        "channel_index": channel_index,

        # ---------- caption layout ----------
        "caption_y_ratio": rnd.choice([0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70]),
        "caption_x_jitter": rnd.choice([-40, -25, -12, 0, 0, 12, 25, 40]),
        "caption_rotation": rnd.choice([-2, -1, 0, 0, 0, 1, 2]),
        "words_per_chunk": rnd.choice([1, 2, 2, 2, 3]),
        "active_scale": rnd.choice([1.10, 1.15, 1.20, 1.25]),
        "size_range": rnd.choice([
            (68, 94, 128),
            (72, 100, 138),
            (66, 90, 122),
            (74, 104, 144),
            (70, 96, 132),
        ]),
        "accents": rnd.choice(ACCENT_PALETTES),
        "font_family": rnd.choice(PREFERRED_FONTS),
        "stroke_scale": rnd.choice([0.09, 0.10, 0.11, 0.12]),

        # ---------- hook card ----------
        "hook_y_ratio": rnd.choice([0.14, 0.17, 0.20, 0.23, 0.26]),
        "hook_tilt": rnd.choice([-2.5, -1.5, 0, 0, 0, 1.5, 2.5]),
        "hook_fill": rnd.choice(HOOK_CARD_FILLS),
        "hook_text_color": rnd.choice(HOOK_CARD_TEXT_COLORS),
        "hook_duration": rnd.choice([2.4, 2.8, 3.0, 3.2, 3.5]),
        "hook_radius": rnd.choice([18, 26, 36, 48]),
        "hook_font_size": rnd.choice(HOOK_FONT_SIZES),

        # ---------- end question ----------
        "end_q_y_ratio": rnd.choice([0.72, 0.75, 0.78, 0.81]),
        "end_q_duration": rnd.choice([1.4, 1.7, 2.0]),

        # ---------- comment pill ----------
        "comment_y_ratio": rnd.choice([0.74, 0.77, 0.79, 0.82]),
        "comment_duration": rnd.choice([2.2, 2.6, 2.8, 3.0]),
        "comment_pill_fill": rnd.choice(COMMENT_PILL_FILLS),
        "comment_pill_border": rnd.choice(COMMENT_PILL_BORDERS),

        # ---------- rhythm / pacing ----------
        "scene_gap": rnd.choice([0.03, 0.05, 0.07, 0.09]),
        "shot_seconds": rnd.choice([1.8, 2.0, 2.2, 2.5, 2.8]),

        # ---------- SFX density ----------
        "sfx_whoosh_gain": rnd.choice([0.35, 0.45, 0.55, 0.65]),
        "sfx_pop_gain": rnd.choice([0.12, 0.18, 0.22]),
        "sfx_click_gain": rnd.choice([0.25, 0.33, 0.40]),

        # ---------- visual grade ----------
        "grade_contrast": rnd.choice([1.03, 1.05, 1.06, 1.08]),
        "grade_saturation": rnd.choice([1.08, 1.12, 1.15, 1.18]),
        "vignette_angle": rnd.choice(["PI/7", "PI/6", "PI/5"]),
        "grain_strength": rnd.choice([3, 4, 5, 6]),

        # ---------- transitions (advanced pack) ----------
        # higher density = more transitions, fewer hard cuts
        "transition_density": rnd.choice([0.65, 0.75, 0.80, 0.85, 0.90]),
        # palette bias (may be overridden by channel_index in transitions module)
        "transition_palette_bias": rnd.choice(["fast", "medium", "slow"]),
        # how loud the transition-specific SFX will be
        "sfx_transition_gain": rnd.choice([0.55, 0.62, 0.70, 0.78]),

        # ---------- pro fx strength (advanced pack: everything on) ----------
        "fx_base_zoom": rnd.choice([1.04, 1.05, 1.06, 1.07]),
        "fx_hook_push": rnd.choice([0.26, 0.32, 0.38]),
        "fx_cut_kick": rnd.choice([0.07, 0.09, 0.11]),
        "fx_shake_px": rnd.choice([10.0, 14.0, 18.0]),
    }

    print(f"[style] ch={channel_index} caption_y=%.2f x_jit=%+d rot=%+d deg | "
          "hook_y=%.2f tilt=%+.1f deg | font=%s | accents=%s | gap=%.2fs shot=%.1fs | "
          "transitions=%.2f density | sfx_gain=%.2f"
          % (style["caption_y_ratio"], style["caption_x_jitter"],
             style["caption_rotation"], style["hook_y_ratio"], style["hook_tilt"],
             style["font_family"], style["accents"][0], style["scene_gap"],
             style["shot_seconds"], style["transition_density"],
             style["sfx_transition_gain"]))
    return style
