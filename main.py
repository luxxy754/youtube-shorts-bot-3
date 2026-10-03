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
from modules.brain import generate_script, record_history

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

# Captions: short punchy key-phrase on screen (Roman, so fonts never break).
# You removed captions earlier, so default is OFF. Set ENABLE_CAPTIONS: '1' in run.yml to enable.
ENABLE_CAPTIONS = os.getenv("ENABLE_CAPTIONS", "0") == "1"
# Word-by-word Hinglish captions (different fonts/sizes, spoken word highlighted).
# When ON it replaces the two caption modes below. Set WORD_CAPTIONS: '0' to turn off.
WORD_CAPTIONS = os.getenv("WORD_CAPTIONS", "1") == "1"
# Big text only on the hook scene (+ twist scene). This is what grabs the first 3 seconds.
HOOK_CAPTION = os.getenv("HOOK_CAPTION", "1") == "1"

client = genai.Client(api_key=GEMINI_API_KEY)

ASSETS_DIR = "assets"
TEMP_VIDEO_DIR = os.path.join(ASSETS_DIR, "video_clips")
TEMP_AUDIO_DIR = os.path.join(ASSETS_DIR, "audio_clips")
SCENE_CLIP_DIR = os.path.join(ASSETS_DIR, "scene_clips")
OUTPUT_DIR = os.path.join(ASSETS_DIR, "final")

for directory in [TEMP_VIDEO_DIR, TEMP_AUDIO_DIR, SCENE_CLIP_DIR, OUTPUT_DIR]:
    os.makedirs(directory, exist_ok=True)

# Exact-phrase overrides only. The old code did substring matching, so
# "ant" matched "giant"/"plant" and "bee" matched "been" -> wrong footage.
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
    """Channel 1 = existing secrets. Channel 2 is OPTIONAL: it is used only when
    YOUTUBE_REFRESH_TOKEN_2 is set. If CLIENT_ID_2 / CLIENT_SECRET_2 are not set,
    channel 1's client id/secret are reused. Playlist ids are per-channel."""
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


def build_scene_voiceovers(scenes):
    paths = []
    for index, scene in enumerate(scenes, start=1):
        path = os.path.join(TEMP_AUDIO_DIR, f"scene_{index:02d}.mp3")
        if os.path.exists(path):
            os.remove(path)

        # Voice with emotion: slower/deeper on hook and twist, brisk in the middle.
        if index == 1:
            rate, pitch = "+0%", "-3Hz"
        elif index == len(scenes) - 1 and len(scenes) > 4:
            rate, pitch = "-4%", "-3Hz"
        elif index == len(scenes):
            rate, pitch = "+4%", "+0Hz"
        else:
            rate, pitch = None, None

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


def build_scene_clips(scenes):
    shutil.rmtree(SCENE_CLIP_DIR, ignore_errors=True)
    os.makedirs(SCENE_CLIP_DIR, exist_ok=True)

    paths = []
    for index, scene in enumerate(scenes, start=1):
        target = os.path.join(SCENE_CLIP_DIR, f"scene_{index:02d}.mp4")
        keyword = scene.get("search_keyword") or "nature landscape"
        query = get_optimized_search_query(keyword)
        print(f"Scene {index}: '{query}'")

        try:
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

    safe_title = re.sub(r"[^\x20-\x7E]", "", title_text)  # emoji/Devanagari -> tofu boxes in DejaVu
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


def main():
    print("Starting Automated Short Pipeline...")
    start_time = time.time()

    print("\nGenerating 30-40s Short script (writer + fact-check editor)...")
    script = generate_script(client, USED_TOPICS_FILE)
    if not script:
        print("Script generation failed.")
        notify_telegram("Pipeline failed: script generation returned None")
        return

    record_history(USED_TOPICS_FILE, script)
    scenes = script["scenes"]
    full_narration = " ".join(s["narration"] for s in scenes)
    print(f"{len(scenes)} scenes | Hook: {scenes[0]['narration']}")

    print("\nGenerating scene-wise Voiceover (Natural Hindi - Madhur)...")
    try:
        voice_paths = build_scene_voiceovers(scenes)
    except Exception as e:
        print(f"Voiceover failed: {e}")
        notify_telegram(f"Voiceover generation failed: {e}")
        return

    print("\nDownloading a different Stock Video for every scene...")
    try:
        clip_paths = build_scene_clips(scenes)
    except Exception as e:
        print(f"Video download failed: {e}")
        notify_telegram(f"Video download failed: {e}")
        return

    print("\nMerging Video & Audio...")
    composer = ShortsComposer(output_dir=OUTPUT_DIR)

    bg_music_path = None
    for candidate in [
        os.path.join("assets", "bgm"),
        os.path.join("modules", "bg_music.mp3"),
    ]:
        if os.path.isdir(candidate):
            files = [
                f for f in os.listdir(candidate)
                if f.lower().endswith(".mp3")
            ]
            if files:
                bg_music_path = os.path.join(candidate, random.choice(files))
                print(f"BG music: {bg_music_path}")
                break
        elif os.path.isfile(candidate):
            bg_music_path = candidate
            print(f"BG music: {bg_music_path}")
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
    try:
        final_video_path = composer.create_multi_scene_short(
            clip_paths=clip_paths,
            voiceover_paths=voice_paths,
            output_filename="final_short.mp4",
            bg_music_path=bg_music_path,
            add_cta=False,
            scene_narrations=captions,
            word_scenes=scenes if WORD_CAPTIONS else None,
        )
    except Exception as e:
        print(f"Composition failed: {e}")
        notify_telegram(f"Video composition failed: {e}")
        return

    if not os.path.exists(final_video_path):
        print("Final video file create nahi hui.")
        notify_telegram("Final video file not created")
        return

    print("\nGenerating thumbnail...")
    thumb_path = os.path.join(OUTPUT_DIR, "thumbnail.jpg")
    generate_thumbnail(
        final_video_path,
        thumb_path,
        script.get("title", "Amazing Fact"),
    )

    print("\nUploading Video to YouTube...")
    title, description, tags = build_metadata(script, full_narration)
    print(f"Title: {title}")
    print(f"Tags: {len(tags)} tags")

    # Each channel is independent: if one fails, the other still gets its upload.
    for ch in get_youtube_channels():
        name = ch["name"]
        print(f"\n[{name}] Uploading...")
        try:
            video_id = upload_video(
                video_path=final_video_path,
                title=title,
                description=description,
                tags=tags,
                privacy_status="public",
                client_id=ch["client_id"],
                client_secret=ch["client_secret"],
                refresh_token=ch["refresh_token"],
            )
            print(f"[{name}] Video uploaded! ID: {video_id}")
            print(f"https://youtube.com/shorts/{video_id}")

            if os.path.exists(thumb_path):
                print(f"[{name}] Setting thumbnail...")
                set_thumbnail(
                    video_id,
                    thumb_path,
                    ch["client_id"],
                    ch["client_secret"],
                    ch["refresh_token"],
                )

            if ch["playlist_id"]:
                print(f"[{name}] Adding to playlist...")
                add_to_playlist(
                    video_id,
                    ch["playlist_id"],
                    ch["client_id"],
                    ch["client_secret"],
                    ch["refresh_token"],
                )

            elapsed = time.time() - start_time
            notify_telegram(
                f"[{name}] Video uploaded!\n"
                f"{title}\n"
                f"https://youtube.com/shorts/{video_id}\n"
                f"{elapsed:.0f}s"
            )

        except Exception as e:
            print(f"[{name}] YouTube Upload Failed: {e}")
            notify_telegram(f"[{name}] YouTube upload failed: {e}")

    if TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and TIKTOK_REFRESH_TOKEN:
        print("\nUploading Video to TikTok (draft)...")
        try:
            tiktok_publish_id = upload_to_tiktok(
                video_path=final_video_path,
                title=title,
                client_key=TIKTOK_CLIENT_KEY,
                client_secret=TIKTOK_CLIENT_SECRET,
                refresh_token=TIKTOK_REFRESH_TOKEN,
            )
            print(f"TikTok upload complete! Publish ID: {tiktok_publish_id}")

            notify_telegram(
                f"TikTok draft uploaded!\n"
                f"{title}\n"
                f"Publish ID: {tiktok_publish_id}\n"
                f"Open TikTok app to post manually"
            )
        except Exception as e:
            print(f"TikTok Upload Failed: {e}")
            notify_telegram(f"TikTok upload failed: {e}")
    else:
        print("\nTikTok credentials missing - skipping TikTok upload.")

    elapsed = time.time() - start_time
    print(f"\nPipeline complete in {elapsed:.0f}s")


if __name__ == "__main__":
    main()
