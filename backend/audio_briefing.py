#!/usr/bin/env python3
"""
Audio News Briefing Generator
==============================
Generates a podcast-style two-host audio briefing from news digest data.

Pipeline:
  1. Gemini LLM reads the full article texts and writes a natural dialogue
  2. Gemini TTS synthesizes the dialogue with two distinct voices

Feed it the raw digest JSON (with full article_text) for the richest output.

Usage:
    export GEMINI_API_KEY="your-key"

    # From digest JSON (recommended — richest input)
    python3 backend/news_digest.py 2>/dev/null | \
        python3 backend/audio_briefing.py --output-dir /tmp/audio_briefing

    # From a saved digest file
    python3 backend/audio_briefing.py --input /tmp/digest.json

    # From the text briefing (less rich, but works)
    python3 backend/audio_briefing.py --briefing-text /tmp/briefing.txt
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

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_TTS_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_DIALOGUE_MODEL = "gemini-2.5-flash"
SPEAKER_A = "Kore"
SPEAKER_B = "Puck"

DIALOGUE_SYSTEM_PROMPT = f"""You are a podcast script writer for a daily tech news audio briefing.

Write a natural, engaging conversation between two hosts:
- {SPEAKER_A}: Lead host. Introduces stories, sets context, drives the conversation.
- {SPEAKER_B}: Co-host. Reacts naturally, explains why things matter, asks sharp follow-up questions.

Style guidelines:
- Sound like two smart friends discussing tech news over coffee
- Use natural speech: "Oh wow", "Wait, seriously?", "That's a big deal", "So basically..."
- Hosts react to each other, occasionally interrupt, build on each other's points
- Each story gets 4-8 conversational turns — not just one speaker per story
- Include a warm intro ("Good morning!") and a brief sign-off
- Aim for 3-5 minutes of spoken content total (roughly 500-800 words)
- Prioritize the most important stories — skip minor ones or mention them briefly
- Explain technical concepts simply — the audience is smart but busy
- Do NOT include URLs, markdown, or stage directions
- Do NOT use asterisks, brackets, or any formatting

Output format — ONLY lines in this exact format, nothing else:
{SPEAKER_A}: dialogue text here
{SPEAKER_B}: dialogue text here"""


# ── Dialogue generation ──────────────────────────────────────────────────────

def build_dialogue_input_from_digest(digest: dict) -> str:
    """Build rich LLM input from the full news digest JSON."""
    date = digest.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    stories = digest.get("stories", [])

    parts = [f"Today's date: {date}\n"]
    for i, story in enumerate(stories, 1):
        title = story.get("title", "")
        source = story.get("source", "")
        article_text = story.get("article_text", "").strip()
        all_sources = story.get("all_sources", [])
        source_count = len(all_sources)
        comments = story.get("comments", 0)

        source_line = source
        if source_count > 1:
            names = ", ".join(s["source"] for s in all_sources)
            source_line = f"{names} ({source_count} sources)"

        engagement = ""
        if comments > 0:
            engagement = f" | {comments} comments"

        parts.append(f"Story {i}: {title}")
        parts.append(f"Source: {source_line}{engagement}")
        if article_text:
            parts.append(f"Article:\n{article_text}")
        parts.append("")

    return '\n'.join(parts)


def build_dialogue_input_from_text(briefing_text: str) -> str:
    """Build LLM input from a plain text briefing (less rich fallback)."""
    return f"Here is today's tech news briefing:\n\n{briefing_text}"


def generate_dialogue(input_text: str, client: genai.Client, model: str) -> str:
    """Use Gemini to write a natural two-host dialogue."""
    response = client.models.generate_content(
        model=model,
        contents=input_text,
        config=types.GenerateContentConfig(
            system_instruction=DIALOGUE_SYSTEM_PROMPT,
        ),
    )
    dialogue = response.text.strip()

    # Keep only valid speaker lines
    valid_lines = []
    for line in dialogue.split('\n'):
        line = line.strip()
        if line.startswith(f"{SPEAKER_A}:") or line.startswith(f"{SPEAKER_B}:"):
            valid_lines.append(line)

    if len(valid_lines) < 6:
        raise RuntimeError(f"Dialogue generation produced only {len(valid_lines)} lines (expected 6+)")

    return '\n'.join(valid_lines)


# ── Gemini TTS ───────────────────────────────────────────────────────────────

def generate_audio_wav(dialogue: str, output_path: str, model: str,
                       client: genai.Client) -> str:
    """Call Gemini TTS to generate multi-speaker audio."""
    prompt = f"TTS the following conversation between {SPEAKER_A} and {SPEAKER_B}:\n\n{dialogue}"

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=[
                        types.SpeakerVoiceConfig(
                            speaker=SPEAKER_A,
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=SPEAKER_A)
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker=SPEAKER_B,
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=SPEAKER_B)
                            ),
                        ),
                    ]
                )
            ),
        ),
    )

    if not response.candidates or not response.candidates[0].content.parts:
        raise RuntimeError("Gemini TTS returned no audio data")

    inline_data = response.candidates[0].content.parts[0].inline_data
    if not inline_data or not inline_data.data:
        raise RuntimeError("Gemini TTS response contained no audio bytes")

    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(inline_data.data)

    print(f"Generated WAV: {output_path} ({os.path.getsize(output_path) // 1024} KB)", file=sys.stderr)
    return output_path


# ── Audio conversion ─────────────────────────────────────────────────────────

def convert_wav_to_mp3(wav_path: str, mp3_path: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
        check=True, capture_output=True,
    )
    print(f"Generated MP3: {mp3_path} ({os.path.getsize(mp3_path) // 1024} KB)", file=sys.stderr)
    return mp3_path


def convert_wav_to_ogg(wav_path: str, ogg_path: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libopus", "-b:a", "128k", ogg_path],
        check=True, capture_output=True,
    )
    print(f"Generated OGG: {ogg_path} ({os.path.getsize(ogg_path) // 1024} KB)", file=sys.stderr)
    return ogg_path


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate podcast-style audio news briefing")
    parser.add_argument("--input", help="Path to digest JSON file (or reads from stdin)")
    parser.add_argument("--briefing-text", help="Path to text briefing (fallback if no JSON)")
    parser.add_argument("--output-dir", default="/tmp/audio_briefing", help="Output directory")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL,
                        help=f"Gemini TTS model (default: {DEFAULT_TTS_MODEL})")
    parser.add_argument("--dialogue-model", default=DEFAULT_DIALOGUE_MODEL,
                        help=f"Gemini model for dialogue writing (default: {DEFAULT_DIALOGUE_MODEL})")
    parser.add_argument("--format", choices=["mp3", "ogg", "both"], default="mp3",
                        help="Output audio format (default: mp3)")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found. Install with: apt install ffmpeg", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    os.makedirs(args.output_dir, exist_ok=True)

    # Build dialogue input — prefer digest JSON for richest content
    if args.input:
        with open(args.input) as f:
            digest = json.load(f)
        dialogue_input = build_dialogue_input_from_digest(digest)
    elif args.briefing_text:
        text = Path(args.briefing_text).read_text()
        dialogue_input = build_dialogue_input_from_text(text)
    elif not sys.stdin.isatty():
        digest = json.load(sys.stdin)
        dialogue_input = build_dialogue_input_from_digest(digest)
    else:
        print("Error: provide --input, --briefing-text, or pipe digest JSON to stdin", file=sys.stderr)
        sys.exit(1)

    wav_path = os.path.join(args.output_dir, "briefing.wav")
    mp3_path = os.path.join(args.output_dir, "briefing.mp3")
    ogg_path = os.path.join(args.output_dir, "briefing.ogg")

    # Step 1: Generate natural dialogue via LLM
    print(f"Generating dialogue via {args.dialogue_model}...", file=sys.stderr)
    dialogue = generate_dialogue(dialogue_input, client, args.dialogue_model)
    dialogue_path = os.path.join(args.output_dir, "dialogue.txt")
    Path(dialogue_path).write_text(dialogue)
    print(f"Dialogue saved to {dialogue_path}", file=sys.stderr)

    # Step 2: Synthesize audio via Gemini TTS
    print(f"Synthesizing audio via {args.tts_model}...", file=sys.stderr)
    generate_audio_wav(dialogue, wav_path, args.tts_model, client)

    # Step 3: Convert to output format
    result = {"dialogue": dialogue_path}
    if args.format in ("mp3", "both"):
        convert_wav_to_mp3(wav_path, mp3_path)
        result["mp3"] = mp3_path
    if args.format in ("ogg", "both"):
        convert_wav_to_ogg(wav_path, ogg_path)
        result["ogg"] = ogg_path

    print(json.dumps(result))


if __name__ == "__main__":
    main()
