"""
Script engine (Gemini) - ENGLISH psychology-facts Shorts.

- Narration, captions, title and description are all English. The TTS voice is a US English neural voice.
- Two passes: writer -> strict fact-check/editor (no pop-psychology myths, no invented stats).
- Built for retention: 20-26 second video, a hook in the first 2 seconds, a re-hook mid-way,
  and a LOOP ending whose last words flow grammatically into the first words, so the Short replays
  seamlessly and people watch it 2-3 times.
- Topic history stores the last facts and titles; Gemini is told to avoid them.
"""

import json
import os
import random
import re
import time
from datetime import datetime

from google.genai import types

# Order = priority. Each model has its OWN quota, so a longer list = more free calls per day.
# Dead names (404) are skipped automatically. Override without editing code:
#   GEMINI_MODELS: 'gemini-3.8-flash,gemini-3.6-flash'   (in run.yml)
_DEFAULT_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]
MODELS = [
    m.strip()
    for m in os.getenv("GEMINI_MODELS", ",".join(_DEFAULT_MODELS)).split(",")
    if m.strip()
]
_DEAD_MODELS = set()

MIN_SCENES = 7
MAX_SCENES = 10
HISTORY_KEY = "_recent"
HISTORY_LIMIT = 90

# Every category maps to footage that really exists on Pexels/Pixabay.
CATEGORIES = [
    "everyday human behaviour: why people do small things in daily life (phone, friends, family, work)",
    "psychology of first impressions, attraction and body language (only well-established findings)",
    "psychology of habits, procrastination, motivation and willpower",
    "emotions, mood, stress and how people react to them",
    "decision making, cognitive biases and why smart people make silly choices",
    "memory: why we forget, false memories, why we remember some things forever",
    "social psychology: crowds, conformity, persuasion, why we copy others",
    "friendship, relationships, texting, jealousy and loneliness (well-established findings only)",
    "shopping, money and attention psychology: why we buy, scroll and get hooked",
    "sleep, dreams and tiredness: what they do to mood and behaviour",
    "focus and attention: why the mind wanders, multitasking, distraction",
    "childhood, learning and curiosity: how people learn and why praise/habits stick",
]

FORMATS = {
    "psychology_effect": (
        "ONE well-established psychology effect explained through a daily-life situation the viewer "
        "recognises instantly ('aapne bhi ye kabhi mehsoos kiya hoga'). Use the real effect, simple words."
    ),
    "myth_vs_truth": (
        "Start with a very common belief about people/behaviour, then reveal the truth with a real reason. "
        "Hook = the belief stated as if it were true, then flip it."
    ),
    "psychology_fact": (
        "ONE surprising, verifiable psychology fact about human behaviour, explained step by step. "
        "Hook = the surprising result, explanation comes after."
    ),
    "personal_what_if": (
        "A 'jab aap X karte ho to aapke saath kya hota hai' situation grounded in real psychology research. "
        "Speak directly to the viewer (aap). Only real, well-established findings - "
        "no invented numbers, no fake 'X will ruin you' claims."
    ),
    "top3": (
        "Three real, related psychology facts, each in 2-3 short scenes, escalating so that the "
        "third is the most surprising. Hook promises 'teen' (three) things."
    ),
}

CTA_STYLES = [
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the reason', '...and it all comes down to this:') whose words continue grammatically into the first words of scene 1, so the video replays seamlessly",
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the reason', '...and it all comes down to this:') whose words continue grammatically into the first words of scene 1, so the video replays seamlessly",
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the reason', '...and it all comes down to this:') whose words continue grammatically into the first words of scene 1, so the video replays seamlessly",
    "a REWATCH line: a final twist that makes the hook mean something new ('Now watch the first line again - it hits different.' style, in your own words) and flows into scene 1",
]


# ----------------------------------------------------------- history ----
def load_history(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_history(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"history save failed: {e}")


def record_history(path, script):
    data = load_history(path)
    recent = data.get(HISTORY_KEY, [])
    recent.append({
        "date": datetime.utcnow().isoformat(timespec="seconds"),
        "category": script.get("category", ""),
        "format": script.get("format", ""),
        "title": script.get("title", ""),
        "fact": script.get("core_fact", ""),
    })
    data[HISTORY_KEY] = recent[-HISTORY_LIMIT:]
    save_history(path, data)


def attach_video_id(path, video_id, channel_name=""):
    """Latest history entry (jisme abhi video_id nahi hai) par uploaded video ka ID jod do."""
    data = load_history(path)
    recent = data.get(HISTORY_KEY, [])
    for entry in reversed(recent):
        if not entry.get("video_id"):
            entry["video_id"] = video_id
            if channel_name:
                entry["channel"] = channel_name
            break
    data[HISTORY_KEY] = recent
    save_history(path, data)


def pick_plan(history_path):
    recent = load_history(history_path).get(HISTORY_KEY, [])
    recent_cats = [r.get("category") for r in recent[-8:]]
    recent_fmts = [r.get("format") for r in recent[-2:]]

    cats = [c for c in CATEGORIES if c not in recent_cats] or CATEGORIES
    fmts = [f for f in FORMATS if f not in recent_fmts] or list(FORMATS)

    return {
        "category": random.choice(cats),
        "format": random.choice(fmts),
        "cta": random.choice(CTA_STYLES),
        "avoid": [r.get("fact") or r.get("title") for r in recent[-40:] if (r.get("fact") or r.get("title"))],
    }


# ------------------------------------------------ narration sanitising ----
def sanitize_narration(text):
    """Clean text for the English TTS voice: keep letters, digits and ,.?!'-: ; spell out symbols."""
    text = str(text)
    text = text.replace("%", " percent").replace("&", " and ").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u2014", " - ").replace("\u2013", " - ").replace("\u2026", "...")
    text = re.sub(r"[\"\u201c\u201d`*_#\[\]{}()<>/\\|~^@]", " ", text)
    # emojis / symbols the voice would stumble on
    text = re.sub(r"[^\w\s,.?!'\-:;]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+([,.?!:;])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# -------------------------------------------------------------- Gemini ----
def _extract_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    return json.loads(raw[start:end + 1])


def _ask(client, prompt, temperature=1.0, max_rounds=3, base_wait=10):
    last = None
    for rnd in range(1, max_rounds + 1):
        live = [m for m in MODELS if m not in _DEAD_MODELS]
        if not live:
            break
        for model in live:
            try:
                print(f"[Gemini round {rnd}/{max_rounds}] {model}")
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=temperature,
                        top_p=0.95,
                        response_mime_type="application/json",
                    ),
                )
                return _extract_json(resp.text)
            except Exception as e:
                last = e
                msg = str(e)
                print(f"   {model} failed: {msg[:160]}")
                if "404" in msg or "NOT_FOUND" in msg:
                    _DEAD_MODELS.add(model)  # never retry a removed model in this run
        if rnd < max_rounds:
            time.sleep(base_wait * rnd)
    raise RuntimeError(f"Gemini failed after all retries: {last}")


_DEVA = re.compile(r"[ऀ-ॿ]")

MAX_SCENE_WORDS = 13
MIN_TOTAL_WORDS = 52
MAX_TOTAL_WORDS = 80


def normalize_and_validate(data):
    """Return (script, problems). Script is cleaned; problems is a list of strings."""
    problems = []
    if not isinstance(data, dict):
        return None, ["not a dict"]

    scenes = []
    for sc in data.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        narration = sanitize_narration(sc.get("narration", ""))
        if not narration:
            continue
        keyword = str(sc.get("search_keyword") or sc.get("visual_keyword") or "").strip()
        keyword_alt = str(sc.get("search_keyword_alt") or "").strip()
        caption = str(sc.get("caption") or "").strip()
        # English: the caption text IS the narration (roman kept as an alias for old code paths)
        scenes.append({"narration": narration, "caption": caption,
                       "search_keyword": keyword, "search_alt": keyword_alt, "roman": narration})

    if not (MIN_SCENES <= len(scenes) <= MAX_SCENES):
        problems.append(f"scene count {len(scenes)} not in {MIN_SCENES}-{MAX_SCENES}")

    for i, sc in enumerate(scenes, 1):
        words = sc["narration"].split()
        if _DEVA.search(sc["narration"]):
            problems.append(f"scene {i} contains Devanagari (must be English)")
        if len(words) > MAX_SCENE_WORDS:
            problems.append(f"scene {i} too long ({len(words)} words)")
        if not sc["search_keyword"]:
            problems.append(f"scene {i} missing search_keyword")

    if scenes and len(scenes[0]["narration"].split()) > 9:
        problems.append("hook longer than 9 words")

    total_words = sum(len(s["narration"].split()) for s in scenes)
    if scenes and not (MIN_TOTAL_WORDS <= total_words <= MAX_TOTAL_WORDS):
        problems.append(f"total words {total_words} outside {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS}")

    comment_cta = sanitize_narration(data.get("comment_cta", ""))
    cta_words = comment_cta.split()
    if len(cta_words) > 11:
        comment_cta = " ".join(cta_words[:11])
    if comment_cta and not comment_cta.endswith(("?", "!")):
        comment_cta += "?"

    tags = data.get("tags") or []
    script = {
        "comment_cta": comment_cta,
        "title": str(data.get("title", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
        "core_fact": str(data.get("core_fact", "")).strip(),
        "hook_text": str(data.get("hook_text", "")).strip(),
        "scenes": scenes,
    }
    if not script["title"]:
        problems.append("missing title")
    return script, problems


# ------------------------------------------------------------- prompts ----
_SCHEMA = """{
  "core_fact": "one sentence stating the main psychology fact (used to avoid repeats)",
  "title": "English title, max 58 chars, one emoji, no hashtags, specific + curiosity-driven, honest (never generic like 'You Won't Believe This')",
  "hook_text": "3-6 word on-screen text shown in the FIRST 2 seconds, English, makes sense with sound off, opens a curiosity gap that the video really pays off",
  "comment_cta": "ONE short on-screen question for the last 2-3 seconds, 4-9 words, that makes people answer in the comments (e.g. 'Have you ever caught yourself doing this?'). Must relate to THIS video's topic, no 'like/subscribe'",
  "description": "2-3 short English lines + one line of search keywords. No hashtags.",
  "tags": ["15-20 lowercase english tags"],
  "scenes": [
    {
      "narration": "ONE spoken English sentence (or fragment), 4-12 words",
      "caption": "2-4 word on-screen emphasis text, English",
      "search_keyword": "2-4 english words: a person doing something filmable that shows THIS sentence",
      "search_keyword_alt": "2-3 simpler english words, backup footage for the same sentence"
    }
  ]
}"""


def _writer_prompt(plan):
    avoid = "\n".join(f"- {a}" for a in plan["avoid"]) or "- (nothing yet)"
    return f"""
You are the head writer of a top English-language YouTube Shorts channel about PSYCHOLOGY FACTS
(why people think, feel and behave the way they do). No brain tricks, no animal/space/random trivia.
Write ONE fresh 20-26 second Short. The goal is MAXIMUM "stayed to watch" and REPLAYS: most viewers should
watch to the end and then watch again 2-3 times because the ending flows straight back into the start.

CATEGORY: {plan['category']}
FORMAT: {plan['format']} -> {FORMATS[plan['format']]}
ENDING STYLE: {plan['cta']}

DO NOT repeat or paraphrase any of these earlier videos:
{avoid}
Also avoid the internet's most overused facts and anything that failed replication or is pop-psychology myth:
10% of the brain, left/right brain, learning styles, power posing, ego depletion, the Mozart effect, 10,000 hours,
the 7-38-55 communication rule, 'you only use X', goldfish attention span, Stanford prison experiment as proof,
marshmallow test as destiny, 21 days to form a habit, MBTI claims, 'people can read minds from microexpressions'.
Pick a well-supported finding a curious person would say "wait, really?" to - and that you would bet money on.

HOOK (scene 1) - THE MOST IMPORTANT LINE
- Max 8 words. The first 3 words must already create shock, a warning, or an open question.
- No greeting, no intro, never start with 'Did you know' or 'Today'. Start mid-thought, like the
  story is already happening.
- Use ONE of these patterns (styles only, do NOT copy the examples):
  1. A bold true claim that sounds wrong: 'Your brain lies to you every single day.'
  2. A warning to the viewer: 'Never say this to someone who is angry.'
  3. A direct 'you' question: 'Why do you replay embarrassing moments at 3 AM?'
  4. A specific stakes line: 'Three seconds. That is all it takes.'
  5. A "stop doing this" command with a hidden reason: 'Stop touching things in stores. Here's why.'
  6. A "they want you to" suspicion question: 'Why do shops WANT you to touch things?'
- Open a CURIOSITY GAP with a concrete detail the viewer must see resolved, not a vague 'something strange'.
- The hook must be truthfully paid off by the last scenes. No clickbait lies, no invented studies.
- hook_text (separate field) is the big text on the very first frame; it must work with the sound off.
  Make it a VISUAL SHOCK: 3-6 words, one power word (STOP, NEVER, WARNING, LIES, SECRET, WHY...), no full stop.
- PACING: no slow setup. Every sentence must either add a new fact or raise the stakes; cut every word that is not needed.

ACCURACY (non-negotiable)
- Only real, well-established findings. If you are not sure, choose a different fact.
- No invented statistics, no fake 'scientists found'. Round, defensible wording ('about', 'roughly', 'most people').
- No medical fear-mongering, no diagnosing the viewer.

LANGUAGE AND VOICE (this is read by a US English neural voice, so write for the EAR)
- Natural spoken American English, like a smart friend telling a story. Simple everyday words a
  12-year-old understands. Contractions always (you're, it's, don't, here's).
- Short punchy sentences. Mix 4-word and 11-word lines so the rhythm is never flat. Max {MAX_SCENE_WORDS} words per scene.
- Use commas, '...' and '?' on purpose: put a comma or '...' right before the big reveal, and '?' for real questions.
- Spell numbers as words when small ('three seconds', 'twenty percent'); no symbols, no emojis, no hashtags.
- Speak to the viewer: 'you', 'your'. Make it feel personal ('you've done this').
- No filler, no 'in this video', no 'let's dive in', no begging for likes/follows.

STRUCTURE (7 to 10 scenes, {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS} words total, about 20-26 seconds when spoken)
1. HOOK (see above).
2. TEASE: one line promising the surprising part ('and the last one will change how you see people' style, in your own
   words, specific to this topic). The viewer must know within 3 seconds WHY to stay.
3-4. The concrete detail, a real example from daily life, then the WHY in plain words.
5. RE-HOOK: a flip or escalation ('but here's the part nobody tells you' style) that adds NEW information.
   Every scene after this ends on a small open loop so nobody swipes away.
Second-last: the twist / most surprising part - the payoff promised by the hook.
Last scene: {plan['cta']} Max 10 words.

COMMENT BAIT (field comment_cta): a separate on-screen question for the last seconds. It must be easy to answer in
one line ('Have you noticed this too?', 'Which one did you do today?', 'Be honest: did this happen to you?'), tied to
this exact topic, never generic. It is NOT spoken, so it does not break the loop.

LOOP RULE (very important): the video auto-replays. The LAST words of the last scene must continue
grammatically into the FIRST words of scene 1, as if the whole Short is one endless sentence.
Example of the mechanism (do NOT copy): last scene '...and that is exactly why' + scene 1 'You never remember things the way they happened.'
Scene 1 must still work as a stand-alone hook. The last scene should also make the hook mean something NEW on
the second watch, so people want to watch it again to catch it.

VISUALS (picture must MATCH the sentence - this is the #1 quality problem, so be strict)
- Psychology is abstract, stock footage is not. For EVERY scene first imagine ONE real, filmable scene of ordinary
  people that illustrates that exact sentence, then describe it as the keyword.
  Show the SITUATION, not the concept: never 'cognitive dissonance' or 'confirmation bias' -
  write 'man arguing with friend', 'woman scrolling phone in bed', 'student studying late night',
  'people laughing at cafe table', 'person looking at mirror', 'friends texting phone', 'crowd waiting bus'.
- search_keyword: English, 2-4 words = a person + an action (+ place). Concrete nouns only, no abstract words,
  no brand or person names. Prefer simple things that certainly exist on Pexels (woman, man, friends,
  student, child, couple, office, cafe, phone, bed, mirror, street, kitchen, gym, classroom).
- search_keyword_alt: a DIFFERENT, even simpler backup for the same sentence (2-3 words, e.g. 'person thinking',
  'woman phone', 'man stressed').
- Write the narration so it NAMES the visible situation (phone, mirror, friends, sleep, shopping...), so voice and
  picture agree word for word. If a sentence has no filmable situation, rewrite the sentence.
- Scene 1 keyword must be the most dramatic, eye-catching footage: BRIGHT, high-contrast, one clear
  person in closeup (face, hands, phone). Avoid dark, murky, empty, abstract or generic shots.
- Never use brain animations, neurons, galaxies, glowing abstract backgrounds, nature, animals or space.
- Every scene must have a DIFFERENT keyword (the last scene's footage is replaced by scene 1's footage automatically for the loop).

Return ONLY valid JSON, exactly this shape:
{_SCHEMA}
"""


def _editor_prompt(draft_json):
    return f"""
You are a strict fact-checker and retention editor for an English psychology-facts Shorts channel.
Below is a draft script (JSON). Improve it and return the FINAL JSON in the exact same schema.

CHECKLIST
1. Fact-check every claim. If any claim is wrong, exaggerated, failed replication, or unverifiable, replace it
   with a verified detail (or rewrite the scene) so the whole video is true. Remove invented numbers and
   'scientists found' claims you cannot support. Keep only facts you would bet money on.
2. Hook: max 8 words. First 3 words create shock, a warning or a burning question with a concrete detail.
   Rewrite if it sounds like an intro, greeting or textbook line. It must be truthfully paid off. Scene 2 must TEASE
   the payoff so the viewer knows within 3 seconds why to stay. Fill hook_text (3-6 words, honest, works muted).
3. Every scene: natural spoken American English for the EAR, simple words, contractions, max {MAX_SCENE_WORDS} words,
   no digits for small numbers, no emojis or symbols, and NO Hindi/Urdu. Vary sentence length so the voice
   is never flat; use a comma or '...' before the big reveal.
4. Cut filler and long setup. Each scene adds NEW info. Keep 7-10 scenes, {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS} words total
   (20-26 seconds spoken). Scene 5 must work as a re-hook (a flip or escalation).
5. LOOP: the last scene's final words must flow grammatically into scene 1's first words (seamless replay,
   REQUIRED - rewrite scene 1 and the last scene together if needed), and the last scene should make the hook mean
   something new on a second watch. No begging for likes/follows.
6. VISUAL MATCH: for every scene, search_keyword must be a concrete filmable situation of ordinary people
   (person + action + place, 2-4 English words) that literally shows what the spoken line says; no abstract
   concepts, no brain animations, no nature/space/animals. Add search_keyword_alt (2-3 simpler words) as backup.
   Different keyword in every scene. Scene 1 keyword = bright, high-contrast, closeup of a person, never dark/generic.
   If a line cannot be filmed, rewrite the line so it names something visible.
7. Title: English, max 58 chars, one emoji, honest (no false promise). Description and tags in English.
8. comment_cta: keep or write ONE on-screen question (4-9 words) tied to this topic that is easy to answer in the
   comments ('Have you noticed this too?'). Not spoken, so it must not change the loop. No like/subscribe begging.
9. hook_text: 3-6 words with one power word, a visual shock that works muted.

Return ONLY the corrected JSON.

DRAFT:
{draft_json}
"""


# ---------------------------------------------------------- public API ----
def generate_script(client, history_path, attempts=3):
    plan = pick_plan(history_path)
    print(f"Category: {plan['category']}")
    print(f"Format:   {plan['format']}")

    best = None
    for attempt in range(1, attempts + 1):
        try:
            draft = _ask(client, _writer_prompt(plan), temperature=1.0)
        except Exception as e:
            print(f"writer failed: {e}")
            continue

        draft_script, draft_problems = normalize_and_validate(draft)
        final_script, final_problems = None, ["editor not run"]

        try:
            edited = _ask(client, _editor_prompt(json.dumps(draft, ensure_ascii=False)), temperature=0.4, max_rounds=2)
            final_script, final_problems = normalize_and_validate(edited)
        except Exception as e:
            print(f"editor failed (using draft if valid): {e}")

        if final_script and not final_problems:
            chosen = final_script
        elif draft_script and not draft_problems:
            print(f"editor output rejected: {final_problems}")
            chosen = draft_script
        else:
            print(f"attempt {attempt} problems: draft={draft_problems} final={final_problems}")
            best = best or final_script or draft_script
            continue

        chosen["category"] = plan["category"]
        chosen["format"] = plan["format"]
        print(f"Script OK | {chosen['title']} | scenes={len(chosen['scenes'])}")
        return chosen

    if best and len(best.get("scenes", [])) >= MIN_SCENES:
        print("Using best-effort script despite validation warnings")
        best["category"] = plan["category"]
        best["format"] = plan["format"]
        return best
    return None
