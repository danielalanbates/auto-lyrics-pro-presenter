# AutoLyrics — Operator Instructions

Step-by-step instructions for running AutoLyrics on a Sunday morning, adding new songs, and re-verifying the system after changes. For architecture and design rationale, see [README.md](README.md).

## 1. One-time setup

1. Install Python 3.11+ and dependencies:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. In ProPresenter: **Settings → Network → Enable Network** (leave the port on auto; the bridge discovers it).
3. Check the mic and audio devices:
   ```bash
   .venv/bin/python -m src.main --list-devices
   ```
   The pipeline expects the built-in **MacBook Pro Microphone** for live capture.

## 2. Running live (service day)

1. Start ProPresenter and make sure Network is enabled.
2. Start the app for the song about to be sung:
   ```bash
   .venv/bin/python -m src.main --song "For All My Days (Live at Camp)"
   ```
   On first run for a song this exports a native `.pro` deck into the ProPresenter library (named `AutoLyrics - <song> [<hash>]`), waits for ProPresenter to index it, then targets it. After that it listens to the room and auto-fires slides as lyrics are recognized.
3. Watch the log: every fired slide is confirmed against ProPresenter's reported `slide_index` — a mismatch is logged, not silently ignored.

Song lyrics live in `~/songs` as `.txt` files, one slide per blank-line-separated block. `--list-songs` shows what's loadable.

## 3. Adding a new song

The reliable path is to build the reference deck from an actual recording with the same pipeline that runs live:

1. Add the track to the **"My Praise"** playlist in the Music app (or place a 16 kHz mono WAV at `tests/recordings/<slug>.wav` — slug is the lowercased, dash-separated title).
2. Build the reference (records through the speakers+mic if no WAV exists; silent if one does):
   ```bash
   .venv/bin/python -m tests.harness build "Exact Track Name"
   ```
   This transcribes with the live windowed pipeline, prunes slides that don't fire consistently across time offsets and injected noise, and enforces a density floor (≥6 slides, ~1 per 45 s) so the deck is actually useful.
3. Test it end-to-end against the mock bridge:
   ```bash
   .venv/bin/python -m tests.harness test "Exact Track Name"
   ```
   A pass requires every slide fired in order, 0..N-1.

## 4. Verification

```bash
# Full playlist through the mock bridge (skips songs already perfect;
# plays audio through the speakers — volume is hard-capped at 30%)
.venv/bin/python -m tests.run_playlist

# Against REAL ProPresenter over the HTTP API:
.venv/bin/python -m tests.pp_verify api                # trigger + read back every slide of every deck
.venv/bin/python -m tests.pp_verify live "Track Name"  # full loop: mic → whisper → real PP slides
.venv/bin/python -m tests.pp_verify live-all           # full loop for the whole playlist
```

Results land in `tests/live_playlist/results.json`. Re-running `run_playlist` only retries songs that aren't yet perfect.

## 5. Rules and gotchas

- **Speaker volume never above 30%.** The harness enforces this (`MAX_OUTPUT_VOLUME` in `tests/harness.py`); don't raise it.
- **Rebuilt decks need a fresh name.** ProPresenter caches a deck's parse by name for its whole session, even across file deletion. The exporter handles this by content-hashing the name (`AutoLyrics - <song> [<sha1-6>]`); if you edit lyrics, just re-run — the hash changes.
- **Don't hand-author `.pro` protobufs.** Hand-built cues parse to zero slides in ProPresenter even when structurally identical to real ones. The exporter clones a verified PP-authored template; keep it that way.
- **Prefer local WAV replay over Apple Music playback** for tests: streaming stalls mid-song, which breaks Whisper's context and run-to-run consistency. `tests/recordings/*.wav` (gitignored — copyrighted audio) is the source of truth.
- **The test loop is acoustic, not injected.** Tests play audio out of the speakers and listen through the mic. Run them in a quiet room; background talking will pollute transcription.
- **Memory pressure:** Whisper + ProPresenter + a VM on a 16 GB machine will swap heavily. Close what you don't need before long runs.
