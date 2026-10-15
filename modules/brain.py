"""
Script engine (Gemini) - ENGLISH psychology-facts Shorts, PER-CHANNEL.

Changes in this version
-----------------------
- Uses modules.content_plan.build_plan() so each channel gets its OWN
  sub-niche and its OWN format rotation. Channel 1 never writes a money
  video; channel 2 never writes a relationship video.
- Each format has its OWN beat order (hook -> name -> example -> mechanism
  -> re-hook -> payoff -> loop is only ONE of seven structures).
- Title ending style, description opener and description CTA come from the
  plan, so titles do not all end '... #Shorts' and descriptions do not all
  end with the same sentence.
- Tags come from the model per video (no more 15 hardcoded BASE_TAGS).
- Topic history stores the last facts and titles; Gemini is told to avoid them.
"""

import json
import os
import random
import re
import time
from datetime import datetime

from google.genai import types

from modules import content_plan
from modules import textprep


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

# Re-exported so older imports (from modules.brain import CATEGORIES / FORMATS)
# do not break. New code should read the pools via content_plan.
CATEGORIES = [c for pool in content_plan.CHANNEL_NICHES.values()
              for c in pool["categories"]]

FORMATS = {
    "psychology_effect": (
        "ONE well-established psychology effect shown through a daily-life situation the viewer "
        "recognises instantly. BEAT ORDER: hook -> name the effect in plain words -> one example the "
        "viewer has lived through -> the real mechanism -> re-hook with a second example -> payoff -> loop."
    ),
    "myth_vs_truth": (
        "Start with a common belief, then reveal the truth. BEAT ORDER: hook = the belief stated as "
        "fact -> 'but that is not what happens' -> what really happens and why -> the kind of research "
        "that showed it (name the field, not a fake number) -> re-hook -> loop."
    ),
    "psychology_fact": (
        "ONE surprising verifiable fact. BEAT ORDER: hook = the result only -> 'here is what is really "
        "happening' -> step-by-step why -> a concrete daily-life example -> re-hook with a twist -> "
        "payoff -> loop. NOT a list format."
    ),
    "personal_what_if": (
        "A 'when YOU do X, here is what happens inside your head' scenario, second person throughout. "
        "BEAT ORDER: hook = the scenario in one line -> the first thing you notice -> what your brain "
        "does underneath -> why it does that -> re-hook -> the surprising consequence -> loop. "
        "Only real, well-established findings."
    ),
    "top3": (
        "Three real, related facts, each in 2-3 short scenes. BEAT ORDER: hook promises 'three' -> "
        "fact 1 -> fact 2 -> re-hook that escalates to fact 3 -> fact 3 (the strongest) -> loop. "
        "The three must build on each other, not be three unrelated trivia points."
    ),
    "story": (
        "Open on one specific person doing one specific thing in the first 2 seconds - a moment, not "
        "a claim. BEAT ORDER: hook = the moment, present tense -> the question that moment raises -> "
        "what the research shows -> why it happens to almost everyone -> re-hook with a second moment "
        "-> payoff -> loop. Every scene must name something visible."
    ),
    "question_first": (
        "Open with a question the viewer has asked themselves in the last week. BEAT ORDER: hook = "
        "the question, phrased the way a real person would ask it -> 'most people guess wrong' -> the "
        "real answer -> why our brains do it -> re-hook with the flip side -> payoff -> loop. No "
        "rhetorical questions later on; only the opening one."
    ),
}

CTA_STYLES = [
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the "
    "reason', '...and it all comes down to this:') whose words continue grammatically into the first "
    "words of scene 1, so the video replays seamlessly",
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the "
    "reason', '...and it all comes down to this:') whose words continue grammatically into the first "
    "words of scene 1, so the video replays seamlessly",
    "a LOOP line: it ends on an unfinished lead-in ('...and that is exactly why', '...which is the "
    "reason', '...and it all comes down to this:') whose words continue grammatically into the first "
    "words of scene 1, so the video replays seamlessly",
    "a REWATCH line: a final twist that makes the hook mean something new ('Now watch the first line "
    "again - it hits different.' style, in your own words) and flows into scene 1",
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


# ------------------------------------------------ narration sanitising ----
def sanitize_narration(text):
    """Clean text for the English TTS voice."""
    text = str(text)
    text = textprep.numbers_to_words(text.replace("%", " percent"))
    text = (text.replace("%", " percent").replace("&", " and ")
                .replace("\u2019", "'").replace("\u2018", "'"))
    text = (text.replace("\u2014", " - ").replace("\u2013", " - ")
                .replace("\u2026", "..."))
    text = re.sub(r"[\"\u201c\u201d`*_#\[\]{}()<>/\\|~^@]", " ", text)
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
                    _DEAD_MODELS.add(model)
        if rnd < max_rounds:
            time.sleep(base_wait * rnd)
    raise RuntimeError(f"Gemini failed after all retries: {last}")


_DEVA = re.compile(r"[\u0900-\u097F]")

# Openers that make people swipe. A hook starting like this is rejected and rewritten.
HOOK_BANNED_STARTS = (
    "did you know", "have you ever", "today", "in this video", "let's", "let us",
    "welcome", "hey ", "hi ", "hello", "so ", "okay", "ok ", "guys", "what if i told you",
    "here's", "here is", "ever wonder", "ever wondered", "have you noticed",
)

MAX_SCENE_WORDS = 13
MIN_TOTAL_WORDS = 52
MAX_TOTAL_WORDS = 80


def normalize_and_validate(data):
    """Return (script, problems)."""
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
        scenes.append({"narration": narration, "caption": caption,
                       "search_keyword": keyword, "search_alt": keyword_alt,
                       "roman": narration})

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
    if scenes and len(scenes[0]["narration"].split()) < 3:
        problems.append("hook too short")
    if scenes and scenes[0]["narration"].lower().lstrip().startswith(HOOK_BANNED_STARTS):
        problems.append("weak/banned hook opener")

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
  "title": "English title, max 58 chars, honest + curiosity-driven, follows the TITLE ENDING RULE given below. No generic 'You Won't Believe This'.",
  "hook_text": "3-6 word on-screen text shown in the FIRST 2 seconds, English, works with sound off, opens a curiosity gap the video really pays off",
  "comment_cta": "ONE short on-screen question for the last 2-3 seconds, 4-9 words, easy to answer in one line. Must relate to THIS video's topic, no 'like/subscribe'",
  "description": "2-3 short engaging English lines following the DESCRIPTION OPENER RULE below + one line of relevant search keywords. No hashtags.",
  "tags": ["12-20 lowercase english tags that a viewer of THIS specific video would search for. Include the exact topic (e.g. 'why we overspend'), not just the channel topic."],
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
    title_tag, title_rule = plan["title_ending"]
    fmt_instructions = FORMATS.get(plan["format"], FORMATS["psychology_effect"])

    return f"""
You are the head writer of a top English-language YouTube Shorts channel. This channel's
niche is: {plan['niche_name']}.
Write ONE fresh 20-26 second Short. Goal: MAXIMUM "stayed to watch" + REPLAYS. Most viewers
should watch to the end and then watch again because the ending flows into the start.

CATEGORY (this is the ONLY category you may write about today):
{plan['category']}

FORMAT: {plan['format']}
STRUCTURE YOU MUST FOLLOW:
{fmt_instructions}

ENDING STYLE: {random.choice(CTA_STYLES)}

TITLE ENDING RULE (mandatory, this is what stops every video from ending '... #Shorts'):
{title_rule}

DESCRIPTION OPENER RULE (mandatory):
{plan['description_opener']}
The description MUST end with this exact style of CTA, in your own words: "{plan['description_cta']}"

DO NOT repeat or paraphrase any of these earlier videos on this channel:
{avoid}
Also avoid the internet's most overused facts and anything that failed replication or is
pop-psychology myth: 10% of the brain, left/right brain, learning styles, power posing,
ego depletion, the Mozart effect, 10,000 hours, the 7-38-55 rule, goldfish attention span,
Stanford prison experiment as proof, marshmallow test as destiny, 21 days to form a habit,
MBTI claims, 'people can read minds from microexpressions'. Pick a well-supported finding a
curious person would say "wait, really?" to - and that you would bet money on.

HOOK (scene 1) - THE MOST IMPORTANT LINE. If the first 2 seconds fail, nothing else matters.
- Max 8 words. The FIRST 3 WORDS must already hit: a shock, a warning, a call-out of something
  the viewer did or felt today, or a question they secretly asked themselves. Start mid-thought.
- Pick ONE technique and commit to it:
  1. CALL-OUT - name something the viewer just did or felt ("You reread that text three times,
     didn't you?").
  2. CONTRADICTION - flip a belief they trust ("Being busy is not what makes you tired.").
  3. HIDDEN COST - something normal that quietly works against them ("This tiny habit is
     rewiring how you think.").
  4. SECRET-FEELING - "nobody warns you about this" energy, ONLY if the video truly pays it off.
  5. STRANGE DETAIL - one specific, odd detail they need explained.
  (The examples show the TECHNIQUE only. Never copy them or reuse their topics.)
- Never open with: a greeting, 'Did you know', 'Have you ever', 'Today', 'In this video',
  'Let's', "Here's", 'Ever wondered'.
- Scene 2 is the TEASER: it widens the gap and gives a reason to stay ("And the reason is not
  what you think.", "Your brain does it on purpose."). It never answers the hook.
- Exactly ONE curiosity gap, with a concrete detail, paid off truthfully by the second-last
  scene. No clickbait lies.
- hook_text (separate field): 3-6 words, one power word (STOP, NEVER, WARNING, LIES, SECRET,
  WHY...), works muted. No full stop.
- VOICE DIRECTION: the narrator is a smart friend leaning in to tell you something they just
  found out and can't keep to themselves. Write the hook so it SOUNDS spoken: short, weighty
  words, one idea. Put '...' right before the key word in the hook or teaser when it makes the
  reveal land (max twice in the whole script).
- PACING: no slow setup. Every sentence adds a new fact or raises the stakes.

ACCURACY (non-negotiable)
- Only real, well-established findings. If unsure, choose a different fact.
- No invented statistics, no fake 'scientists found'. Round defensible wording.
- No medical fear-mongering, no diagnosing the viewer.

LANGUAGE AND VOICE (read by a US English neural voice, write for the EAR)
- Natural spoken American English. Simple everyday words a 12-year-old understands.
- Contractions always. Short punchy sentences. Mix 4-word and 11-word lines.
  Max {MAX_SCENE_WORDS} words per scene.
- Use commas, '...' and '?' on purpose. Comma or '...' right before the big reveal.
- Spell numbers as words for small numbers. No symbols, no emojis, no hashtags.
- Speak to the viewer: 'you', 'your'.
- No filler, no 'in this video', no 'let's dive in', no begging.
- WRITE FOR THE VOICE: no rare jargon or long Latin/Greek terms (say 'stress hormone', not
  'cortisol'; 'fear center', not 'amygdala'). No researcher names, no foreign words, no
  acronyms except FBI, DNA, TV, IQ. No digits, no symbols, no ALL-CAPS words. If a word is
  hard to say out loud, use a simpler one.

STRUCTURE (7 to 10 scenes, {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS} words total, 20-26 seconds)
Follow the STRUCTURE YOU MUST FOLLOW above for the beat ORDER. In general:
1. HOOK.
2. Add a teaser line so the viewer knows within 3 seconds WHY to stay.
3-4. A concrete detail, a real example, then the WHY in plain words.
5. RE-HOOK that adds NEW information. Every scene after this ends on a small open loop.
Second-last: the payoff promised by the hook.
Last scene: the ENDING STYLE above, max 10 words.

LOOP RULE (mandatory): the video auto-replays. The LAST words of the last scene must
continue grammatically into the FIRST words of scene 1. Scene 1 must still work as a
stand-alone hook. The last scene should also make the hook mean something NEW on a second
watch so people want to watch again.

VISUALS (picture must MATCH the sentence - #1 quality problem, be strict)
- Psychology is abstract, stock footage is not. For EVERY scene imagine ONE real, filmable
  scene of ordinary people that illustrates that exact sentence, then describe it.
  Show the SITUATION, never the concept: 'man arguing with friend', 'woman scrolling phone
  in bed', 'student studying late night', 'person looking at mirror'.
- search_keyword: 2-4 English words = a person + an action (+ place). Concrete nouns only,
  no abstract words, no brand or person names.
- search_keyword_alt: a DIFFERENT simpler backup for the same sentence (2-3 words).
- Scene 1 keyword must be the most eye-catching: bright, high-contrast, one clear person.
- Never use brain animations, neurons, galaxies, glowing abstract backgrounds, nature,
  animals, science experiments or space.
- Every scene must have a DIFFERENT keyword (the last scene reuses scene 1's footage
  automatically for the loop).

Return ONLY valid JSON, exactly this shape:
{_SCHEMA}
"""


def _editor_prompt(draft_json, plan):
    title_tag, title_rule = plan["title_ending"]
    fmt_instructions = FORMATS.get(plan["format"], FORMATS["psychology_effect"])
    return f"""
You are a strict fact-checker and retention editor for an English psychology Shorts channel.
The channel's niche is: {plan['niche_name']}.
Below is a draft script (JSON). Improve it and return the FINAL JSON in the same schema.

STRUCTURE the final must follow:
{fmt_instructions}

TITLE ENDING RULE (mandatory): {title_rule}
DESCRIPTION OPENER RULE (mandatory): {plan['description_opener']}
DESCRIPTION CTA (mandatory, in your own words): "{plan['description_cta']}"

CHECKLIST
1. Fact-check every claim. Replace anything wrong, exaggerated, failed replication or
   unverifiable with a verified detail. Remove invented numbers. Keep only facts you would
   bet money on.
2. Hook: max 8 words, first 3 words must HIT (call-out, contradiction, hidden cost, strange
   detail). Never 'Did you know', 'Have you ever', 'Today', a greeting, or 'Here's'. It should
   sound like a friend leaning in to share something big. Scene 2 must TEASE the payoff and
   widen the gap, never answer the hook. Fill hook_text (3-6 words).
3. Every scene: natural spoken American English, simple words (replace hard jargon like
   'cortisol' with 'stress hormone'; no researcher names, no ALL-CAPS), contractions, max
   {MAX_SCENE_WORDS} words, no digits for small numbers, no emojis, no Hindi/Urdu. Vary
   sentence length. Comma or '...' before the big reveal.
4. Cut filler. Each scene adds NEW info. Keep 7-10 scenes, {MIN_TOTAL_WORDS}-{MAX_TOTAL_WORDS}
   words total. Make sure the BEAT ORDER matches the structure above, not the generic one.
5. LOOP: last words flow into scene 1's first words (REQUIRED - rewrite both if needed).
   Last scene makes the hook mean something new on a second watch.
6. VISUAL MATCH: every scene's search_keyword is a concrete filmable situation of ordinary
   people (person + action + place, 2-4 English words). No abstract concepts, no science
   experiments, no brain animations, no nature/space/animals. Add search_keyword_alt.
   Different keyword every scene. Scene 1 = bright, high-contrast, closeup of a person.
7. Title: follows the TITLE ENDING RULE. Max 58 chars. One emoji is fine.
   Description: follows the DESCRIPTION OPENER RULE and ends with the DESCRIPTION CTA.
   Tags: 12-20 tags SPECIFIC to this video's actual topic (not the channel topic).
8. comment_cta: 4-9 words, tied to this topic, easy to answer in one line.
9. hook_text: 3-6 words with one power word.

Return ONLY the corrected JSON.

DRAFT:
{draft_json}
"""




# ------------------------------------------------------------ hook doctor ----
def _hook_prompt(script):
    scenes = script["scenes"]
    first, second, last = scenes[0], scenes[1], scenes[-1]
    middle = "\n".join(f"{i}. {sc['narration']}" for i, sc in enumerate(scenes[2:-1], start=3))
    return f"""
You are a viral Shorts hook specialist. The video below is finished except its opening and
its last line. Rewrite ONLY: the hook (scene 1), the teaser (scene 2), hook_text, and the last
scene (so the loop still flows into the new hook).

First think of 5 different hooks using different techniques: call-out, contradiction, hidden
cost, secret-feeling, strange detail. Pick the ONE that a stranger scrolling at midnight
could not scroll past. Then write it.

RULES
- hook: 3-8 words, spoken English, starts mid-thought, first 3 words hit hard. It sounds like a
  friend leaning in to tell you something they just found out. Never starts with: did you know,
  have you ever, today, hey, here's, in this video, let's.
- It must be 100% truthfully paid off by the existing middle of the video. Do not promise
  anything the script does not deliver.
- teaser (scene 2): max 12 words. Widens the gap and gives a reason to stay. Does NOT reveal
  the answer.
- Use '...' (at most once across hook + teaser) right before the key word if it makes the
  reveal land.
- Simple everyday words, contractions, no digits, no jargon, no symbols.
- last_scene: max 10 words, must flow grammatically into the FIRST words of your NEW hook
  (the video loops) and keep the original ending idea.
- hook_text: 3-6 words shown on screen, one power word, works with sound off, no full stop.
- Scene 1 footage shows: "{first.get('search_keyword', '')}". The hook must still make sense
  over that picture.

TOPIC: {script.get('core_fact', '')}
CURRENT HOOK: {first['narration']}
CURRENT TEASER: {second['narration']}
MIDDLE OF THE VIDEO (the hook must be paid off by this):
{middle}
CURRENT LAST SCENE: {last['narration']}

Return ONLY JSON: {{"hook": "...", "teaser": "...", "hook_text": "...", "last_scene": "..."}}
"""


def boost_hook(client, script, attempts=2):
    """One extra pass that rewrites ONLY hook + teaser + hook_text + loop line. Never raises."""
    if os.getenv("HOOK_DOCTOR", "1") != "1":
        return script
    scenes = script.get("scenes") or []
    if len(scenes) < 5:
        return script
    for attempt in range(1, attempts + 1):
        try:
            data = _ask(client, _hook_prompt(script), temperature=0.9, max_rounds=2)
            hook = sanitize_narration(data.get("hook", ""))
            teaser = sanitize_narration(data.get("teaser", ""))
            last = sanitize_narration(data.get("last_scene", ""))
            hook_text = str(data.get("hook_text", "")).strip().rstrip(".")
        except Exception as e:
            print(f"hook doctor failed (keeping original hook): {e}")
            return script
        ok = (3 <= len(hook.split()) <= 8
              and not hook.lower().lstrip().startswith(HOOK_BANNED_STARTS)
              and 3 <= len(teaser.split()) <= MAX_SCENE_WORDS
              and 3 <= len(last.split()) <= 11)
        if not ok:
            print(f"hook doctor attempt {attempt}: output rejected, retrying")
            continue
        print(f"Hook boosted: '{scenes[0]['narration']}' -> '{hook}'")
        for idx, text in ((0, hook), (1, teaser), (len(scenes) - 1, last)):
            scenes[idx]["narration"] = text
            scenes[idx]["roman"] = text
        if 2 <= len(hook_text.split()) <= 7:
            script["hook_text"] = hook_text
        return script
    return script


# ---------------------------------------------------------- public API ----
def generate_script(client, history_path, channel_index=0, attempts=3):
    """Write a fresh script for the given channel (0-based index)."""
    history = load_history(history_path).get(HISTORY_KEY, [])
    plan = content_plan.build_plan(channel_index, recent_history=history)
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
            edited = _ask(
                client,
                _editor_prompt(json.dumps(draft, ensure_ascii=False), plan),
                temperature=0.4, max_rounds=2,
            )
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
        chosen["niche_name"] = plan["niche_name"]
        chosen["_plan"] = plan  # main.py reads title_ending + desc CTA from here
        chosen = boost_hook(client, chosen)
        print(f"Script OK | {chosen['title']} | scenes={len(chosen['scenes'])}")
        return chosen

    if best and len(best.get("scenes", [])) >= MIN_SCENES:
        print("Using best-effort script despite validation warnings")
        best["category"] = plan["category"]
        best["format"] = plan["format"]
        best["niche_name"] = plan["niche_name"]
        best["_plan"] = plan
        return boost_hook(client, best)
    return None
