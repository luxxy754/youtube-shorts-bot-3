import os
import random
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from moviepy.editor import (
    VideoFileClip, AudioFileClip, CompositeAudioClip,
    concatenate_audioclips, concatenate_videoclips, vfx,
    ImageClip, CompositeVideoClip
)
import moviepy.audio.fx.all as afx

from modules.audio import build_final_audio, INTER_SCENE_PAUSE

TARGET_W = 1080
TARGET_H = 1920

BG_MUSIC_VOLUME = 0.15
SCENE_GAP = INTER_SCENE_PAUSE

# ============================================================
# CAPTION SETTINGS — PIL based (no ImageMagick needed)
# ============================================================
CAPTION_FONT_SIZE = 72
CAPTION_POSITION_RATIO = 0.55
CAPTION_FADE = 0.10
CAPTION_MAX_WIDTH = TARGET_W - 100

# CTA settings
CTA_TEXT = "Follow for more"
CTA_FONT_SIZE = 58
CTA_POSITION_RATIO = 0.85
CTA_START_RATIO = 0.55
CTA_FADE_DURATION = 0.5


class ShortsComposer:
    def __init__(self, output_dir="output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    @staticmethod
    def _get_font_file():
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "assets/fonts/DejaVuSans-Bold.ttf",
            "assets/fonts/NotoSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for f in font_candidates:
            if os.path.exists(f):
                print("Font found: " + f)
                return f

        try:
            import subprocess
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", "sans:bold"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                path = result.stdout.strip()
                if os.path.exists(path):
                    print("Font via fc-match: " + path)
                    return path
        except Exception as e:
            print("fc-match failed: " + str(e))

        print("Koi bhi font nahi mila!")
        return None

    @staticmethod
    def _fit_vertical(clip):
        if clip.w / clip.h > TARGET_W / TARGET_H:
            clip = clip.resize(height=TARGET_H)
            clip = clip.crop(x_center=clip.w / 2, width=TARGET_W)
        else:
            clip = clip.resize(width=TARGET_W)
            clip = clip.crop(y_center=clip.h / 2, height=TARGET_H)
        if (clip.w, clip.h) != (TARGET_W, TARGET_H):
            clip = clip.resize((TARGET_W, TARGET_H))
        return clip

    @staticmethod
    def _prepare_scene_video(path, duration, punchy=False):
        try:
            clip = VideoFileClip(path, audio=False)
        except Exception as e:
            raise RuntimeError("VideoFileClip fail: " + path + ": " + str(e))

        if clip.duration is None or clip.duration <= 0:
            clip.close()
            raise RuntimeError("Clip duration invalid: " + path)

        if clip.duration < duration + 0.2:
            try:
                clip = clip.fx(vfx.loop, duration=duration + 0.5)
            except Exception as e:
                clip.close()
                raise RuntimeError("Loop fail: " + path + ": " + str(e))
        else:
            spare = max(0.0, clip.duration - duration - 0.2)
            start = random.uniform(0, spare) if spare > 0.1 else 0.0
            end = start + duration
            if end > clip.duration:
                end = clip.duration
                start = max(0.0, end - duration)
            try:
                clip = clip.subclip(start, end)
            except Exception as e:
                clip.close()
                raise RuntimeError("Subclip fail: " + path + ": " + str(e))

        fitted = ShortsComposer._fit_vertical(clip)
        zoom_amount = 0.08
        if punchy:
            zoom_amount = 0.16
            try:
                fitted = fitted.fx(vfx.lum_contrast, lum=14, contrast=0.22, contrast_thr=120)
            except Exception as e:
                print("First-scene punch-up skipped: " + str(e))
        try:
            d = max(duration, 0.5)
            zoomed = fitted.resize(lambda t: 1 + zoom_amount * min(t, d) / d)
            return CompositeVideoClip(
                [zoomed.set_position("center")], size=(TARGET_W, TARGET_H)
            ).set_duration(fitted.duration)
        except Exception:
            return fitted

    # ========================================================
    # PIL CAPTION — ImageMagick ki zaroorat NAHI
    # ========================================================
    @staticmethod
    def _make_caption_png(text, font_file, font_size=CAPTION_FONT_SIZE):
        try:
            if not text or not text.strip() or not font_file:
                return None

            display_text = text.strip().upper()
            if len(display_text) > 55:
                display_text = display_text[:52] + "..."

            words = display_text.split()
            if len(words) > 5:
                mid = len(words) // 2
                line1 = " ".join(words[:mid])
                line2 = " ".join(words[mid:])
                lines = [line1, line2]
            else:
                lines = [display_text]

            try:
                font = ImageFont.truetype(font_file, font_size)
            except Exception as e:
                print("PIL font load fail: " + str(e))
                font = ImageFont.load_default()

            dummy_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
            dummy_draw = ImageDraw.Draw(dummy_img)

            line_heights = []
            line_widths = []
            for line in lines:
                bbox = dummy_draw.textbbox((0, 0), line, font=font, stroke_width=6)
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                line_widths.append(w)
                line_heights.append(h)

            max_width = max(line_widths) if line_widths else 0
            total_height = sum(line_heights) + (len(lines) - 1) * 15

            pad_x = 40
            pad_y = 30

            img_w = max_width + pad_x * 2
            img_h = total_height + pad_y * 2

            img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            y_offset = pad_y
            for i, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font, stroke_width=6)
                line_w = bbox[2] - bbox[0]
                x = (img_w - line_w) // 2
                draw.text(
                    (x, y_offset),
                    line,
                    font=font,
                    fill=(255, 255, 255, 255),
                    stroke_width=6,
                    stroke_fill=(0, 0, 0, 255),
                )
                y_offset += line_heights[i] + 15

            return img

        except Exception as e:
            print("Caption PNG creation error: " + str(e))
            return None

    def _make_caption_overlay(self, text, start_time, duration, font_file):
        try:
            png_img = self._make_caption_png(text, font_file)
            if png_img is None:
                return None

            img_array = np.array(png_img)

            caption_clip = ImageClip(img_array, transparent=True)
            caption_clip = caption_clip.set_duration(duration)
            caption_clip = caption_clip.set_start(start_time)

            caption_clip = caption_clip.set_position(
                ("center", int(TARGET_H * CAPTION_POSITION_RATIO))
            )

            caption_clip = caption_clip.crossfadein(CAPTION_FADE).crossfadeout(CAPTION_FADE)
            caption_clip = caption_clip.set_opacity(1.0)

            print("Caption added at " + str(round(start_time, 1)) + "s")
            return caption_clip
        except Exception as e:
            print("Caption overlay error: " + str(e))
            return None

    def _make_hook_banner(self, text, font_file, duration):
        try:
            text = " ".join(str(text or "").split()).upper()
            if not text:
                return None
            if len(text) > 42:
                text = text[:40].rstrip() + "..."

            font_path = font_file
            try:
                from modules.captions import ensure_fonts
                fonts = ensure_fonts()
                font_path = fonts.get("anton") or fonts.get("lilita") or font_file
            except Exception:
                pass
            if not font_path:
                return None

            font = ImageFont.truetype(font_path, 104)
            dummy = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
            max_w = TARGET_W - 160
            lines, cur = [], ""
            for word in text.split():
                trial = (cur + " " + word).strip()
                if cur and dummy.textlength(trial, font=font) > max_w:
                    lines.append(cur)
                    cur = word
                else:
                    cur = trial
            if cur:
                lines.append(cur)
            lines = lines[:3]

            line_h = 104 + 14
            pad_x, pad_y = 44, 30
            box_w = int(max(dummy.textlength(l, font=font) for l in lines)) + pad_x * 2
            box_h = line_h * len(lines) + pad_y * 2
            img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=36, fill=(0, 0, 0, 175))
            y = pad_y
            for l in lines:
                w = dummy.textlength(l, font=font)
                draw.text(((box_w - w) / 2, y), l, font=font, fill=(255, 221, 0, 255),
                          stroke_width=7, stroke_fill=(0, 0, 0, 255))
                y += line_h

            clip = ImageClip(np.array(img), transparent=True).set_duration(duration).set_start(0)
            clip = clip.crossfadeout(0.25)
            return clip.set_position(("center", int(TARGET_H * 0.15)))
        except Exception as e:
            print("Hook banner error: " + str(e))
            return None

    def _make_cta_overlay(self, total_duration, font_file):
        try:
            if not font_file:
                return None

            png_img = self._make_caption_png(CTA_TEXT, font_file, font_size=CTA_FONT_SIZE)
            if png_img is None:
                return None

            img_array = np.array(png_img)
            cta_clip = ImageClip(img_array, transparent=True)
            cta_clip = cta_clip.set_duration(total_duration)
            cta_clip = cta_clip.set_position(
                ("center", int(TARGET_H * CTA_POSITION_RATIO))
            )

            start_time = total_duration * CTA_START_RATIO
            cta_clip = cta_clip.set_start(start_time)
            cta_clip = cta_clip.crossfadein(CTA_FADE_DURATION)
            cta_clip = cta_clip.set_opacity(1.0)
            return cta_clip
        except Exception as e:
            print("CTA error: " + str(e))
            return None

    def _add_background_music(self, voice_audio, total_duration, bg_music_path):
        audio_tracks = [voice_audio]
        bg_music = None
        try:
            if bg_music_path and os.path.exists(bg_music_path):
                print("BG music: " + bg_music_path)
                bg_music_raw = AudioFileClip(bg_music_path)

                if bg_music_raw.duration is None or bg_music_raw.duration <= 0:
                    bg_music_raw.close()
                    bg_music = None
                else:
                    bg_music = bg_music_raw
                    if bg_music.duration < total_duration:
                        loop_count = int(total_duration // bg_music.duration) + 2
                        bg_music = concatenate_audioclips([bg_music] * loop_count)
                    if bg_music.duration > total_duration:
                        bg_music = bg_music.subclip(0, total_duration)
                    bg_music = bg_music.volumex(BG_MUSIC_VOLUME)
                    bg_music = (
                        bg_music
                        .fx(afx.audio_fadein, 0.5)
                        .fx(afx.audio_fadeout, 1.0)
                    )
                    audio_tracks.append(bg_music)
            else:
                print("bg_music nahi mila")
        except Exception as e:
            print("BG music error: " + str(e))
            bg_music = None

        return CompositeAudioClip(audio_tracks), bg_music

    def _export(self, video_clip, output_filename):
        output_path = os.path.join(self.output_dir, output_filename)
        video_clip.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="192k",
            fps=24,                       # 30 se 24 kar diya (tez)
            preset="ultrafast",           # medium se ultrafast (bohat tez)
            ffmpeg_params=["-pix_fmt", "yuv420p", "-crf", "23"],  # 22 se 23 (tez)
            temp_audiofile=os.path.join(self.output_dir, "temp_audio.m4a"),
            remove_temp=True,
            threads=4,                    # multi-threading
        )
        return output_path

    def create_multi_scene_short(self, clip_paths, voiceover_paths,
                                  output_filename="final_short.mp4",
                                  bg_music_path="bg_music.mp3",
                                  add_cta=True,
                                  scene_narrations=None,
                                  word_scenes=None,
                                  hook_text=None):
        print("Multi-scene composition START")

        if not clip_paths or not voiceover_paths:
            raise ValueError("clip_paths ya voiceover_paths empty hain")

        count = min(len(clip_paths), len(voiceover_paths))
        print("Scenes: " + str(count))

        font_file = self._get_font_file()
        if not font_file:
            print("FONT NAHI MILA - Captions skip ho jayengi")
        else:
            print("Font ready: " + font_file)

        voice_clips = []
        video_scenes = []
        opened_audio = []
        opened_video = []
        timeline = 0.0
        scene_timings = []

        try:
            for index in range(count):
                clip_path = clip_paths[index]
                voice_path = voiceover_paths[index]

                try:
                    voice = AudioFileClip(voice_path)
                except Exception as e:
                    raise RuntimeError("Scene voice load fail: " + str(e))

                opened_audio.append(voice)

                if voice.duration is None or voice.duration <= 0.1:
                    raise RuntimeError("Scene voice invalid")

                scene_duration = voice.duration + SCENE_GAP
                scene_timings.append((timeline, voice.duration))

                try:
                    scene_video = self._prepare_scene_video(clip_path, scene_duration, punchy=(index == 0))
                except Exception as e:
                    raise RuntimeError("Scene video fail: " + str(e))

                opened_video.append(scene_video)
                video_scenes.append(scene_video)
                voice_clips.append(voice.set_start(timeline))
                timeline += scene_duration
                print("Scene " + str(index + 1) + " ready")

            total_duration = timeline
            print("Total: " + str(round(total_duration, 1)) + "s")

            if not voice_clips:
                raise RuntimeError("No voice clips ready")

            # ---- audio: voice EQ/comp + synthesized SFX + ducked BGM + loudnorm ----
            try:
                master_path = build_final_audio(
                    voiceover_paths[:count],
                    scene_timings,
                    total_duration,
                    bg_music_path,
                    out_dir=os.path.join(self.output_dir, "mix"),
                )
                master_clip = AudioFileClip(master_path)
                opened_audio.append(master_clip)
                real_len = float(master_clip.duration or 0)
                if real_len > 1.0:
                    total_duration = min(total_duration, real_len - 0.15)
                final_audio = master_clip
            except Exception as e:
                print("Pro audio mix failed, using simple mix: " + str(e))
                voice_track = CompositeAudioClip(voice_clips).set_duration(total_duration)
                final_audio, bg_music = self._add_background_music(
                    voice_track, total_duration, bg_music_path
                )
                if bg_music is not None:
                    opened_audio.append(bg_music)

            video = concatenate_videoclips(video_scenes, method="chain")
            video = video.set_audio(final_audio).set_duration(total_duration)

            overlays = []

            # Word-by-word Hinglish captions (replaces the old block captions)
            if word_scenes:
                try:
                    from modules.captions import build_word_caption_clips
                    overlays.extend(build_word_caption_clips(
                        word_scenes, scene_timings, total_duration
                    ))
                except Exception as e:
                    print("Word captions failed, video continues without them: " + str(e))
            elif scene_narrations and font_file:
                for i, (start_t, dur_t) in enumerate(scene_timings):
                    if i >= len(scene_narrations):
                        break
                    narration_text = scene_narrations[i]
                    caption = self._make_caption_overlay(
                        narration_text, start_t, dur_t, font_file
                    )
                    if caption is not None:
                        overlays.append(caption)
                print(str(len(overlays)) + " captions added")

            if hook_text:
                banner_clip = self._make_hook_banner(
                    hook_text, font_file, min(2.6, max(1.5, total_duration * 0.2))
                )
                if banner_clip is not None:
                    overlays.append(banner_clip)
                    print("Hook banner added: " + str(hook_text))

            if add_cta and font_file:
                cta_overlay = self._make_cta_overlay(total_duration, font_file)
                if cta_overlay is not None:
                    overlays.append(cta_overlay)
                    print("CTA overlay added")

            if overlays:
                try:
                    video = CompositeVideoClip(
                        [video] + overlays,
                        size=(TARGET_W, TARGET_H)
                    ).set_duration(total_duration)
                except Exception as e:
                    print("Overlay compose fail: " + str(e))

            output_path = self._export(video, output_filename)

            try:
                video.close()
            except Exception:
                pass

        finally:
            for clip in opened_audio:
                try:
                    clip.close()
                except Exception:
                    pass
            for clip in opened_video:
                try:
                    clip.close()
                except Exception:
                    pass

        print("Video ready: " + output_path)
        return output_path

    def create_short(self, video_path, voiceover_path,
                     output_filename="final_short.mp4",
                     bg_music_path="bg_music.mp3"):
        print("Single-clip composition")

        if not os.path.exists(video_path):
            raise FileNotFoundError("Video nahi mili: " + video_path)
        if not os.path.exists(voiceover_path):
            raise FileNotFoundError("Voiceover nahi mili: " + voiceover_path)

        voiceover_clip = AudioFileClip(voiceover_path)
        final_duration = voiceover_clip.duration
        video_clip = VideoFileClip(video_path)

        if video_clip.duration < final_duration:
            video_clip = video_clip.fx(vfx.loop, duration=final_duration)
        else:
            video_clip = video_clip.subclip(0, final_duration)

        final_audio, bg_music = self._add_background_music(
            voiceover_clip, final_duration, bg_music_path
        )
        video_clip = video_clip.set_audio(final_audio)

        font_file = self._get_font_file()
        if font_file:
            cta_overlay = self._make_cta_overlay(final_duration, font_file)
            if cta_overlay is not None:
                try:
                    video_clip = CompositeVideoClip(
                        [video_clip, cta_overlay],
                        size=(TARGET_W, TARGET_H)
                    ).set_duration(final_duration)
                except Exception as e:
                    print("CTA overlay fail: " + str(e))

        output_path = self._export(video_clip, output_filename)

        video_clip.close()
        voiceover_clip.close()
        if bg_music is not None:
            try:
                bg_music.close()
            except Exception:
                pass

        print("Video ready: " + output_path)
        return output_path
