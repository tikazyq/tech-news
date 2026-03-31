#!/usr/bin/env python3
"""
Audio News Briefing Generator
==============================
Converts a text news briefing into a two-host podcast-style MP3
using Gemini TTS (multi-speaker).

Usage:
    export GEMINI_API_KEY="your-key"
    python3 backend/audio_briefing.py --briefing-text /tmp/briefing.txt
    python3 backend/audio_briefing.py --briefing-text /tmp/briefing.txt --model gemini-2.5-pro-preview-tts
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
SPEAKER_A = "Kore"   # host — introduces stories
SPEAKER_B = "Puck"   # co-host — adds color / "why it matters"

DIALOGUE_PROMPT = f"""You are a podcast script writer. Convert the following tech news briefing into a
natural, engaging two-host dialogue between {SPEAKER_A} and {SPEAKER_B}.

{SPEAKER_A} is the lead host — introduces stories, sets up context.
{SPEAKER_B} is the co-host — reacts naturally, adds "why it matters" insight, asks follow-up questions.

Guidelines:
- Sound like two smart friends discussing the news over coffee, NOT like reading a teleprompter
- Use natural speech patterns: "Oh wow", "Wait, really?", "That's huge", "So basically...", brief laughs
- Hosts should react to each other, interrupt occasionally, build on each other's points
- Each story gets 3-6 conversational turns (not just one speaker per story)
- Include a warm intro and a brief sign-off
- Keep it concise — aim for 3-5 minutes of spoken content total
- Do NOT include any URLs, source attributions, or markdown — this is purely spoken word
- Do NOT include stage directions, sound effects, or parenthetical notes

Output format — each line must be exactly:
{SPEAKER_A}: dialogue text here
{SPEAKER_B}: dialogue text here

Here is the briefing to convert:

"""


# ── Dialogue generation ─────────────────────────────────────────────────────

def generate_dialogue_via_llm(briefing_text: str, client: genai.Client, model: str) -> str:
    """Use Gemini LLM to write a natural two-host dialogue from the briefing."""
    response = client.models.generate_content(
        model=model,
        contents=DIALOGUE_PROMPT + briefing_text,
    )
    dialogue = response.text.strip()

    # Validate that the output has the expected speaker format
    lines = [l for l in dialogue.split('\n') if l.strip()]
    valid_lines = [l for l in lines if l.startswith(f"{SPEAKER_A}:") or l.startswith(f"{SPEAKER_B}:")]
    if len(valid_lines) < 4:
        raise RuntimeError(f"LLM dialogue generation produced only {len(valid_lines)} valid speaker lines")

    # Return only valid speaker lines (strip any preamble the LLM might add)
    return '\n'.join(valid_lines)


def text_to_dialogue_fallback(briefing_text: str) -> str:
    """Simple heuristic fallback if LLM dialogue generation fails."""
    clean = briefing_text
    clean = re.sub(r'\*([^*]+)\*', r'\1', clean)
    clean = re.sub(r'_([^_]+)_', r'\1', clean)
    clean = re.sub(r'\[Read more →\]\([^)]+\)', '', clean)
    clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
    clean = re.sub(r'📡\s*\d+\s*sources?', '', clean)
    clean = re.sub(r'[·🔵🔴🟢🟡🟠]', '', clean)
    clean = re.sub(r'HN\s*⬆\d+', '', clean)
    clean = re.sub(r'@\w+', '', clean)

    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', clean)
    date_str = date_match.group(1) if date_match else datetime.now(timezone.utc).strftime("%B %d, %Y")
    try:
        date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %d, %Y")
    except ValueError:
        pass

    blocks = [b.strip() for b in clean.split('\n\n') if len(b.strip()) > 50]
    stories = blocks[1:7] if len(blocks) > 1 else blocks[:6]

    lines = [
        f"{SPEAKER_A}: Good morning! Here's your tech briefing for {date_str}.",
        f"{SPEAKER_B}: Let's dive right in.",
    ]
    for story in stories:
        sentences = re.split(r'(?<=[.!?])\s+', story.strip())
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        if not sentences:
            continue
        intro_end = min(2, len(sentences))
        lines.append(f"{SPEAKER_A}: {' '.join(sentences[:intro_end])}")
        if len(sentences) > intro_end:
            lines.append(f"{SPEAKER_B}: {' '.join(sentences[intro_end:])}")

    lines.append(f"{SPEAKER_A}: That's your briefing for today. See you tomorrow morning.")
    lines.append(f"{SPEAKER_B}: Have a great day!")
    return '\n'.join(lines)


# ── Gemini TTS ───────────────────────────────────────────────────────────────

def generate_audio_wav(dialogue_script: str, output_path: str, model: str,
                       client: genai.Client = None) -> str:
    """Call Gemini TTS to generate multi-speaker audio. Returns WAV path."""
    if client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY environment variable not set", file=sys.stderr)
            sys.exit(1)
        client = genai.Client(api_key=api_key)

    prompt = f"TTS the following conversation between {SPEAKER_A} and {SPEAKER_B}:\n\n{dialogue_script}"

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

    if not response.candidates or not response.candidates[0].content.parts:
        raise RuntimeError("Gemini TTS returned no audio data — check your API key and model name")

    inline_data = response.candidates[0].content.parts[0].inline_data
    if not inline_data or not inline_data.data:
        raise RuntimeError("Gemini TTS response contained no audio bytes")

    audio_data = inline_data.data
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(24000)
        wf.writeframes(audio_data)

    size_kb = os.path.getsize(output_path) // 1024
    print(f"Generated WAV: {output_path} ({size_kb} KB)", file=sys.stderr)
    return output_path


# ── Audio conversion ─────────────────────────────────────────────────────────

def convert_wav_to_mp3(wav_path: str, mp3_path: str) -> str:
    """Convert WAV to high-quality MP3 via ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-qscale:a", "2", mp3_path],
        check=True, capture_output=True,
    )
    size_kb = os.path.getsize(mp3_path) // 1024
    print(f"Generated MP3: {mp3_path} ({size_kb} KB)", file=sys.stderr)
    return mp3_path


def convert_wav_to_ogg(wav_path: str, ogg_path: str) -> str:
    """Convert WAV to OGG/Opus for Telegram voice messages."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libopus", "-b:a", "128k", ogg_path],
        check=True, capture_output=True,
    )
    size_kb = os.path.getsize(ogg_path) // 1024
    print(f"Generated OGG: {ogg_path} ({size_kb} KB)", file=sys.stderr)
    return ogg_path


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate podcast-style audio news briefing")
    parser.add_argument("--briefing-text", required=True, help="Path to the briefing text file")
    parser.add_argument("--output-dir", default="/tmp/audio_briefing", help="Output directory")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL,
                        help=f"Gemini TTS model (default: {DEFAULT_TTS_MODEL})")
    parser.add_argument("--dialogue-model", default=DEFAULT_DIALOGUE_MODEL,
                        help=f"Gemini model for dialogue script generation (default: {DEFAULT_DIALOGUE_MODEL})")
    parser.add_argument("--format", choices=["mp3", "ogg", "both"], default="mp3",
                        help="Output audio format (default: mp3)")
    args = parser.parse_args()

    # Validate
    if not shutil.which("ffmpeg"):
        print("Error: ffmpeg not found. Install with: apt install ffmpeg", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    briefing_text = Path(args.briefing_text).read_text()
    os.makedirs(args.output_dir, exist_ok=True)

    wav_path = os.path.join(args.output_dir, "briefing.wav")
    mp3_path = os.path.join(args.output_dir, "briefing.mp3")
    ogg_path = os.path.join(args.output_dir, "briefing.ogg")

    # Step 1: Generate natural dialogue via LLM (with heuristic fallback)
    print(f"Generating dialogue script via {args.dialogue_model}...", file=sys.stderr)
    try:
        dialogue = generate_dialogue_via_llm(briefing_text, client, args.dialogue_model)
        print("LLM dialogue generation succeeded", file=sys.stderr)
    except Exception as e:
        print(f"LLM dialogue generation failed ({e}), using heuristic fallback", file=sys.stderr)
        dialogue = text_to_dialogue_fallback(briefing_text)

    dialogue_path = os.path.join(args.output_dir, "dialogue.txt")
    Path(dialogue_path).write_text(dialogue)
    print(f"Dialogue saved to {dialogue_path}", file=sys.stderr)

    # Step 2: Generate audio via Gemini TTS
    print(f"Generating audio via {args.tts_model}...", file=sys.stderr)
    generate_audio_wav(dialogue, wav_path, args.tts_model, client)

    # Step 3: Convert to output format(s)
    result = {}
    if args.format in ("mp3", "both"):
        print("Converting to MP3...", file=sys.stderr)
        convert_wav_to_mp3(wav_path, mp3_path)
        result["mp3"] = mp3_path
    if args.format in ("ogg", "both"):
        print("Converting to OGG...", file=sys.stderr)
        convert_wav_to_ogg(wav_path, ogg_path)
        result["ogg"] = ogg_path

    result["dialogue"] = dialogue_path
    print(json.dumps(result))


if __name__ == "__main__":
    main()
