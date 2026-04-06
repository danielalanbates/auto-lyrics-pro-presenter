# Auto Lyrics Pro Presenter

Real-time lyric recognition and auto-advance for ProPresenter.

## Goal

Listen to live singing/audio, recognize lyrics in real-time using AI, and automatically advance ProPresenter slides to match what's being sung.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│  Audio Capture  │────▶│  Vocal Isolation │────▶│  Lyric Engine   │────▶│  ProPresenter│
│  (macOS mic)    │     │  (demucs/spleeter)│     │  (Whisper AI)   │     │  (OSC/HTTP)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘     └──────────────┘
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

## Tech Stack

- **Python 3.11+**
- **Whisper** (OpenAI) — Real-time lyric transcription
- **Demucs** or **Spleeter** — Vocal isolation
- **SoundDevice** — Audio capture
- **python-osc** — ProPresenter OSC communication

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
python -m auto_lyrics start
```

## Project Structure

```
auto-lyrics-pro-presenter/
├── src/
│   ├── __init__.py
│   ├── audio_capture.py      # Mic/audio input handling
│   ├── vocal_isolation.py    # Separate vocals from instruments
│   ├── lyric_engine.py       # Whisper-based lyric recognition + matching
│   ├── propresenter_bridge.py # ProPresenter OSC/HTTP integration
│   ├── song_loader.py        # Load and manage song lyric databases
│   └── config.py             # Configuration management
├── tests/
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Status

🚧 Initial development — building the foundation.
