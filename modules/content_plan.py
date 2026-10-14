"""
Per-channel content plan.

Why this exists
---------------
Before this file, all 3 channels drew from the SAME psychology category list
and the SAME format list. The only thing that changed between channels was the
voice and the caption skin. YouTube's reused-content heuristic looks at the
CONTENT, not the skin, so three channels with the same category + same beat
order is the single biggest risk in the project.

This module gives each channel:

  * its OWN sub-niche pool (relationship / money / sleep-focus-learning),
  * its OWN format rotation so two channels never write the same beat order
    on the same day,
  * a deterministic per-video "content fingerprint" (category + format + a
    title ending style + a description opener style) that brain.py uses.

Nothing here talks to the network. Pure stdlib.
"""

import hashlib
import os
import random
from datetime import datetime


# ---------------------------------------------------------------------------
# Sub-niche pools. Channel index -> list of category strings.
# Add or reorder inside a pool any time. Pools never share entries on purpose.
# ---------------------------------------------------------------------------
CHANNEL_NICHES = {
    0: {
        "name": "relationships and everyday social behaviour",
        "categories": [
            "friendship, texting, jealousy and loneliness (well-established findings only)",
            "psychology of first impressions, attraction and body language (well-established findings)",
            "everyday human behaviour: why people do small things with family, friends and co-workers",
            "social psychology: crowds, conformity, persuasion, why we copy others",
            "emotions, mood and how people react when someone hurts them",
        ],
    },
    1: {
        "name": "money, shopping and consumer brain",
        "categories": [
            "shopping, money and attention psychology: why we buy, scroll and get hooked",
            "decision making under money pressure and why smart people still overspend",
            "habit loops around spending, subscriptions and impulse buys",
            "how prices, packaging and store layout change what we buy",
            "why free trials, discounts and 'limited time' actually work on the brain",
        ],
    },
    2: {
        "name": "sleep, focus, learning and memory",
        "categories": [
            "sleep, dreams and tiredness: what they do to mood and behaviour",
            "focus and attention: why the mind wanders, multitasking, distraction",
            "childhood, learning and curiosity: how people learn and why habits stick",
            "memory: why we forget, false memories, why we remember some things forever",
            "psychology of habits, procrastination and willpower",
        ],
    },
}

DEFAULT_CHANNEL = 0


# ---------------------------------------------------------------------------
# Format pools. Different channels get different slice of this dict.
# Two channels can share a format TYPE across videos, but never in the same
# run - the plan picks per-channel from a channel-biased subset.
# ---------------------------------------------------------------------------
ALL_FORMATS = [
    "psychology_effect",
    "myth_vs_truth",
    "psychology_fact",
    "personal_what_if",
    "top3",
    "story",
    "question_first",
]

# Which formats each channel leans toward. This is a SOFT bias: the plan
# picks 3-4 of these per run, shuffled, so the channel still varies.
CHANNEL_FORMAT_BIAS = {
    0: ["psychology_effect", "story", "personal_what_if", "myth_vs_truth"],
    1: ["myth_vs_truth", "psychology_fact", "question_first", "top3"],
    2: ["personal_what_if", "top3", "psychology_fact", "story", "question_first"],
}


# ---------------------------------------------------------------------------
# Title endings and description openers - picked per video so titles never
# all end with '... #Shorts'. brain.py feeds these into the prompt.
# ---------------------------------------------------------------------------
TITLE_ENDINGS = [
    ("plain",      "end with no hashtag at all"),
    ("shorts",     "end with a single #Shorts (capital S)"),
    ("shorts_psy", "end with #shorts #psychology"),
    ("shorts_fct", "end with #shorts #facts"),
    ("topic_only", "end with ONE topic hashtag that matches this exact video (#relationships, #money, #sleep, #focus, #memory, #habits, #attention)"),
    ("mindset",    "end with #shorts #mindset"),
    ("none",       "end with no hashtag at all"),
]

DESCRIPTION_OPENERS = [
    "Open with a one-line question the viewer has asked themselves in the last week.",
    "Open with a one-line statement of the surprising result.",
    "Open with a one-line everyday scene the viewer will recognise instantly.",
    "Open with a one-line myth and immediately undercut it with '...but that is not what happens.'",
    "Open with a one-line 'here is why you do this' promise.",
    "Open with a one-line contrast between what people think and what actually happens.",
]

# Description closers - replaces the hardcoded "Tell me in the comments!"
DESCRIPTION_CTAS = [
    "Tell me in the comments!",
    "Drop your answer below.",
    "Let me know in the comments.",
    "Which one are you?",
    "Comment your honest answer.",
    "I read every reply.",
    "Say it in the comments.",
]


def _seed_for_channel(channel_index, salt=""):
    src = (
        str(channel_index)
        + salt
        + os.getenv("GITHUB_RUN_ID", "")
        + os.getenv("GITHUB_RUN_NUMBER", "")
        + os.getenv("GITHUB_RUN_ATTEMPT", "")
        + datetime.utcnow().isoformat()
    )
    return int(hashlib.sha256(src.encode()).hexdigest(), 16) % (2**32)


def niche_for_channel(channel_index):
    return CHANNEL_NICHES.get(channel_index, CHANNEL_NICHES[DEFAULT_CHANNEL])


def build_plan(channel_index, recent_history=None, seed=None):
    """
    Build the per-video content fingerprint for one channel.

    Returns a dict:
      {
        "channel_index": int,
        "niche_name":    str,
        "category":      str,   # the exact category string
        "format":        str,   # one key from brain.FORMATS
        "title_ending":  (tag, instruction),
        "description_opener": str,
        "description_cta":    str,
        "avoid":         [str], # facts/titles from recent history to avoid
      }
    """
    rnd = random.Random(seed if seed is not None else _seed_for_channel(channel_index))

    niche = niche_for_channel(channel_index)
    recent = recent_history or []
    recent_cats = [r.get("category") for r in recent[-10:]]

    cats = [c for c in niche["categories"] if c not in recent_cats]
    if not cats:
        cats = niche["categories"]
    category = rnd.choice(cats)

    pool = CHANNEL_FORMAT_BIAS.get(channel_index, ALL_FORMATS)
    recent_fmts = [r.get("format") for r in recent[-2:]]
    pool = [f for f in pool if f not in recent_fmts] or pool
    fmt = rnd.choice(pool)

    title_ending = rnd.choice(TITLE_ENDINGS)
    desc_opener = rnd.choice(DESCRIPTION_OPENERS)
    desc_cta = rnd.choice(DESCRIPTION_CTAS)

    avoid = [
        r.get("fact") or r.get("title")
        for r in recent[-40:]
        if (r.get("fact") or r.get("title"))
    ]

    plan = {
        "channel_index": channel_index,
        "niche_name": niche["name"],
        "category": category,
        "format": fmt,
        "title_ending": title_ending,
        "description_opener": desc_opener,
        "description_cta": desc_cta,
        "avoid": avoid,
    }

    print(f"[plan] ch{channel_index + 1} niche='{niche['name']}'")
    print(f"[plan]   category: {category}")
    print(f"[plan]   format:   {fmt}")
    print(f"[plan]   title ending: {title_ending[0]}")
    return plan
