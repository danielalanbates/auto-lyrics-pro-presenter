# Auto Lyrics Pro Presenter

Real-time lyric recognition and auto-advance for ProPresenter. MIT licensed.

**Status: working & verified end-to-end** against ProPresenter 21.4 on Apple
Silicon (2026-06-28). In a TTS-driven live test it followed an 8-slide song and
landed **7/7** slide advances, firing each one **predictively** (avg 1.7 words
before the line ended). See `DESIGN.md` for the full verification log. Not yet
tested on real-room sung audio with a band — that's the remaining unknown.

## What it does

1. **Displays the correct slide** — listens to the singing and follows along.
2. **Predictive auto-advance** — fires the *next* slide as the singer reaches the
   tail of the current line (default: as the last word begins), so the next slide
   is already up before they finish — no lag.

## How it works (two provably-correct cores around noisy STT)

- **`matcher.py` — WHICH slide.** We already know the song's slide text (the
  "answer key", pulled live from ProPresenter), so the problem is just "which of
  these ~15 known lines does this noisy transcript match?" — fuzzy content-word
  recall + a position prior (next > current > …) + anti-jitter hysteresis.
- **`slide_tracker.py` — WHEN to advance.** Aligns the live transcript to the
  current slide's words and fires once, as the line nears its end.
- **STT** is faster-whisper (MIT), primed with the song's own lyrics
  (`initial_prompt`) so today's model is accurate *for this constrained task*.
- **`propresenter_bridge.py`** drives ProPresenter's built-in HTTP API (stdlib
  only — no third-party HTTP client). Answer key + live index + triggering all
  verified live.

## Architecture

```
 mic/feed ─▶ AudioCapture ─▶ [vocal isolation] ─▶ faster-whisper (lyric-primed)
                                                          │ transcript
                                                          ▼
                        ┌──────────────── LyricEngine.process() ───────────────┐
                        │  SlideProgressTracker (WHEN: predictive advance)      │
                        │  best_match (WHICH: position-aware correction)        │
                        └───────────────────────┬──────────────────────────────┘
                                                 │ Decision(advance|jump, index)
 ProPresenter ◀── /v1/presentation/active/{i}/trigger ◀── auto-fire / suggest ──┘
      │  /v1/presentation/active     → answer key (full slide text)   [verified]
      └─ /v1/presentation/slide_index → live position (re-sync)        [verified]
```

### Components

| Module | Purpose |
|--------|---------|
| `audio_capture` | Capture live audio input from macOS (mic, interface, or system audio) |
| `vocal_isolation` | Separate vocals from music/instruments using AI source separation |
| `lyric_engine` | Transcribe vocals and match against loaded song lyrics using Whisper |
| `propresenter_bridge` | Send slide advance commands to ProPresenter via OSC or network protocol |

### Key Challenges

1. **Latency** — Must recognize lyrics fast enough to advance before the line ends
2. **Accuracy** — Live vocals with music bleed are hard to transcribe
3. **Vocal isolation** — Need lightweight source separation that runs in real-time on M1 Pro
4. **ProPresenter integration** — OSC is supported; HTTP API may need authentication

## Tech Stack (all permissive / MIT-compatible)

- **Python 3.11+**
- **faster-whisper** (MIT) — lyric transcription (CPU/int8, no PyTorch)
- **SoundDevice / SoundFile** — audio capture
- **stdlib urllib** — ProPresenter HTTP API (no third-party HTTP client)
- Optional: **Demucs** (MIT) for AI vocal isolation; default is a scipy band-pass

## Getting Started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# In ProPresenter: Settings → Network → Enable Network (the port is auto-detected).
# Open a song and make a slide live.

python -m pytest tests/ -q       # 22 unit tests (matcher + predictive tracker)
python tools/make_test_song.py   # create the 8-slide test presentation in PP
python tools/e2e_live.py         # live end-to-end test against ProPresenter
python -m src.main               # run for real off the mic  (--song NAME optional)
```

By default it runs in **suggest** mode; set `matching.auto_fire: true` (config) or
`MatchingConfig.auto_fire` to let it drive slides unattended.

## Project Structure

```
auto-lyrics-pro-presenter/
├── src/
│   ├── __init__.py
│   ├── audio_capture.py      # Mic/audio input handling
│   ├── vocal_isolation.py    # Separate vocals from instruments
│   ├── lyric_engine.py       # transcribe + decide (combines the two cores)
│   ├── matcher.py            # WHICH slide — candidate-set, position-aware
│   ├── slide_tracker.py      # WHEN to advance — predictive, end-of-line
│   ├── propresenter_bridge.py # ProPresenter HTTP API (stdlib urllib)
│   ├── song_loader.py        # Load and manage song lyric databases
│   └── config.py             # Configuration management
├── tests/
│   ├── test_matcher.py       # 11 tests — the WHICH core
│   └── test_slide_tracker.py # 11 tests — the WHEN core
├── tools/
│   ├── e2e_live.py           # live end-to-end test against ProPresenter
│   └── make_test_song.py     # create test slides in PP programmatically
├── requirements.txt
├── LICENSE                   # MIT
└── README.md
```

## Status

✅ Working & verified end-to-end on clean (TTS) audio + live ProPresenter.
🚧 Next: real-room sung audio with a band, and streaming partials for lower latency.
