# --- Pillow>=10 compat: moviepy 1.0.3 still uses Image.ANTIALIAS ---
import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
# ---------------------------------------------------------------------
import os
import json
import math
import time
import re
import random
import shutil
import requests
from datetime import datetime
from google import genai

from modules.composer import ShortsComposer
from modules.youtube_uploader import (
    upload_video,
    set_thumbnail,
    add_to_playlist,
)
from modules.tiktok_uploader import upload_to_tiktok
from modules.asset_manager import fetch_scene_video, fetch_extra_clips
from modules import audio as audio_mod
from modules.audio import generate_voiceover, load_word_timings, get_duration
from modules.brain import generate_script, record_history, attach_video_id
from modules.post_schedule import mark_posted

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY")

YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")
YOUTUBE_PLAYLIST_ID = os.getenv("YOUTUBE_PLAYLIST_ID", "")

TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
TIKTOK_REFRESH_TOKEN = os.getenv("TIKTOK_REFRESH_TOKEN")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

USED_TOPICS_FILE = "used_topics.json"

ENABLE_CAPTIONS = os.getenv("ENABLE_CAPTIONS", "0") == "1"
WORD_CAPTIONS = os.getenv("WORD_CAPTIONS", "1") == "1"
HOOK_CAPTION = os.getenv("HOOK_CAPTION", "1") == "1"
HOOK_TEXT = os.getenv("HOOK_TEXT", "1") == "1"        # curiosity-gap opening text on frame 0
LOOP_VIDEO = os.getenv("LOOP_VIDEO", "1") == "1"       # last scene = first scene's footage (visual loop)
FIRST_FRAME_MIN_BRIGHTNESS = int(os.getenv("FIRST_FRAME_MIN_BRIGHTNESS", "55"))  # 0-255, 0 = off
# a NEW stock clip roughly every SHOT_SECONDS (2-3 s feels like human editing)
SHOT_SECONDS = float(os.getenv("SHOT_SECONDS", "2.0"))
MAX_SHOTS_PER_SCENE = 3
MIN_SHOT_LEN = 1.4

# channel_index -> YouTube video ids uploaded in this run (used for the daily post marker)
UPLOADED_IDS = []

client = genai.Client(api_key=GEMINI_API_KEY)

ASSETS_DIR = "assets"
TEMP_VIDEO_DIR = os.path.join(ASSETS_DIR, "video_clips")
TEMP_AUDIO_DIR = os.path.join(ASSETS_DIR, "audio_clips")
SCENE_CLIP_DIR = os.path.join(ASSETS_DIR, "scene_clips")
OUTPUT_DIR = os.path.join(ASSETS_DIR, "final")

for directory in [TEMP_VIDEO_DIR, TEMP_AUDIO_DIR, SCENE_CLIP_DIR, OUTPUT_DIR]:
    os.makedirs(directory, exist_ok=True)

KEYWORD_MAP = {
    "brain": "human brain animation",
    "heart": "human heart beating",
    "eye": "human eye closeup",
    "money": "money cash dollars",
    "gold": "gold coins treasure",
    "volcano": "volcano eruption lava",
    "lightning": "lightning storm sky",
    "tsunami": "tsunami wave ocean",
}

TITLE_POOL = [
    "Your Brain Does This Every Day 🧠",
    "The Psychology Behind This 🤯",
    "Why You Really Do This 👀",
    "Most People Never Notice This",
    "This Psychology Fact Is Wild 😳",
    "You've Done This Without Knowing",
]

COMMENT_CTA_POOL = [
    "Have you noticed this too?",
    "Be honest: did this happen to you?",
    "Which one are you? Tell me below",
    "Has this ever happened to you?",
    "Did you catch yourself doing this?",
]

BASE_TAGS = [
    "shorts", "youtubeshorts", "psychology facts", "psychology", "human psychology",
    "human behavior", "psychology shorts", "mind facts", "amazing facts", "mind blowing facts",
    "behavioral psychology", "social psychology", "facts you didn't know", "viral shorts", "dark psychology facts",
]


def get_youtube_channels():
    """
    Channel 1 = existing secrets.
    Channel 2, 3... OPTIONAL: tab add hote hain jab YOUTUBE_REFRESH_TOKEN_2, _3 ... set ho.
    Har channel ka apna independent pipeline chalega.
    """
    channels = [{
        "name": "Channel 1",
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
        "refresh_token": YOUTUBE_REFRESH_TOKEN,
        "playlist_id": YOUTUBE_PLAYLIST_ID,
    }]
    # Channel 2, 3, 4... : jis number ka YOUTUBE_REFRESH_TOKEN_N set ho, wo channel add hoga
    for n in range(2, 11):
        token = os.getenv(f"YOUTUBE_REFRESH_TOKEN_{n}")
        if not token:
            continue
        channels.append({
            "name": f"Channel {n}",
            "client_id": os.getenv(f"YOUTUBE_CLIENT_ID_{n}") or YOUTUBE_CLIENT_ID,
            "client_secret": os.getenv(f"YOUTUBE_CLIENT_SECRET_{n}") or YOUTUBE_CLIENT_SECRET,
            "refresh_token": token,
            "playlist_id": os.getenv(f"YOUTUBE_PLAYLIST_ID_{n}", ""),
        })
    return channels


def notify_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message[:4000]},
            timeout=15,
        )
    except Exception as e:
        print(f"Telegram notify failed: {e}")


def get_optimized_search_query(text):
    key = re.sub(r"\s+", " ", (text or "").lower()).strip()
    return KEYWORD_MAP.get(key, text)


def _scene_prosody(index, total):
    """
    Voice energy curve (index is 1-based), HUMAN pace (~1.0-1.09x):
      hook      -> a bit faster + higher pitch
      payoff    -> slower + lower pitch (weight, so the answer lands)
      loop line -> quick, rolls straight back into the hook
      others    -> small random variation so it never sounds like a metronome
    Kokoro uses the % as speed (1 + pct/100); Edge-TTS uses it as `rate`.
    """
    if index == 1:
        return "+9%", "+3Hz"
    if total >= 4 and index == total - 1:
        return "+1%", "-2Hz"
    if index == total:
        return "+6%", "+0Hz"
    if total >= 5 and index == 3:
        return "+8%", "+1Hz"
    return random.choice(["+4%", "+5%", "+6%", "+7%"]), random.choice(["+0Hz", "+1Hz", "-1Hz"])


def _voices_with_engine(scenes, audio_dir, engine):
    paths = []
    total = len(scenes)
    for index, scene in enumerate(scenes, start=1):
        path = os.path.join(audio_dir, f"scene_{index:02d}.mp3")
        if os.path.exists(path):
            os.remove(path)
        rate, pitch = _scene_prosody(index, total)
        last_err = None
        for attempt in range(1, 4):
            try:
                generate_voiceover(scene["narration"], path, rate=rate, pitch=pitch, engine=engine)
                if os.path.exists(path) and os.path.getsize(path) > 1000:
                    break
            except Exception as e:
                last_err = e
                print(f"[Voice scene {index}] attempt {attempt} failed: {e}")
                time.sleep(2)
        else:
            raise RuntimeError(f"Scene {index} voiceover failed: {last_err}")
        paths.append(path)
    return paths


def build_scene_voiceovers(scenes, audio_dir):
    """
    Har channel ke liye alag audio_dir (files overwrite na hon).
    ONE engine for the whole video (a voice that changes mid-video sounds fake):
    try Kokoro first (natural); if any scene fails, redo ALL scenes with Edge-TTS.
    Silence is trimmed and real word timings are saved next to each mp3 (<mp3>.words.json).
    """
    pref = (audio_mod.TTS_ENGINE or "auto").lower()
    if pref in ("auto", "kokoro") and audio_mod.kokoro_available():
        try:
            paths = _voices_with_engine(scenes, audio_dir, "kokoro")
            print("Voice engine: Kokoro (%s)" % audio_mod.KOKORO_VOICE)
            return paths
        except Exception as e:
            if pref == "kokoro":
                raise
            print(f"Kokoro failed ({e}) -> using Edge-TTS for ALL scenes")
    elif pref in ("auto", "kokoro"):
        print("Kokoro not installed -> Edge-TTS fallback voice")
    paths = _voices_with_engine(scenes, audio_dir, "edge")
    print("Voice engine: Edge-TTS")
    return paths


def build_scene_clips(scenes, clip_dir, voice_paths):
    """
    Har channel ke liye alag clip_dir.
    One main clip per scene (bot ke keywords: search_keyword -> search_alt) PLUS extra
    different clips for the long middle scenes, so the picture changes every ~2 s.
    Returns (paths, extras): extras[i] = additional clips for scene i.
    """
    shutil.rmtree(clip_dir, ignore_errors=True)
    os.makedirs(clip_dir, exist_ok=True)

    paths, extras = [], []
    total = len(scenes)
    loop_on = LOOP_VIDEO and total >= 4

    for index, scene in enumerate(scenes, start=1):
        extras.append([])
        target = os.path.join(clip_dir, f"scene_{index:02d}.mp4")
        keyword = scene.get("search_keyword") or "person thinking"
        alt = scene.get("search_alt") or ""
        query = get_optimized_search_query(keyword)
        print(f"Scene {index}: '{query}' (alt: '{alt}') | {scene.get('narration', '')[:70]}")

        # visual loop: closing scene shows the hook footage again
        if loop_on and index == total and paths:
            loop_copy = os.path.join(clip_dir, f"scene_{index:02d}_loop.mp4")
            try:
                shutil.copyfile(paths[0], loop_copy)
                paths.append(loop_copy)
                print("Loop: last scene re-uses the opening footage")
                continue
            except Exception as e:
                print(f"Loop clip copy failed ({e}) - fetching a normal clip instead")

        dur = get_duration(voice_paths[index - 1]) if index - 1 < len(voice_paths) else 3.0
        min_dur = max(3, int(math.ceil(dur)))
        if index == 1 and loop_on and len(voice_paths) >= 3:
            # hook footage is reused for the loop ending: it needs room BEFORE the hook's
            # in-point (last scene plays the seconds that precede the hook)
            tail = get_duration(voice_paths[-1])
            min_dur = min(9, max(min_dur, int(math.ceil(dur + tail + 0.7))))

        try:
            if index == 1 and FIRST_FRAME_MIN_BRIGHTNESS:
                # first frame = what decides swipe vs. watch: do not accept a dark/murky clip
                try:
                    fetch_scene_video(query, target, min_duration=min_dur,
                                      min_brightness=FIRST_FRAME_MIN_BRIGHTNESS, alt_keyword=alt)
                except Exception as long_err:
                    print(f"No bright AND long clip for scene 1 ({long_err}); relaxing length")
                    try:
                        fetch_scene_video(query, target, min_duration=3,
                                          min_brightness=FIRST_FRAME_MIN_BRIGHTNESS, alt_keyword=alt)
                    except Exception as bright_err:
                        print(f"No bright clip for scene 1 ({bright_err}); accepting any clip")
                        fetch_scene_video(query, target, min_duration=3, alt_keyword=alt)
            else:
                fetch_scene_video(query, target, min_duration=min_dur, alt_keyword=alt)
            paths.append(target)
        except Exception as e:
            print(f"Scene {index} ka clip nahi mila: {e}")
            if not paths:
                raise
            paths.append(paths[-1])
            continue

        # middle scenes: more than one real clip when the sentence is long enough
        # (hook + loop scenes keep ONE clip so the seamless loop stays intact)
        if 1 < index < total:
            scene_len = dur + 0.05
            n_shots = max(1, min(MAX_SHOTS_PER_SCENE, int(round(scene_len / SHOT_SECONDS))))
            while n_shots > 1 and scene_len / n_shots < MIN_SHOT_LEN:
                n_shots -= 1
            if n_shots > 1:
                try:
                    extras[-1] = fetch_extra_clips(
                        query, os.path.join(clip_dir, f"scene_{index:02d}"), n_shots - 1,
                        min_duration=3, alt_keyword=alt,
                    )
                except Exception as e:
                    print(f"Scene {index}: extra clips skipped: {e}")
                    extras[-1] = []

    total_shots = sum(1 + len(x) for x in extras)
    print(f"Shots in video: {total_shots} clips for {total} scenes")
    return paths, extras


def build_metadata(script, full_narration):
    title_core = re.sub(r"#\S+", "", script.get("title", "")).strip()
    if not title_core:
        title_core = random.choice(TITLE_POOL)

    title = f"{title_core[:75].strip()} #Shorts"[:95]

    tags, seen, total_chars = [], set(), 0
    for tag in script.get("tags", []) + BASE_TAGS:
        tag = re.sub(r"[#,<>]", "", tag).strip().lower()
        if not tag or tag in seen:
            continue
        if total_chars + len(tag) + 1 > 450:
            break
        seen.add(tag)
        tags.append(tag)
        total_chars += len(tag) + 1

    hashtags, seen_h = [], set()
    candidates = ["#Shorts", "#Psychology", "#PsychologyFacts", "#HumanBehavior", "#MindFacts", "#Facts"]
    candidates += [
        "#" + re.sub(r"[^0-9a-zA-Z]", "", t)
        for t in script.get("tags", [])
    ]
    for candidate in candidates:
        key = candidate.lower()
        if len(candidate) < 3 or key in seen_h:
            continue
        seen_h.add(key)
        hashtags.append(candidate)
        if len(hashtags) >= 10:
            break

    body = script.get("description") or full_narration
    cta_line = script.get("comment_cta") or ""
    if cta_line:
        body = f"{body}\n\n💬 {cta_line} Tell me in the comments!"
    description = f"{body}\n\n{' '.join(hashtags)}"[:4900]

    return title, description, tags


def generate_thumbnail(video_path: str, output_path: str, title_text: str):
    import subprocess

    if not os.path.exists(video_path):
        return None

    frame_path = output_path + ".frame.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "1", "-i", video_path,
            "-frames:v", "1", "-q:v", "2", frame_path,
        ],
        capture_output=True,
    )

    if not os.path.exists(frame_path):
        print("Thumbnail frame extract nahi ho paya.")
        return None

    safe_title = re.sub(r"[^\x20-\x7E]", "", title_text)
    safe_title = re.sub(r'[":\'\\\n\r%]', "", safe_title)[:40].strip()
    if not safe_title:
        safe_title = "Amazing Fact"

    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    font_file = next((f for f in font_candidates if os.path.exists(f)), None)

    vf_parts = [
        "scale=1080:1920:force_original_aspect_ratio=increase",
        "crop=1080:1920",
    ]

    if font_file:
        vf_parts.append(
            f"drawtext=text='{safe_title}':"
            f"fontcolor=white:fontsize=72:"
            f"box=1:boxcolor=black@0.7:boxborderw=20:"
            f"x=(w-text_w)/2:y=h*0.75:"
            f"fontfile={font_file}"
        )

    cmd = [
        "ffmpeg", "-y", "-i", frame_path,
        "-vf", ",".join(vf_parts),
        "-frames:v", "1", "-q:v", "2",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if os.path.exists(frame_path):
        os.remove(frame_path)

    if result.returncode == 0 and os.path.exists(output_path):
        return output_path

    print(f"Thumbnail generate nahi hua: {result.stderr[-300:]}")
    return None


def run_channel_pipeline(channel: dict, channel_index: int) -> bool:
    """
    Ek channel ke liye poora pipeline chalata hai:
    script -> voiceover -> clips -> compose -> upload.
    Har channel ke liye alag temp directories use hoti hain taake
    parallel/sequential dono cases mein files clash na karein.
    """
    name = channel["name"]
    print(f"\n{'='*60}")
    print(f"  Starting pipeline for {name}")
    print(f"{'='*60}\n")

    # Channel-specific temp dirs (avoid overlap between channels)
    ch_audio_dir = os.path.join(TEMP_AUDIO_DIR, f"channel_{channel_index}")
    ch_clip_dir = os.path.join(SCENE_CLIP_DIR, f"channel_{channel_index}")
    ch_output_dir = os.path.join(OUTPUT_DIR, f"channel_{channel_index}")
    for d in (ch_audio_dir, ch_clip_dir, ch_output_dir):
        os.makedirs(d, exist_ok=True)

    start_time = time.time()

    # ---------- 1. Script (unique per channel) ----------
    print(f"\n[{name}] Generating fresh script...")
    script = generate_script(client, USED_TOPICS_FILE)
    if not script:
        msg = f"[{name}] Script generation failed."
        print(msg)
        notify_telegram(msg)
        return False

    record_history(USED_TOPICS_FILE, script)
    scenes = script["scenes"]
    full_narration = " ".join(s["narration"] for s in scenes)
    print(f"[{name}] {len(scenes)} scenes | Hook: {scenes[0]['narration']}")

    # ---------- 2. Voiceover ----------
    print(f"\n[{name}] Generating voiceovers...")
    try:
        voice_paths = build_scene_voiceovers(scenes, ch_audio_dir)
    except Exception as e:
        msg = f"[{name}] Voiceover failed: {e}"
        print(msg)
        notify_telegram(msg)
        return False

    # ---------- 3. Stock clips ----------
    print(f"\n[{name}] Downloading stock clips...")
    try:
        clip_paths, extra_clip_paths = build_scene_clips(scenes, ch_clip_dir, voice_paths)
    except Exception as e:
        msg = f"[{name}] Video download failed: {e}"
        print(msg)
        notify_telegram(msg)
        return False

    # ---------- 4. Compose ----------
    print(f"\n[{name}] Composing final video...")
    composer = ShortsComposer(output_dir=ch_output_dir)

    bg_music_path = None
    for candidate in [
        os.path.join("assets", "bgm"),
        os.path.join("modules", "bg_music.mp3"),
    ]:
        if os.path.isdir(candidate):
            files = [f for f in os.listdir(candidate) if f.lower().endswith(".mp3")]
            if files:
                bg_music_path = os.path.join(candidate, random.choice(files))
                print(f"[{name}] BG music: {bg_music_path}")
                break
        elif os.path.isfile(candidate):
            bg_music_path = candidate
            print(f"[{name}] BG music: {bg_music_path}")
            break

    if WORD_CAPTIONS:
        captions = None
    elif ENABLE_CAPTIONS:
        captions = [s.get("caption", "") for s in scenes]
    elif HOOK_CAPTION:
        captions = [""] * len(scenes)
        captions[0] = scenes[0].get("caption", "")
        if len(scenes) > 4:
            captions[-2] = scenes[-2].get("caption", "")
    else:
        captions = None

    hook_text = None
    if HOOK_TEXT:
        hook_text = script.get("hook_text") or scenes[0].get("caption") or scenes[0].get("narration") or ""
        print(f"[{name}] Opening text: {hook_text!r}")

    comment_cta = script.get("comment_cta") or random.choice(COMMENT_CTA_POOL)
    script["comment_cta"] = comment_cta
    print(f"[{name}] Comment CTA: {comment_cta!r}")

    output_filename = f"final_short_{channel_index}.mp4"
    try:
        final_video_path = composer.create_multi_scene_short(
            clip_paths=clip_paths,
            extra_clip_paths=extra_clip_paths,
            voiceover_paths=voice_paths,
            output_filename=output_filename,
            bg_music_path=bg_music_path,
            add_cta=False,
            scene_narrations=captions,
            word_scenes=scenes if WORD_CAPTIONS else None,
            hook_text=hook_text,
            comment_cta=comment_cta,
            word_timings=[load_word_timings(p) for p in voice_paths],
            loop_visual=LOOP_VIDEO,
        )
    except Exception as e:
        msg = f"[{name}] Composition failed: {e}"
        print(msg)
        notify_telegram(msg)
        return False

    if not os.path.exists(final_video_path):
        msg = f"[{name}] Final video file not created."
        print(msg)
        notify_telegram(msg)
        return False

    # ---------- 5. Thumbnail ----------
    print(f"\n[{name}] Generating thumbnail...")
    thumb_path = os.path.join(ch_output_dir, f"thumbnail_{channel_index}.jpg")
    generate_thumbnail(final_video_path, thumb_path, script.get("title", "Amazing Fact"))

    # ---------- 6. Upload to YouTube ----------
    print(f"\n[{name}] Uploading to YouTube...")
    title, description, tags = build_metadata(script, full_narration)
    print(f"[{name}] Title: {title}")

    try:
        video_id = upload_video(
            video_path=final_video_path,
            title=title,
            description=description,
            tags=tags,
            privacy_status="public",
            client_id=channel["client_id"],
            client_secret=channel["client_secret"],
            refresh_token=channel["refresh_token"],
        )
        print(f"[{name}] Video uploaded! ID: {video_id}")
        print(f"https://youtube.com/shorts/{video_id}")
        attach_video_id(USED_TOPICS_FILE, video_id, name)
        UPLOADED_IDS.append(video_id)

        if os.path.exists(thumb_path):
            print(f"[{name}] Setting thumbnail...")
            set_thumbnail(
                video_id,
                thumb_path,
                channel["client_id"],
                channel["client_secret"],
                channel["refresh_token"],
            )

        if channel["playlist_id"]:
            print(f"[{name}] Adding to playlist...")
            add_to_playlist(
                video_id,
                channel["playlist_id"],
                channel["client_id"],
                channel["client_secret"],
                channel["refresh_token"],
            )

        elapsed = time.time() - start_time
        notify_telegram(
            f"[{name}] Video uploaded!\n"
            f"{title}\n"
            f"https://youtube.com/shorts/{video_id}\n"
            f"{elapsed:.0f}s"
        )

    except Exception as e:
        msg = f"[{name}] YouTube Upload Failed: {e}"
        print(msg)
        notify_telegram(msg)
        return False

    # ---------- 7. TikTok (sirf channel 1 ke liye, ya jis channel par chahiye) ----------
    # NOTE: TikTok par same video dono channels se post karna weird lagega,
    # isliye sirf pehle channel ke liye TikTok upload kar rahe hain.
    if channel_index == 0 and TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN:
        print(f"\n[{name}] Uploading to TikTok (draft)...")
        try:
            tiktok_publish_id = upload_to_tiktok(
                video_path=final_video_path,
                title=title,
                client_key=TIKTOK_CLIENT_KEY,
                client_secret=TIKTOK_CLIENT_SECRET,
                refresh_token=TIKTOK_REFRESH_TOKEN,
            )
            print(f"[{name}] TikTok upload complete! Publish ID: {tiktok_publish_id}")

            notify_telegram(
                f"[{name}] TikTok draft uploaded!\n"
                f"{title}\n"
                f"Publish ID: {tiktok_publish_id}\n"
                f"Open TikTok app to post manually"
            )
        except Exception as e:
            msg = f"[{name}] TikTok Upload Failed: {e}"
            print(msg)
            notify_telegram(msg)
    elif channel_index == 0:
        print(f"\n[{name}] TikTok credentials missing - skipping TikTok upload.")

    elapsed = time.time() - start_time
    print(f"\n[{name}] Pipeline complete in {elapsed:.0f}s")
    return True


def main():
    print("Starting Automated Short Pipeline...")
    channels = get_youtube_channels()
    print(f"Found {len(channels)} channel(s) to process.\n")

    # Posting time is decided by the `gate` job (modules/post_schedule.py): ONE random
    # moment per day. When this runs, the videos simply go public right now.
    manual = os.getenv("EVENT_NAME", "") == "workflow_dispatch"
    print("Manual run: test post (does not use up today's daily slot)." if manual
          else "Scheduled run: posting now (random daily slot picked by the gate).")

    results = []
    for idx, channel in enumerate(channels):
        try:
            ok = run_channel_pipeline(channel, idx)
            results.append((channel["name"], ok))
        except Exception as e:
            print(f"[{channel['name']}] Pipeline crashed: {e}")
            notify_telegram(f"[{channel['name']}] Pipeline crashed: {e}")
            results.append((channel["name"], False))

    print("\n" + "=" * 60)
    print("  FINAL SUMMARY")
    print("=" * 60)
    for name, ok in results:
        status = "✅ SUCCESS" if ok else "❌ FAILED"
        print(f"  {name}: {status}")

    # Today's post is done as soon as at least one channel really uploaded - otherwise the next
    # hourly wake-up would post the same day again. If everything failed we do NOT mark it,
    # so the gate retries (catch-up) in the next hour.
    if UPLOADED_IDS and not manual:
        try:
            mark_posted(",".join(UPLOADED_IDS))
            print("Daily post marked in post_state.json")
        except Exception as e:
            print(f"mark_posted failed (ignored): {e}")

    # Agar koi bhi channel fail hua to overall exit non-zero
    if not all(ok for _, ok in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
