"""
Script engine (Gemini).

Why this replaces the old prompt:
- Narration is written in DEVANAGARI. The TTS voice is hi-IN, and Roman text is
  pronounced badly by a Hindi neural voice. Captions/title stay Roman (Hinglish)
  so Pakistani + Indian viewers can read and search them.
- Two passes: writer -> strict fact-check/editor. No more "10 chips can kill you".
- Topic history stores the last facts and titles; Gemini is told to avoid them
  (the old fixed list of 38 topics repeated within two weeks at 3 videos/day).
- Story structure with a real hook, a payoff, and a loop-back ending.
- Digits / % / stray Latin letters are cleaned so TTS never stumbles.
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
MAX_SCENES = 11
HISTORY_KEY = "_recent"
HISTORY_LIMIT = 90

# Every category maps to footage that really exists on Pexels/Pixabay.
CATEGORIES = [
    "human body and brain (eyes, heart, hands, sleep)",
    "space, planets, stars and astronauts",
    "deep ocean and sea animals",
    "wild animals and their survival tricks",
    "extreme weather (lightning, storms, ice, rain)",
    "volcanoes, earthquakes and how the Earth works",
    "ancient civilizations, temples and ruins",
    "money, gold and strange facts about wealth",
    "food and cooking science",
    "technology, robots, computers and smartphones",
    "psychology and everyday human habits",
    "time, clocks and calendars",
    "deserts, mountains and extreme places on Earth",
    "insects and tiny creatures",
    "dreams and sleep",
    "science labs, experiments and discoveries",
    "trees, plants and forests",
    "fire, ice and extreme temperatures",
    "sports and the limits of the human body",
    "history's strangest records and museum objects",
]

FORMATS = {
    "shocking_fact": (
        "ONE surprising, verifiable fact, explained step by step. "
        "Hook = the surprising result, explanation comes after."
    ),
    "personal_what_if": (
        "A 'what if this happened to YOU' scenario grounded in real science. "
        "Speak directly to the viewer (aap). Only real, well-established consequences - "
        "no invented numbers, no fake 'X will kill you' claims."
    ),
    "myth_vs_truth": (
        "Start with a very common belief, then reveal the truth with a real reason. "
        "Hook = the belief stated as if it were true, then flip it."
    ),
    "mystery_explained": (
        "Open with a strange real phenomenon that seems impossible, "
        "then explain how it actually works."
    ),
    "top3": (
        "Three real, related facts, each in 2-3 short scenes, escalating so that the "
        "third is the most shocking. Hook promises 'teen' (three) things."
    ),
}

HOOK_PATTERNS = {
    "bold_claim": (
        "Bold TRUE claim that sounds wrong, stated as a fact. "
        "Style example: 'आपका दिमाग़ आपसे रोज़ झूठ बोलता है।'"
    ),
    "warning": (
        "A warning to the viewer about something they do or will meet. "
        "Style example: 'रात को ये गलती कभी मत करना।'"
    ),
    "impossible": (
        "Something that sounds impossible but is real. "
        "Style example: 'ये जानवर मरकर भी ज़िंदा रहता है।'"
    ),
    "aap_question": (
        "A direct 'aap' question the viewer cannot answer. "
        "Style example: 'आप रोज़ ये करते हैं, पर क्यों?'"
    ),
    "stakes": (
        "A countdown or stakes line where something changes fast. "
        "Style example: 'सिर्फ़ तीन सेकंड, और सब बदल जाता है।'"
    ),
    "result_first": (
        "Start with the SHOCKING RESULT/ending of the story (what was found, what happened), "
        "then the video explains how. Style example: 'उन्हें गुफा में जो मिला, कोई यक़ीन नहीं करता।' "
        "Only if the result is real and is truly delivered later."
    ),
}

# Ending styles. Only some ask for engagement - a forced CTA on every video hurts.
CTA_STYLES = [
    "a loop line whose last words complete the hook sentence, so the video replays naturally",
    "a loop line whose last words complete the hook sentence, so the video replays naturally",
    "a loop line whose last words complete the hook sentence, so the video replays naturally",
    "NO call to action at all: end on the final punchline of the story in one short, strong line",
    "NO call to action at all: end on the final punchline of the story in one short, strong line",
    "ONE question tied to THIS exact story that makes the viewer picture themselves in it "
    "(like 'आप होते तो क्या करते?' or 'आपको क्या लगता है, असल में हुआ क्या?'), not a generic 'comment karo'",
    "a curiosity teaser for the next video, no begging for likes",
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
        "hook_pattern": script.get("hook_pattern", ""),
        "hook": (script.get("scenes") or [{}])[0].get("roman", ""),
        "title": script.get("title", ""),
        "fact": script.get("core_fact", ""),
    })
    data[HISTORY_KEY] = recent[-HISTORY_LIMIT:]
    save_history(path, data)


def attach_video_id(path, video_id, channel=""):
    """Store the YouTube video id on the newest history row so analytics
    (swipe-away %, avg % viewed) can later be compared per hook pattern / category / format."""
    data = load_history(path)
    recent = data.get(HISTORY_KEY, [])
    if not recent:
        return
    recent[-1]["video_id"] = video_id
    if channel:
        recent[-1]["channel"] = channel
    data[HISTORY_KEY] = recent
    save_history(path, data)


def pick_plan(history_path):
    recent = load_history(history_path).get(HISTORY_KEY, [])
    recent_cats = [r.get("category") for r in recent[-8:]]
    recent_fmts = [r.get("format") for r in recent[-2:]]
    recent_hooks = [r.get("hook_pattern") for r in recent[-2:]]

    cats = [c for c in CATEGORIES if c not in recent_cats] or CATEGORIES
    fmts = [f for f in FORMATS if f not in recent_fmts] or list(FORMATS)
    hooks = [h for h in HOOK_PATTERNS if h not in recent_hooks] or list(HOOK_PATTERNS)

    return {
        "category": random.choice(cats),
        "format": random.choice(fmts),
        "hook_pattern": random.choice(hooks),
        "cta": random.choice(CTA_STYLES),
        "avoid": [r.get("fact") or r.get("title") for r in recent[-40:] if (r.get("fact") or r.get("title"))],
    }


# ---------------------------------------------- Hindi text sanitising ----
_ONES = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
    "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस "
    "छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चौवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
    "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ "
    "सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी "
    "इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे "
    "पंचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()


def num_to_hindi(n):
    if n < 100:
        return _ONES[n]
    parts = []
    for unit, word in ((10_000_000, "करोड़"), (100_000, "लाख"), (1000, "हज़ार"), (100, "सौ")):
        if n >= unit:
            q, n = divmod(n, unit)
            parts.append(f"{num_to_hindi(q)} {word}")
    if n:
        parts.append(_ONES[n])
    return " ".join(parts)


def sanitize_narration(text):
    text = str(text)
    text = text.replace("%", " प्रतिशत ")
    text = re.sub(r"(\d+)\.(\d+)", lambda m: f"{num_to_hindi(int(m.group(1)))} दशमलव {num_to_hindi(int(m.group(2)))}", text)
    text = re.sub(r"\d[\d,]*", lambda m: num_to_hindi(int(m.group(0).replace(",", ""))) if len(m.group(0).replace(",", "")) < 10 else m.group(0), text)
    text = re.sub(r"[\"“”'`*_#\[\]{}()<>/\\|~^]", " ", text)
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


_LATIN = re.compile(r"[A-Za-z]")
_DEVA = re.compile(r"[\u0900-\u097F]")


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
        caption = str(sc.get("caption") or "").strip()
        roman = " ".join(str(sc.get("narration_roman") or "").split())
        scenes.append({"narration": narration, "caption": caption,
                       "search_keyword": keyword, "roman": roman})

    if not (MIN_SCENES <= len(scenes) <= MAX_SCENES):
        problems.append(f"scene count {len(scenes)} not in {MIN_SCENES}-{MAX_SCENES}")

    for i, sc in enumerate(scenes, 1):
        words = sc["narration"].split()
        if _LATIN.search(sc["narration"]):
            problems.append(f"scene {i} has Latin letters")
        if not _DEVA.search(sc["narration"]):
            problems.append(f"scene {i} not Devanagari")
        if len(words) > 15:
            problems.append(f"scene {i} too long ({len(words)} words)")
        if not sc["search_keyword"]:
            problems.append(f"scene {i} missing search_keyword")

    if scenes and len(scenes[0]["narration"].split()) > 8:
        problems.append("hook longer than 8 words")
    if scenes and re.match(r"\s*(क्या\s+आप\s+जानते|दोस्तों|नमस्ते|आज\s+हम)", scenes[0]["narration"]):
        problems.append("hook starts with a greeting/intro")
    if scenes and not scenes[0]["caption"]:
        problems.append("scene 1 caption (on-screen opening text) missing")
    kws = [s_["search_keyword"].lower() for s_ in scenes if s_["search_keyword"]]
    if len(set(kws)) < len(kws):
        problems.append("duplicate search_keyword")

    total_words = sum(len(s["narration"].split()) for s in scenes)
    if scenes and not (58 <= total_words <= 95):
        problems.append(f"total words {total_words} outside 58-95")

    tags = data.get("tags") or []
    script = {
        "title": str(data.get("title", "")).strip(),
        "description": str(data.get("description", "")).strip(),
        "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
        "core_fact": str(data.get("core_fact", "")).strip(),
        "scenes": scenes,
    }
    if not script["title"]:
        problems.append("missing title")
    return script, problems


# ------------------------------------------------------------- prompts ----
_SCHEMA = """{
  "core_fact": "one English sentence stating the main fact (used to avoid repeats)",
  "title": "Roman Hinglish title, max 58 chars, one emoji, no hashtags",
  "description": "2-3 short Roman Hinglish lines + one line of English search keywords. No hashtags.",
  "tags": ["15-20 lowercase tags mixing english + roman hindi/urdu"],
  "scenes": [
    {
      "narration": "ONE spoken sentence in Devanagari",
      "narration_roman": "the SAME sentence typed in Roman Hinglish (as people type Hindi on WhatsApp), EXACTLY the same number of words in the same order, keep the commas/?/. at the same places, no digits",
      "caption": "2-5 word on-screen text in Roman Hinglish/English, UPPERCASE-friendly. Scene 1 = curiosity-gap opening text (see rules)",
      "search_keyword": "2-4 english words for stock footage"
    }
  ]
}"""


def _writer_prompt(plan):
    avoid = "\n".join(f"- {a}" for a in plan["avoid"]) or "- (nothing yet)"
    return f"""
You are the head writer of a top Hindi/Urdu-audience YouTube Shorts facts channel.
Write ONE fresh 22-28 second Short. Retention is everything: about 7 out of 10 viewers
swipe away in the first 2 seconds, so the first scene decides the whole video. Short and
tight beats long: every extra second of setup loses viewers.

CATEGORY: {plan['category']}
FORMAT: {plan['format']} -> {FORMATS[plan['format']]}
HOOK PATTERN: {plan['hook_pattern']} -> {HOOK_PATTERNS[plan['hook_pattern']]}
ENDING STYLE: {plan['cta']}

DO NOT repeat or paraphrase any of these earlier videos:
{avoid}
Also avoid the internet's most overused facts (honey never spoils, octopus has three hearts,
we use only 10% of the brain, banana radiation, Great Wall visible from space, etc).
Pick something a curious person would say "sach mein?" to.

HOOK (scene 1) - THE MOST IMPORTANT LINE
- Max 7 words. The first 3 words must already create shock, danger, or an open question.
- No greeting, no intro, never start with 'क्या आप जानते हैं'. Start mid-action, like the
  story is already happening. The viewer must understand in one second WHAT this video is about.
- Use the HOOK PATTERN given above (style only, do NOT copy its example).
- The hook must promise something the LAST scenes actually deliver. No clickbait lies.
- Scene 1 caption = on-screen OPENING TEXT, 3-5 words, Roman, UPPERCASE-friendly. It must open a
  curiosity gap (a half-finished sentence about the real topic, e.g. 'IS JAGAH PAR JO MILA...',
  'CAMERA NE JO RECORD KIYA...'), NOT a generic label like 'EK AJEEB WAQIA'.
  It must only hint at things the video really delivers - no invented claims, no fake police/
  government/news framing.

PAYOFF EARLY (do not hide everything until the end)
- Scene 1 or 2 must tease the payoff: show the stake or the shocking RESULT (not the explanation).
- Scene 2 = one short line of context, then go straight into the story. No long setup.
- The real answer/reveal comes in the second-last scene, and every scene before it must add a
  new detail that makes the viewer wait for it.

ACCURACY (non-negotiable)
- Only real, well-established facts. If you are not sure, choose a different fact.
- No invented statistics. No "X will kill you" style medical fear-mongering.
- Use round, defensible numbers ("lagbhag", "takreeban" style words are fine).

LANGUAGE
- Narration: natural spoken Hindustani in DEVANAGARI only, understood by both Indian and
  Pakistani viewers. Everyday words, not Sanskritised (say 'दिमाग' not 'मस्तिष्क',
  'ज़िंदगी' not 'जीवन', 'वजह' not 'कारण' when natural).
- SUPER EASY WORDS: write so that a 10-year-old child understands every single word on first
  hearing. NO difficult, bookish, shuddh-Hindi or heavy Urdu/Persian words (avoid words like
  'अत्यंत', 'संभवतः', 'प्रक्रिया', 'अस्तित्व', 'विशाल', 'उत्पन्न', 'मुमकिन', 'निहायत'). Use the
  simple word people use in daily chat: 'बहुत' (not 'अत्यंत'), 'शायद' (not 'संभवतः'),
  'बड़ा' (not 'विशाल'), 'बनाना' (not 'उत्पन्न करना'), 'तरीका' (not 'प्रक्रिया').
- LIGHT ENGLISH MIX: sprinkle easy, everyday English words the way young people really talk
  (जैसे 'स्पीड', 'पावर', 'टाइम', 'फ़ास्ट', 'सीक्रेट', 'ट्राई', 'सुपर', 'प्लीज़', 'एक्चुअली').
  About 1 easy English word in every 1-2 scenes is enough - it should feel natural, not forced,
  and the sentence must stay simple. Still write them in Devanagari only (no Latin letters).
- ZERO Latin letters and ZERO digits inside narration. Write numbers in Hindi words
  (सौ, हज़ार, पचास प्रतिशत). Any English term must be written in Devanagari the way people say it
  (जैसे 'लेज़र', 'सैटेलाइट').
- narration_roman: word-for-word Roman Hinglish of the narration (e.g. 'दिमाग़ रोज़ झूठ बोलता है।' ->
  'dimaag roz jhoot bolta hai.'). SAME word count and order as narration, so captions stay in sync.
  Simple, common spellings (hai, nahi, kya, bahut, aap, kyun). Never translate to English - transliterate.
- Use commas and the danda (।) or ? naturally so the voice gets rhythm and breath.
- Each scene = EXACTLY ONE short sentence, 6-10 words. It must sound like a friend telling a
  story, not a textbook.

STRUCTURE (7 to 10 scenes, 65-85 words total)
1. HOOK (see above).
2. Payoff tease + one line of context - why should I care? Zero filler.
3-4. Concrete detail, a real number, then the WHY in simple words.
Middle (scene 4 or 5): RE-HOOK: a line that flips or escalates ('पर असली बात अभी बाकी है' style, in
   your own words) and still adds NEW information. This stops the mid-video drop-off.
Then: the story continues. Every scene adds new info and ends on a small open loop.
Second-last: the twist / most surprising part (the payoff you teased).
Last scene: {plan['cta']}. Max 9 words. No 'like/subscribe' begging.
If the ending style is a loop line, its last words must grammatically complete the hook sentence.

DELIVERY (the voice is an AI, so write for the ear)
- Short sentences, active verbs, a different rhythm every scene: mix a 5-word punch with a 9-word line.
- Use '?' and '!' where a real person would, and a comma where a real person would breathe.
- Say 'आप' to the viewer often. Never sound like you are reading a textbook.

VISUALS
- search_keyword: English, 2-4 words, BROAD footage that certainly exists on free stock sites
  (space galaxy, ocean waves, lion running, human eye closeup, city traffic night, gold coins,
  ancient temple ruins, scientist microscope, lightning storm, sleeping person, clock ticking...).
- Scene 1 keyword must be the most eye-catching footage (fast motion, extreme closeup, vivid colour,
  bright and high-contrast - NOT dark, foggy or blurry), and it must show the exact subject of the
  hook, because it is the first frame the viewer sees.
- Never a brand, a person's name, or a rare species. Every scene must have a DIFFERENT keyword.
- Make the keyword show the exact thing/noun SAID in that scene, so picture and voice agree.
  If no stock clip could show it literally, pick the closest concrete visual of that same subject,
  never an unrelated pretty background.

Return ONLY valid JSON, exactly this shape:
{_SCHEMA}
"""


def _editor_prompt(draft_json):
    return f"""
You are a strict fact-checker and retention editor for a Hindi Shorts channel.
Below is a draft script (JSON). Improve it and return the FINAL JSON in the exact same schema.

CHECKLIST
1. Fact-check every claim. If any claim is wrong, exaggerated or unverifiable, replace it with a
   verified detail (or rewrite the scene) so the whole video is true. Remove invented numbers.
2. Hook: max 7 words. First 3 words must create shock, danger or a burning question. Rewrite it
   if it sounds like an intro, a greeting or a textbook line. The hook must be truthfully paid off.
   Scene 1 caption must be a 3-5 word curiosity-gap opening text about the real topic (not a generic
   label), with no invented claims. Scene 1 or 2 must tease the payoff; the reveal is scene N-1.
3. Every scene: one sentence, 6-10 words, Devanagari only, no digits, no Latin letters, natural
   spoken Hindustani that an Indian and a Pakistani viewer both understand.
   Replace every difficult / bookish / heavy Hindi or Urdu word with the simple everyday word a
   10-year-old child knows. Keep a light sprinkle of easy English words written in Devanagari
   (जैसे 'स्पीड', 'टाइम', 'पावर', 'सीक्रेट') - about one every 1-2 scenes, natural, never forced.
4. Cut filler. Each scene must add new info. Keep 7-10 scenes, 65-85 words total (22-28 seconds).
   Scene 4 or 5 must work as a re-hook (a flip or escalation). Delete any setup that delays the payoff.
5. Last scene: short, natural, no begging for likes. It should make the viewer want to replay (a loop
   that completes the hook sentence) or think. Do not add a generic 'comment karo' line.
6. Keep search_keyword broad, English, 2-4 words, different for every scene, showing the exact thing
   said in that line. Scene 1 keyword: bright, high-contrast, eye-catching subject of the hook.
7. Title: Roman Hinglish, max 58 chars, one emoji, honest (no false promise).
8. For EVERY scene, narration_roman must be the exact word-for-word Roman Hinglish of the (final, edited)
   narration: same number of words, same order, same punctuation. Re-write it whenever you change the narration.

Return ONLY the corrected JSON.

DRAFT:
{draft_json}
"""


# ---------------------------------------------------------- public API ----
def generate_script(client, history_path, attempts=3):
    plan = pick_plan(history_path)
    print(f"Category: {plan['category']}")
    print(f"Format:   {plan['format']}")
    print(f"Hook:     {plan['hook_pattern']}")

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
        chosen["hook_pattern"] = plan["hook_pattern"]
        print(f"Script OK | {chosen['title']} | scenes={len(chosen['scenes'])}")
        return chosen

    if best and len(best.get("scenes", [])) >= MIN_SCENES:
        print("Using best-effort script despite validation warnings")
        best["category"] = plan["category"]
        best["format"] = plan["format"]
        best["hook_pattern"] = plan["hook_pattern"]
        return best
    return None
