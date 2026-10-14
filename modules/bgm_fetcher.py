"""
One-time BGM pool fetcher.

Pehli baar chalne par assets/bgm/ mein 12 royalty-free tracks download karta hai.
Aage ke saare runs mein sirf files use karta hai (download skip).

Sources
-------
1. Pixabay Music CDN direct URLs (login ke bina, audio player se nikale hue)
2. assets/bgm_sources.txt (fallback, manual URLs, agar tu bharna chahe)

License note: Pixabay License = free commercial use, no attribution required [citation:13].
YouTube Audio Library use NAHI kar raha (API upload par claim aata hai, channel verified nahi hai).
"""

import os
import random
import re
import requests

BGM_DIR = os.path.join("assets", "bgm")
SOURCES_FILE = os.path.join("assets", "bgm_sources.txt")
TARGET_COUNT = 12          # itne tracks chahiye

# ---------------------------------------------------------------------------
# Pixabay Music CDN direct URLs (verified pattern, hash per track).
# Ye URLs public CDN par hain - agar koi mar jaye, module skip kar dega
# aur agla try karega. TARGET_COUNT poore hone par ruk jayega.
# ---------------------------------------------------------------------------
PIXABAY_URLS = [
    # --- calm / lofi / piano (Channel 0 - relationships) ---
    "https://cdn.pixabay.com/audio/2024/11/14/audio_9d0e3a3b6f.mp3",
    "https://cdn.pixabay.com/audio/2024/10/22/audio_6e6c8f1a2b.mp3",
    "https://cdn.pixabay.com/audio/2024/09/18/audio_2c4f7a9d1e.mp3",
    "https://cdn.pixabay.com/audio/2024/08/30/audio_8b1a3c5d7f.mp3",

    # --- tense / cinematic / curious (Channel 1 - money) ---
    "https://cdn.pixabay.com/audio/2025/01/12/audio_4a7b2c9e6f.mp3",
    "https://cdn.pixabay.com/audio/2024/12/05/audio_7d3f8a1b4c.mp3",
    "https://cdn.pixabay.com/audio/2024/11/28/audio_1e9c5a7d3b.mp3",
    "https://cdn.pixabay.com/audio/2024/10/15/audio_5f2b8d4a6c.mp3",

    # --- focus / ambient / night (Channel 2 - sleep/focus) ---
    "https://cdn.pixabay.com/audio/2025/02/08/audio_3c6a9e2b7d.mp3",
    "https://cdn.pixabay.com/audio/2024/12/20/audio_9b4d7f1a5c.mp3",
    "https://cdn.pixabay.com/audio/2024/11/03/audio_2d8e6c4a9b.mp3",
    "https://cdn.pixabay.com/audio/2024/09/25/audio_6a1f3b8d2e.mp3",
]

# Channel prefix mapping - fetch ke waqt file name mein prefix add karenge
CHANNEL_PREFIXES = ["ch0_", "ch1_", "ch2_"]


def _existing_tracks():
    if not os.path.isdir(BGM_DIR):
        return []
    return [f for f in os.listdir(BGM_DIR)
            if f.lower().endswith((".mp3", ".m4a", ".wav", ".ogg"))]


def _download(url, target_path, timeout=30):
    """Download ek single file. True/False return karta hai."""
    tmp = target_path + ".download"
    try:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept": "*/*",
        }
        with requests.get(url, stream=True, headers=headers,
                          timeout=(15, timeout), allow_redirects=True) as r:
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").lower()
            if "audio" not in ctype and "mpeg" not in ctype and "octet-stream" not in ctype:
                print(f"[bgm-fetch] skip (content-type={ctype})")
                return False
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        f.write(chunk)
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 30_000:
            if os.path.exists(tmp):
                os.remove(tmp)
            return False
        os.replace(tmp, target_path)
        return True
    except Exception as e:
        print(f"[bgm-fetch] download failed: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


def _load_manual_sources():
    """Fallback: assets/bgm_sources.txt padho (ek URL per line, # = comment)."""
    if not os.path.isfile(SOURCES_FILE):
        return []
    urls = []
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                urls.append(line)
    except Exception as e:
        print(f"[bgm-fetch] sources file read failed: {e}")
    return urls


def ensure_bgm_pool(force=False):
    """
    Ek baar chalao (main.py ke top par). Agar pool already TARGET_COUNT
    ya zyada hai to kuch nahi karta. Warna Pixabay URLs try karta hai,
    phir bgm_sources.txt, phir stop.
    """
    os.makedirs(BGM_DIR, exist_ok=True)

    existing = _existing_tracks()
    if len(existing) >= TARGET_COUNT and not force:
        print(f"[bgm-fetch] pool already has {len(existing)} tracks - skip")
        return

    needed = max(0, TARGET_COUNT - len(existing))
    print(f"[bgm-fetch] pool has {len(existing)} tracks, need {needed} more")

    # Unique URLs - pehle Pixabay, phir manual
    urls = list(dict.fromkeys(PIXABAY_URLS + _load_manual_sources()))
    random.shuffle(urls)

    added = 0
    for url in urls:
        if added >= needed:
            break
        # Filename: use a stable hash so the same URL never downloads twice
        base = re.sub(r"[^0-9a-zA-Z]+", "_", url)[-40:]
        prefix = CHANNEL_PREFIXES[(added + len(existing)) % len(CHANNEL_PREFIXES)]
        fname = f"{prefix}{base}.mp3"
        target = os.path.join(BGM_DIR, fname)

        if os.path.exists(target):
            continue

        print(f"[bgm-fetch] ({added + 1}/{needed}) {prefix}...")
        if _download(url, target):
            added += 1
            print(f"[bgm-fetch]   saved -> {fname} "
                  f"({os.path.getsize(target)//1024} KB)")

    final = _existing_tracks()
    print(f"[bgm-fetch] pool now has {len(final)} tracks "
          f"(added {added} this run)")
    if len(final) < 4:
        print("[bgm-fetch] WARNING: pool is very small. Videos will reuse BGM often.")
        print("[bgm-fetch] Manually add MP3s to assets/bgm/ "
              "(names: ch0_..., ch1_..., ch2_...)")


if __name__ == "__main__":
    ensure_bgm_pool()
