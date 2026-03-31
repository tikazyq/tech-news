# Audio & Video News Briefing — Feasibility Analysis

## Current System Overview

The tech-news system collects stories from 5 sources (HN, Reddit, Lobsters, Google News, RSS feeds), scores/deduplicates them, and Claude edits them into a ~4000-character Telegram markdown briefing (5-6 stories + honorable mentions). It runs as a daily scheduled task.

**Typical briefing:** ~600-800 words, ~3000-4000 characters of spoken content once markdown formatting is stripped.

---

## Option 1: Audio (TTS) News Briefing

### Approach A: Free / Near-Free TTS

| Tool | Cost | Quality | Notes |
|------|------|---------|-------|
| **edge-tts** (Python package) | **Free** | Good (Microsoft Azure voices) | Uses Microsoft's free Edge browser TTS endpoint. 300+ voices, multiple languages. No API key needed. Best free option. |
| **Piper TTS** | **Free** (open-source) | Good | Fast offline TTS. ONNX models, runs on CPU. Good for self-hosted. |
| **gTTS** (Google Translate TTS) | **Free** | Mediocre | Robotic quality, limited control. Not recommended for daily listening. |

**Recommendation: `edge-tts`** — zero cost, surprisingly natural voices, trivial to integrate.

```python
# Example: ~5 lines of code to generate audio
import edge_tts, asyncio
async def generate():
    tts = edge_tts.Communicate("Your briefing text...", "en-US-AndrewMultilingualNeural")
    await tts.save("briefing.mp3")
asyncio.run(generate())
```

**Estimated audio length:** 3-5 minutes for a typical briefing.

### Approach B: Paid Cloud TTS (Higher Quality)

| Service | Cost per briefing (~4K chars) | Quality | Key Feature |
|---------|-------------------------------|---------|-------------|
| **OpenAI TTS** | ~$0.03-0.06 | Excellent | `tts-1` ($15/1M chars) or `tts-1-hd` ($30/1M chars). 6 natural voices. |
| **ElevenLabs** | ~$0.05-0.10 | Excellent | Most natural. Voice cloning. Free tier: 10K chars/month (~2-3 briefings). |
| **Google Cloud TTS** | ~$0.06-0.08 | Very Good | WaveNet/Neural2 voices. $4-16/1M chars depending on tier. |
| **Amazon Polly** | ~$0.02 | Good | Neural voices at $4/1M chars. SSML support. |
| **Azure Cognitive TTS** | ~$0.06 | Very Good | 500K chars/month free tier. Same voices as edge-tts but with more control. |

**Monthly cost at daily frequency:** $0.60-$3.00/month for paid options.

### Approach C: Podcast-Style (Multiple Voices, Music)

Tools like **Google NotebookLM** have popularized the "AI podcast" format with two hosts discussing content. Replicating this requires:

1. **Script generation** — Claude already writes the briefing; a second prompt could restructure it as a two-host dialogue
2. **Multi-voice synthesis** — ElevenLabs or OpenAI TTS with different voices for each host
3. **Music bed** — royalty-free intro/outro jingle mixed in via `pydub` or `ffmpeg`

**Complexity:** Medium. Adds ~20 lines of Python + a restructured prompt. Cost doubles (two voices).

### Audio Delivery Options

| Channel | How | Complexity |
|---------|-----|------------|
| **Telegram voice message** | Upload .ogg via Bot API `sendVoice` | Very Low |
| **Telegram audio file** | Upload .mp3 via Bot API `sendAudio` | Very Low |
| **Podcast RSS feed** | Host MP3 + generate RSS XML, publish to podcast apps | Medium |
| **Web player** | Host on existing Vue frontend with `<audio>` tag | Low |

**Best fit:** Send as Telegram voice/audio alongside the text briefing. Two API calls instead of one.

### Audio Feasibility Verdict

| Dimension | Rating |
|-----------|--------|
| **Technical complexity** | Very Low (10-30 lines of Python) |
| **Cost** | Free (edge-tts) to ~$1-3/month (cloud APIs) |
| **Quality** | Good to Excellent |
| **Integration effort** | 1-2 hours |
| **Daily runtime overhead** | 5-15 seconds |

**Verdict: Highly feasible. Recommended starting point.**

---

## Option 2: Video News Briefing

### Approach A: Simple Slideshow + TTS Narration

Generate a video with text/image slides narrated by TTS audio. Minimal tooling:

1. **Generate TTS audio** (same as above)
2. **Create title cards** — use `Pillow` (Python) to render headline text on branded backgrounds
3. **Assemble video** — use `ffmpeg` or `moviepy` to combine slides + audio into MP4

```
[Intro card: "Tech Briefing — Mar 31, 2026"]
    ↓ (3 sec)
[Story 1 headline card + narration]
    ↓ (30-45 sec)
[Story 2 headline card + narration]
    ...
[Outro card]
```

| Component | Tool | Cost |
|-----------|------|------|
| TTS | edge-tts | Free |
| Image generation | Pillow | Free |
| Video assembly | ffmpeg / moviepy | Free |

**Total cost: Free.** Output: 3-5 minute MP4 video.
**Complexity:** Medium (~100-150 lines of Python).
**Quality:** Functional but basic — think "cable news ticker" aesthetic.

### Approach B: AI Avatar Presenter

An AI-generated human presenter reads the news. Most realistic but expensive:

| Service | Cost per minute | Cost per briefing (4 min) | Monthly (daily) |
|---------|----------------|--------------------------|-----------------|
| **HeyGen** | ~$0.50-1.00/min | ~$2-4 | ~$60-120 |
| **Synthesia** | ~$1-2/min | ~$4-8 | ~$120-240 |
| **D-ID** | ~$0.10-0.50/min | ~$0.40-2.00 | ~$12-60 |

**Pros:** Professional-looking, AI presenter with lip sync.
**Cons:** Expensive at daily frequency, API latency (minutes to render), potential uncanny valley.

### Approach C: Screen-Recording Style / Motion Graphics

Generate animated text/graphics without a presenter:

1. **Remotion** (React-based) — programmatic video from React components. Great for data visualizations and text animations. Requires Node.js.
2. **FFmpeg filters** — text overlays, fade transitions, scrolling tickers. Free but complex command-line syntax.
3. **MoviePy** — Python library wrapping FFmpeg. Easier API for compositing text, images, and audio.

**Complexity:** Medium-High (~200-400 lines of code for polished output).
**Cost:** Free (all open-source).
**Quality:** Can look quite professional with good design templates.

### Video Delivery Options

| Channel | How | Limits |
|---------|-----|--------|
| **Telegram video** | Bot API `sendVideo` (MP4) | 50MB max, 2GB with upload API |
| **YouTube** | YouTube Data API auto-upload | Requires channel, API setup |
| **Web player** | Host on Vue frontend | Storage/bandwidth costs |

### Video Feasibility Verdict

| Dimension | Slideshow + TTS | AI Avatar | Motion Graphics |
|-----------|----------------|-----------|-----------------|
| **Complexity** | Medium | Low (API call) | High |
| **Cost** | Free | $30-120/mo | Free |
| **Quality** | Basic | High | Medium-High |
| **Runtime** | 30-60 sec | 2-10 min | 1-3 min |
| **Integration** | 2-4 hours | 1-2 hours | 1-2 days |

**Verdict: Feasible but with tradeoffs.** Slideshow+TTS is the practical starting point. AI avatars look great but costs add up. Motion graphics require significant upfront design work.

---

## Recommended Implementation Path

### Phase 1: Audio Briefing (Effort: Small, Impact: High)

1. Add `edge-tts` to the scheduled prompt workflow
2. After Claude writes the text briefing, generate an audio version
3. Send both text + audio to Telegram (text as message, audio as voice note)
4. **No additional cost. ~30 lines of Python. Can ship in a day.**

### Phase 2: Simple Video (Effort: Medium, Impact: Medium)

1. Use Pillow to generate headline cards with branded backgrounds
2. Use edge-tts for narration
3. Use ffmpeg/moviepy to combine into MP4
4. Send via Telegram `sendVideo`
5. **Free. ~150 lines of Python. 2-3 days of work.**

### Phase 3 (Optional): Enhanced Video or Podcast

Pick one based on goals:
- **Podcast format** — two-voice dialogue via ElevenLabs (~$3-5/mo), publish RSS feed
- **AI Avatar** — D-ID or HeyGen for presenter video (~$30-60/mo)
- **Motion graphics** — Remotion for animated news cards (free, ~1 week of design work)

---

## Key Risks & Considerations

| Risk | Mitigation |
|------|------------|
| **edge-tts is an unofficial endpoint** — Microsoft could block it | Fall back to Azure free tier (500K chars/mo) or OpenAI TTS ($1/mo) |
| **Video generation adds runtime** — currently the briefing is fast | Generate video async, send text first, video follows in 30-60 sec |
| **Audio quality in Telegram voice notes** — Telegram re-encodes to .ogg | Use `sendAudio` instead of `sendVoice` to preserve MP3 quality |
| **ffmpeg/moviepy dependency** — adds system packages | `apt install ffmpeg` in setup, or use a container |
| **Content too long for comfortable listening** — 5 min may feel long | Write a shorter "audio script" variant (~2-3 min) alongside the full text |

---

## Summary

| Option | Feasibility | Cost | Effort | Recommended? |
|--------|------------|------|--------|-------------|
| **Audio (edge-tts)** | Very High | Free | 1 day | Yes — start here |
| **Audio (cloud TTS)** | Very High | $1-3/mo | 1 day | Optional upgrade |
| **Video (slideshow)** | High | Free | 2-3 days | Yes — Phase 2 |
| **Video (AI avatar)** | Medium | $30-120/mo | 1-2 days | Only if budget allows |
| **Video (motion graphics)** | Medium | Free | 1 week | If polished look needed |
| **Podcast (two voices)** | High | $3-5/mo | 2 days | Fun alternative |

**Bottom line:** Audio is a quick win — `edge-tts` makes it essentially free and trivial to add. Video is feasible but requires more effort; start with a simple slideshow approach and iterate. AI avatar video is the most impressive but the ongoing cost needs to justify the audience size.
