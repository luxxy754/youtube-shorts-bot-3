import os
import json
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
from modules.asset_manager import fetch_scene_video
from modules.audio import generate_voiceover
from modules.brain import generate_script, record_history, attach_video_id

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
FIRST_FRAME_MIN_BRIGHTNESS = int(os.getenv("FIRST_FRAME_MIN_BRIGHTNESS", "55"))  # 0-255, 0 = off

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
    "Ye Kaise Possible Hai? 😱",
    "Duniya Ka Sabse Bada Raaz!",
    "Scientists Bhi Confuse! 🤯",
    "Ye Sach Hai Ya Jhoot?",
    "Aapko Yakeen Nahi Hoga!",
    "Ye Cheez Real Hai!",
    "Ye Mat Karna Kabhi!",
]

BASE_TAGS = [
    "shorts", "youtubeshorts", "facts", "hindi facts", "urdu facts",
    "amazing facts", "mysteries", "viral shorts", "science facts",
    "crazy facts", "mind blowing", "unbelievable", "dangerous facts",
    "what if", "how many",
]


def get_youtube_channels():
    """
    Channel 1 = existing secrets.
    Channel 2 is OPTIONAL: used only when YOUTUBE_REFRESH_TOKEN_2 is set.
    Har channel ka apna independent pipeline chalega.
    """
    channels = [{
        "name": "Channel 1",
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
        "refresh_token": YOUTUBE_REFRESH_TOKEN,
        "playlist_id": YOUTUBE_PLAYLIST_ID,
    }]
    token2 = os.getenv("YOUTUBE_REFRESH_TOKEN_2")
    if token2:
        channels.append({
            "name": "Channel 2",
            "client_id": os.getenv("YOUTUBE_CLIENT_ID_2") or YOUTUBE_CLIENT_ID,
            "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET_2") or YOUTUBE_CLIENT_SECRET,
            "refresh_token": token2,
            "playlist_id": os.getenv("YOUTUBE_PLAYLIST_ID_2", ""),
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


def scene_prosody(index, total, text):
    """
    Edge TTS only exposes rate/pitch, so vary those by the role of the line to avoid a flat read:
    hook = firm and slightly slower, twist = slow + low (weight), last = a touch faster,
    questions rise, exclamations push, everything else gets small random variation so no two
    neighbouring sentences have the identical melody.
    """
    text = (text or "").strip()
    if index == 1:
        return "+2%", "-3Hz"
    if index == total - 1 and total > 4:
        return "-6%", "-4Hz"
    if index == total:
        return "+4%", "+0Hz"
    if text.endswith("?"):
        return "+6%", "+4Hz"
    if text.endswith("!"):
        return "+11%", "+2Hz"
    rate = random.choice([6, 8, 10, 12])
    pitch = random.choice([-2, -1, 0, 1, 2])
    return f"+{rate}%", f"{pitch:+d}Hz"


def build_scene_voiceovers(scenes, audio_dir):
    """Har channel ke liye alag audio_dir use hoga, taake files overwrite na hon."""
    paths = []
    for index, scene in enumerate(scenes, start=1):
        path = os.path.join(audio_dir, f"scene_{index:02d}.mp3")
        if os.path.exists(path):
            os.remove(path)

        rate, pitch = scene_prosody(index, len(scenes), scene["narration"])

        for attempt in range(1, 4):
            try:
                generate_voiceover(scene["narration"], path, rate=rate, pitch=pitch)
                if os.path.exists(path) and os.path.getsize(path) > 1000:
                    break
            except Exception as e:
                print(f"[Voice scene {index}] attempt {attempt} failed: {e}")
                time.sleep(2)
        else:
            raise RuntimeError(f"Scene {index} ki voiceover generate nahi ho saki.")
        paths.append(path)
    return paths


def build_scene_clips(scenes, clip_dir):
    """Har channel ke liye alag clip_dir use hoga."""
    shutil.rmtree(clip_dir, ignore_errors=True)
    os.makedirs(clip_dir, exist_ok=True)

    paths = []
    for index, scene in enumerate(scenes, start=1):
        target = os.path.join(clip_dir, f"scene_{index:02d}.mp4")
        keyword = scene.get("search_keyword") or "nature landscape"
        query = get_optimized_search_query(keyword)
        print(f"Scene {index}: '{query}'")

        try:
            if index == 1 and FIRST_FRAME_MIN_BRIGHTNESS:
                # first frame = what decides swipe vs. watch: do not accept a dark/murky clip
                try:
                    fetch_scene_video(query, target, min_duration=3,
                                      min_brightness=FIRST_FRAME_MIN_BRIGHTNESS)
                except Exception as bright_err:
                    print(f"No bright clip for scene 1 ({bright_err}); accepting any clip")
                    fetch_scene_video(query, target, min_duration=3)
            else:
                fetch_scene_video(query, target, min_duration=3)
            paths.append(target)
        except Exception as e:
            print(f"Scene {index} ka clip nahi mila: {e}")
            if not paths:
                raise
            paths.append(paths[-1])
    return paths


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
    candidates = ["#Shorts", "#Facts", "#HindiFacts", "#UrduFacts", "#AmazingFacts", "#CrazyFacts", "#WhatIf", "#DangerousFacts"]
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
        clip_paths = build_scene_clips(scenes, ch_clip_dir)
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
        hook_text = scenes[0].get("caption") or scenes[0].get("roman") or ""
        print(f"[{name}] Opening text: {hook_text!r}")

    output_filename = f"final_short_{channel_index}.mp4"
    try:
        final_video_path = composer.create_multi_scene_short(
            clip_paths=clip_paths,
            voiceover_paths=voice_paths,
            output_filename=output_filename,
            bg_music_path=bg_music_path,
            add_cta=False,
            scene_narrations=captions,
            word_scenes=scenes if WORD_CAPTIONS else None,
            hook_text=hook_text,
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

    # Agar koi bhi channel fail hua to overall exit non-zero
    if not all(ok for _, ok in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
