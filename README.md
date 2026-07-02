# Auto Lyrics Pro Presenter

Real-time lyric recognition that drives ProPresenter slides automatically. Listen to live singing, transcribe it on-device with Whisper, match it against the song's slide deck, and fire the right slide in ProPresenter — verified against ProPresenter's own reported state, not fire-and-forget.

## How it works

```
┌─────────────────┐   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│  Audio Capture  │──▶│  Vocal Isolation │──▶│   Lyric Engine   │──▶│  ProPresenter Bridge │
│  (macOS mic)    │   │ (spectral gating)│   │ (faster-whisper) │   │  (HTTP API v1)       │
└─────────────────┘   └──────────────────┘   └──────────────────┘   └──────────────────────┘
```

| Module | Purpose |
|--------|---------|
| `src/audio_capture.py` | Live audio input from the mic (sounddevice) |
| `src/vocal_isolation.py` | Real-time-safe spectral gating to reduce music bleed |
| `src/lyric_engine.py` | Windowed faster-whisper transcription + slide-based matching with auto-fire, forward-only bias, jump penalties, and soft-hit confirmation |
| `src/propresenter_bridge.py` | ProPresenter 7/21 HTTP API: port auto-discovery, slide triggers, and state read-back |
| `src/pro_export.py` | Exports a lyric deck as a native `.pro` presentation ProPresenter indexes automatically |
| `src/pp_proto/` | Generated protobuf schema for `.pro` files ([greyshirtguy/ProPresenter7-Proto](https://github.com/greyshirtguy/ProPresenter7-Proto), MIT) |
| `src/song_loader.py` | Loads blank-line-separated slide lyrics from the songs directory |

### Design decisions that mattered

- **Slide-based matching, not line-based.** The engine matches transcription windows against whole slides and only moves forward (`lookbehind_slides: 0` by default) — back-jumps cause chorus oscillation.
- **Reference decks are built by the same pipeline that runs live.** A song's deck comes from recording and transcribing the actual track with the live windowed pipeline, then self-consistency pruning: the deck is simulated at multiple time offsets and with injected noise, and slides that don't fire in order across all variants are pruned (with a density floor so pruning can't produce a trivially-passing skeleton deck).
- **`.pro` export clones a ProPresenter-authored template.** Hand-built protobuf cues parse to *zero* slides in ProPresenter no matter how closely they match the schema. The exporter embeds a verified PP-authored cue + presentation skeleton and swaps in UUIDs and lyric RTF. Deck UUIDs are deterministic per name because PP indexes a file by its first-seen UUID.
- **Read-back everywhere.** ProPresenter's `v1/presentation/slide_index` confirms every fired slide; the tests never trust a 200 response.

## Requirements

- macOS, ProPresenter 7/21 with **Settings → Network → Enable Network** checked
- Python 3.11+

## Getting started

```bash
pip install -r requirements.txt

# List audio devices / available songs
python -m src.main --list-devices
python -m src.main --list-songs

# Run live: exports the deck into ProPresenter (if needed), targets it,
# and auto-fires slides as the song is sung
python -m src.main --song "For All My Days (Live at Camp)"
```

Songs live in `~/songs` as `.txt` files: one slide per blank-line-separated block.

## Testing

The test harness plays real recordings through the speakers, listens through the mic (a genuine acoustic loop, not injected audio), and checks that every slide fires in order.

```bash
# Build references + live-test the whole playlist against a mock bridge
python -m tests.run_playlist

# Verify against REAL ProPresenter over the HTTP API
python -m tests.pp_verify api                 # deterministic: trigger + read back every slide
python -m tests.pp_verify live "Track Name"   # full loop: mic → whisper → real PP slides
python -m tests.pp_verify live-all
```

`tests/live_playlist/results.json` records per-song results. A pass requires every slide fired in order and a deck dense enough to be useful (≥6 slides, ~1 per 45s of audio).
