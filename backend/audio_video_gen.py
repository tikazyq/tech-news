#!/usr/bin/env python3
"""
Audio & Video Briefing Generator
=================================
Converts a text news briefing into:
  1. A two-host podcast-style MP3 (via Gemini 2.5 Flash TTS)
  2. A headline-card slideshow MP4 video with the audio narration

Usage:
    export GEMINI_API_KEY="your-key"
    python3 backend/audio_video_gen.py \
        --briefing-text /tmp/briefing.txt \
        --headlines /tmp/headlines.json \
        --output-dir /tmp/av_output
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

# ── Constants ────────────────────────────────────────────────────────────────

CARD_WIDTH = 1280
CARD_HEIGHT = 720
BG_COLOR = (10, 14, 39)  # deep navy #0a0e27
ACCENT_COLORS = [
    (66, 133, 244),   # blue
    (234, 67, 53),    # red
    (52, 168, 83),    # green
    (251, 188, 4),    # yellow
    (255, 109, 0),    # orange
    (171, 71, 188),   # purple
]
TEXT_COLOR = (255, 255, 255)
SUBTEXT_COLOR = (180, 180, 190)
INTRO_DURATION = 4.0
OUTRO_DURATION = 3.0

SPEAKER_A = "Kore"   # host — introduces stories
SPEAKER_B = "Puck"   # co-host — adds color / "why it matters"


# ── Font loading ─────────────────────────────────────────────────────────────

def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    print("Warning: No TrueType font found, using default bitmap font", file=sys.stderr)
    return ImageFont.load_default()


# ── Dialogue generation ─────────────────────────────────────────────────────

def text_to_dialogue(briefing_text: str) -> str:
    """Split a plain-text briefing into a two-host dialogue script."""
    # Split on story boundaries (colored circle emoji or double newlines between blocks)
    story_pattern = re.compile(r'[🔵🔴🟢🟡🟠]\s*')
    # Remove markdown artifacts
    clean = briefing_text
    clean = re.sub(r'\*([^*]+)\*', r'\1', clean)       # *bold*
    clean = re.sub(r'_([^_]+)_', r'\1', clean)         # _italic_
    clean = re.sub(r'\[Read more →\]\([^)]+\)', '', clean)
    clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)  # [text](url)
    clean = re.sub(r'📡\s*\d+\s*sources?', '', clean)
    clean = re.sub(r'·', '', clean)
    clean = re.sub(r'HN\s*⬆\d+', '', clean)

    # Extract date from briefing if present
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', clean)
    date_str = date_match.group(1) if date_match else datetime.now(timezone.utc).strftime("%B %d, %Y")
    try:
        date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        pass

    # Split into story blocks
    parts = story_pattern.split(clean)
    # First part is usually the header, rest are stories
    stories = []
    also_reading = []
    in_also = False
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if 'also worth reading' in part.lower():
            in_also = True
            continue
        if in_also:
            also_reading.append(part)
        elif len(part) > 30:  # skip very short fragments
            stories.append(part)

    # If emoji splitting didn't work well, try double-newline splitting
    if len(stories) < 2:
        blocks = [b.strip() for b in clean.split('\n\n') if len(b.strip()) > 50]
        stories = blocks[1:] if len(blocks) > 1 else blocks  # skip header

    # Build dialogue
    lines = []
    lines.append(f"{SPEAKER_A}: Good morning! Here's your tech briefing for {date_str}. We've got some interesting stories today.")
    lines.append(f"{SPEAKER_B}: Let's dive right in.")

    for i, story in enumerate(stories[:6]):  # cap at 6 stories
        sentences = re.split(r'(?<=[.!?])\s+', story.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if not sentences:
            continue

        # Speaker A introduces with the first sentence(s)
        intro_end = min(2, len(sentences))
        intro = ' '.join(sentences[:intro_end])
        lines.append(f"{SPEAKER_A}: {intro}")

        # Speaker B adds the rest
        if len(sentences) > intro_end:
            detail = ' '.join(sentences[intro_end:])
            lines.append(f"{SPEAKER_B}: {detail}")
        elif i < len(stories) - 1:
            lines.append(f"{SPEAKER_B}: Interesting. What's next?")

    # Outro
    lines.append(f"{SPEAKER_A}: And that's your briefing for today. Stay informed, and we'll see you tomorrow morning.")
    lines.append(f"{SPEAKER_B}: Have a great day!")

    return '\n'.join(lines)


# ── Gemini TTS ───────────────────────────────────────────────────────────────

def generate_audio_wav(dialogue_script: str, output_path: str) -> str:
    """Call Gemini 2.5 Flash TTS to generate multi-speaker audio."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    prompt = f"TTS the following conversation between {SPEAKER_A} and {SPEAKER_B}:\n\n{dialogue_script}"

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-tts",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=[
                        types.SpeakerVoiceConfig(
                            speaker=SPEAKER_A,
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=SPEAKER_A)
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker=SPEAKER_B,
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=SPEAKER_B)
                            ),
                        ),
                    ]
                )
            ),
        ),
    )

    audio_data = response.candidates[0].content.parts[0].inline_data.data
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(24000)
        wf.writeframes(audio_data)

    print(f"Generated WAV: {output_path} ({os.path.getsize(output_path)} bytes)", file=sys.stderr)
    return output_path


# ── Audio conversion ─────────────────────────────────────────────────────────

def convert_wav_to_mp3(wav_path: str, mp3_path: str) -> str:
    """Convert WAV to high-quality MP3 via ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
        check=True, capture_output=True,
    )
    print(f"Generated MP3: {mp3_path} ({os.path.getsize(mp3_path)} bytes)", file=sys.stderr)
    return mp3_path


# ── Headline card generation ────────────────────────────────────────────────

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def generate_headline_cards(headlines: list[str], output_dir: str, date_str: str) -> list[str]:
    """Create PNG headline cards for the video slideshow."""
    paths = []
    title_font = _load_font(48)
    subtitle_font = _load_font(28)
    small_font = _load_font(22)

    # Intro card
    intro_path = os.path.join(output_dir, "card_000_intro.png")
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    # Accent line
    draw.rectangle([0, 340, CARD_WIDTH, 346], fill=ACCENT_COLORS[0])
    _draw_centered(draw, "Tech Briefing", title_font, CARD_WIDTH, 260)
    _draw_centered(draw, date_str, subtitle_font, CARD_WIDTH, 370, color=SUBTEXT_COLOR)
    img.save(intro_path)
    paths.append(intro_path)

    # Story cards
    for i, headline in enumerate(headlines[:6]):
        card_path = os.path.join(output_dir, f"card_{i+1:03d}.png")
        img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
        draw = ImageDraw.Draw(img)

        color = ACCENT_COLORS[i % len(ACCENT_COLORS)]
        # Left accent bar
        draw.rectangle([0, 0, 8, CARD_HEIGHT], fill=color)
        # Story number
        draw.text((40, 40), f"Story {i+1}", font=small_font, fill=color)

        # Headline text (wrapped)
        lines = _wrap_text(draw, headline, title_font, CARD_WIDTH - 120)
        y = (CARD_HEIGHT - len(lines) * 64) // 2
        for line in lines:
            draw.text((60, y), line, font=title_font, fill=TEXT_COLOR)
            y += 64

        img.save(card_path)
        paths.append(card_path)

    # Outro card
    outro_path = os.path.join(output_dir, "card_999_outro.png")
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 340, CARD_WIDTH, 346], fill=ACCENT_COLORS[2])
    _draw_centered(draw, "That's all for today.", subtitle_font, CARD_WIDTH, 290)
    _draw_centered(draw, "See you tomorrow morning!", small_font, CARD_WIDTH, 370, color=SUBTEXT_COLOR)
    img.save(outro_path)
    paths.append(outro_path)

    print(f"Generated {len(paths)} headline cards", file=sys.stderr)
    return paths


def _draw_centered(draw: ImageDraw.ImageDraw, text: str, font, canvas_width: int, y: int,
                   color=TEXT_COLOR):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x = (canvas_width - tw) // 2
    draw.text((x, y), text, font=font, fill=color)


# ── Video assembly ───────────────────────────────────────────────────────────

def get_audio_duration(audio_path: str) -> float:
    """Get duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def assemble_video(image_paths: list[str], audio_path: str, output_path: str) -> str:
    """Combine headline card images + audio into an MP4 video."""
    duration = get_audio_duration(audio_path)
    n_cards = len(image_paths)

    # Reserve fixed time for intro/outro, split rest evenly
    story_cards = n_cards - 2  # exclude intro and outro
    reserved = INTRO_DURATION + OUTRO_DURATION
    story_duration = max(2.0, (duration - reserved) / max(1, story_cards))

    # Build concat file
    output_dir = os.path.dirname(output_path)
    concat_path = os.path.join(output_dir, "concat.txt")
    with open(concat_path, "w") as f:
        for i, img_path in enumerate(image_paths):
            f.write(f"file '{img_path}'\n")
            if i == 0:
                f.write(f"duration {INTRO_DURATION}\n")
            elif i == len(image_paths) - 1:
                f.write(f"duration {OUTRO_DURATION}\n")
            else:
                f.write(f"duration {story_duration:.1f}\n")
        # ffmpeg concat requires the last file repeated without duration
        f.write(f"file '{image_paths[-1]}'\n")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-i", audio_path,
            "-vf", "scale=1280:720,format=yuv420p",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ],
        check=True, capture_output=True,
    )
    print(f"Generated video: {output_path} ({os.path.getsize(output_path)} bytes)", file=sys.stderr)
    return output_path


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate audio & video news briefing")
    parser.add_argument("--briefing-text", required=True, help="Path to plain-text briefing file")
    parser.add_argument("--headlines", required=True, help="Path to JSON array of headline strings")
    parser.add_argument("--output-dir", required=True, help="Directory for output files")
    args = parser.parse_args()

    # Validate environment
    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found. Install with: apt install ffmpeg", file=sys.stderr)
        sys.exit(1)

    # Read inputs
    briefing_text = Path(args.briefing_text).read_text()
    headlines = json.loads(Path(args.headlines).read_text())

    # Create output directory
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Extract date
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', briefing_text)
    date_str = date_match.group(1) if date_match else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    wav_path = os.path.join(output_dir, "briefing.wav")
    mp3_path = os.path.join(output_dir, "briefing.mp3")
    mp4_path = os.path.join(output_dir, "briefing.mp4")

    # Step 1: Convert to dialogue
    print("Converting briefing to dialogue script...", file=sys.stderr)
    dialogue = text_to_dialogue(briefing_text)
    dialogue_path = os.path.join(output_dir, "dialogue.txt")
    Path(dialogue_path).write_text(dialogue)
    print(f"Dialogue script saved to {dialogue_path}", file=sys.stderr)

    # Step 2: Generate audio via Gemini TTS
    print("Generating audio via Gemini 2.5 Flash TTS...", file=sys.stderr)
    generate_audio_wav(dialogue, wav_path)

    # Step 3: Convert to MP3
    print("Converting WAV to MP3...", file=sys.stderr)
    convert_wav_to_mp3(wav_path, mp3_path)

    # Step 4: Generate headline cards
    print("Generating headline cards...", file=sys.stderr)
    display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y") if "-" in date_str else date_str
    card_paths = generate_headline_cards(headlines, output_dir, display_date)

    # Step 5: Assemble video
    print("Assembling video...", file=sys.stderr)
    assemble_video(card_paths, mp3_path, mp4_path)

    # Output paths as JSON for the caller
    result = {"audio": mp3_path, "video": mp4_path}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
