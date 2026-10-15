"""
Text preparation for TTS (Kokoro / Edge).

Goal: the voice must NEVER get stuck on a word.
  1. numbers  -> words      ("2024" -> "twenty twenty-four", "3.5" -> "three point five")
  2. acronyms -> letters    ("FBI" -> "F B I"), other ALL-CAPS words -> lowercase
  3. hard words -> easy words, from YOUR list: pronunciations.json (repo root)
       {"cortisol": "stress hormone", "amygdala": "fear center"}
     You can add any word there any time, no code change needed.
     (Also works via env KOKORO_WORD_FIXES="word=replacement,word2=replacement2")
  4. SIMPLE_SYNONYMS = built-in jargon -> plain words, used when a word fails.
"""

import json
import os
import re

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]
_SCALES = [(10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]


def _below_1000(n):
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        t = _TENS[n // 10]
        parts.append(t + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n > 0 or not parts:
        parts.append(_ONES[n])
    return " ".join(parts)


def int_to_words(n):
    n = int(n)
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 1000:
        return _below_1000(n)
    out = []
    for size, name in _SCALES:
        if n >= size:
            out.append(_below_1000(n // size) + " " + name)
            n %= size
    if n:
        out.append(_below_1000(n))
    return " ".join(out)


def _year_to_words(n):
    if 2000 <= n <= 2009:
        return "two thousand" + (" " + _ONES[n % 10] if n % 10 else "")
    hi, lo = divmod(n, 100)
    if lo == 0:
        return int_to_words(hi) + " hundred"
    if lo < 10:
        return int_to_words(hi) + " oh " + _ONES[lo]
    return int_to_words(hi) + " " + int_to_words(lo)


def _ordinal(words):
    special = {"one": "first", "two": "second", "three": "third", "five": "fifth",
               "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}
    head, sep, last = words.rpartition(" ")
    if "-" in last:
        h2, _, l2 = last.rpartition("-")
        last = h2 + "-" + _ordinal(l2)
        return head + sep + last
    if last in special:
        last = special[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(st|nd|rd|th)?(?![\w])")


def numbers_to_words(text):
    text = re.sub(r"\$(\d[\d,]*)\.(\d{2})\b", r"\1 dollars and \2 cents", str(text))
    text = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)", r"\1 dollars", text)

    def repl(m):
        whole = m.group(1).replace(",", "")
        dec, ordn = m.group(2), m.group(3)
        try:
            n = int(whole)
        except ValueError:
            return m.group(0)
        if n >= 10 ** 12:
            return m.group(0)
        if dec:
            return int_to_words(n) + " point " + " ".join(_ONES[int(d)] for d in dec)
        if ordn:
            return _ordinal(int_to_words(n))
        if 1100 <= n <= 2099 and len(whole) == 4 and "," not in m.group(1):
            return _year_to_words(n)
        return int_to_words(n)

    return _NUM.sub(repl, text)


# ALL-CAPS words that should be spelled letter by letter
LETTER_ACRONYMS = {
    "FBI", "CIA", "USA", "UK", "TV", "DNA", "IQ", "ADHD", "OCD", "PTSD", "AI",
    "GPS", "BBC", "CEO", "NFL", "NBA", "UFO", "ER", "ICU", "MRI", "EEG", "PC",
    "US", "UN", "EU", "HR", "PR", "DIY", "FAQ", "ASMR",
}


def fix_acronyms(text):
    def repl(m):
        w = m.group(0)
        if w in LETTER_ACRONYMS:
            return " ".join(w)
        return w.lower()          # STOP -> stop (never shouted or spelled out)
    return re.sub(r"\b[A-Z]{2,6}\b", repl, text)


# --------------------------------------------------------- hard words ----
# Built-in jargon -> plain words. Used when a word is likely to trip the voice.
SIMPLE_SYNONYMS = {
    "neurotransmitter": "brain chemical", "neurotransmitters": "brain chemicals",
    "amygdala": "fear center", "hippocampus": "memory center",
    "prefrontal": "front", "cortex": "brain", "cortisol": "stress hormone",
    "oxytocin": "bonding hormone", "serotonin": "mood chemical",
    "endorphin": "feel good chemical", "endorphins": "feel good chemicals",
    "heuristic": "mental shortcut", "heuristics": "mental shortcuts",
    "cognitive": "mental", "dissonance": "inner conflict",
    "schadenfreude": "guilty pleasure", "phenomenon": "thing",
    "subconscious": "hidden mind", "unconscious": "hidden",
    "neuroscientists": "scientists", "neuroscientist": "scientist",
    "neuroscience": "science", "psychologists": "scientists",
    "psychologist": "scientist", "synapses": "connections", "synapse": "connection",
    "ephemeral": "short lived", "juxtaposition": "contrast", "paradoxical": "strange",
    "physiological": "body", "neurological": "brain", "amygdalae": "fear centers",
    "confabulation": "making things up", "anthropomorphic": "human like",
    "pareidolia": "seeing faces in things", "apophenia": "seeing patterns",
    "ostensibly": "supposedly", "idiosyncratic": "personal", "ubiquitous": "everywhere",
}


def load_word_fixes():
    """User-editable replacements: pronunciations.json + env KOKORO_WORD_FIXES."""
    fixes = {}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path in (os.path.join(root, "pronunciations.json"), "pronunciations.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if str(k).strip() and not str(k).startswith("_"):
                        fixes[str(k).strip().lower()] = str(v).strip()
                break
        except Exception:
            continue
    for pair in (os.getenv("KOKORO_WORD_FIXES", "") or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            if k.strip():
                fixes[k.strip().lower()] = v.strip()
    return fixes


def apply_word_map(text, mapping):
    if not mapping:
        return text
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in
                                           sorted(mapping, key=len, reverse=True)) + r")\b",
                         re.IGNORECASE)
    return pattern.sub(lambda m: mapping.get(m.group(0).lower(), m.group(0)), text)


def add_pauses(text, words, max_add=2):
    """Put a comma before reveal words (real pause in the voice), at most max_add."""
    tokens = text.split()
    out, added = [], 0
    for i, tok in enumerate(tokens):
        core = re.sub(r"[^\w']", "", tok).lower()
        if (i > 0 and added < max_add and core in words
                and not out[-1].endswith((",", ".", "!", "?", ";", ":"))):
            out[-1] += ","
            added += 1
        out.append(tok)
    return " ".join(out)


def prepare_for_tts(text, pause_words=()):
    """Best-sounding version: numbers, acronyms, YOUR word fixes, dramatic commas."""
    t = numbers_to_words(text)
    t = fix_acronyms(t)
    t = apply_word_map(t, load_word_fixes())
    if pause_words:
        t = add_pauses(t, set(pause_words))
    return re.sub(r"\s+", " ", t).strip()


def simplify_for_tts(text):
    """Safest version: plain ASCII letters only, jargon swapped for plain words."""
    t = numbers_to_words(text)
    t = fix_acronyms(t)
    t = apply_word_map(t, {**SIMPLE_SYNONYMS, **load_word_fixes()})
    t = re.sub(r"\.{2,}", ",", t)
    t = re.sub(r"[^A-Za-z',.?! -]", " ", t)
    t = re.sub(r"\s*-\s*", " ", t)
    t = re.sub(r"\s+([,.?!])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()
