"""Live test harness — builds reference lyrics and runs live slide-advance tests.

Pass A (build): play a track from the Music app through the speakers, record
the mic, transcribe offline (beam 5), group segments into slides, and save a
reference lyrics file into tests/live_playlist/.

Pass B (test): replay the track, run the real pipeline (AudioCapture →
LyricEngine → mock ProPresenter bridge with auto_fire), and require the fired
slide sequence to be exactly 0..N-1 in order — a perfect pass.

Usage:
  python -m tests.harness build "Track Name"
  python -m tests.harness test  "Track Name"
  python -m tests.harness run   "Track Name"   # build (if needed) then test
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger

REPO = Path(__file__).resolve().parent.parent
PLAYLIST_DIR = REPO / "tests" / "live_playlist"
RECORDINGS_DIR = REPO / "tests" / "recordings"
MANIFEST = PLAYLIST_DIR / "playlist.json"
MUSIC_PLAYLIST = "My Praise"
SAMPLE_RATE = 16000

sys.path.insert(0, str(REPO))
from src.config import AppConfig  # noqa: E402
from src.audio_capture import AudioCapture  # noqa: E402
from src.lyric_engine import LyricEngine  # noqa: E402


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"osascript failed: {r.stderr.strip()}")
    return r.stdout.strip()


def music_play(track: str) -> float:
    """Start the track from the beginning; return its duration in seconds."""
    osa('set volume output volume 80')
    dur = float(osa(
        f'tell application "Music" to get duration of track "{track}" of playlist "{MUSIC_PLAYLIST}"'
    ))
    osa(f'tell application "Music" to play track "{track}" of playlist "{MUSIC_PLAYLIST}"')
    osa('tell application "Music" to set player position to 0')
    return dur


def music_stop():
    subprocess.run(["osascript", "-e", 'tell application "Music" to stop'], capture_output=True)


def record(duration: float) -> np.ndarray:
    """Record the mic for `duration` seconds while the track plays."""
    import sounddevice as sd
    frames = int(duration * SAMPLE_RATE)
    rec = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=0)
    sd.wait()
    return rec[:, 0]


# ---------------------------------------------------------------- pass A

def build_reference(track: str, max_seconds: float = 600) -> Path:
    """Play the track, record it, transcribe offline, save slide-blocked lyrics."""
    from faster_whisper import WhisperModel

    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    s = slug(track)
    wav_path = RECORDINGS_DIR / f"{s}.wav"

    if wav_path.exists():
        logger.info(f"Using existing recording {wav_path.name}")
        audio, _ = sf.read(wav_path, dtype="float32")
        dur = len(audio) / SAMPLE_RATE
    else:
        dur = min(music_play(track), max_seconds)
        logger.info(f"Recording '{track}' for {dur:.0f}s ...")
        audio = record(dur)
        music_stop()
        sf.write(wav_path, audio, SAMPLE_RATE)
        logger.info(f"Saved recording (RMS {float(np.sqrt((audio**2).mean())):.4f})")

    logger.info("Transcribing reference (beam 5)...")
    model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, language="en", beam_size=5, vad_filter=False)

    # Group segments into slides: break at ~12s of accumulated span or 18 words.
    slides: list[list[str]] = []
    cur: list[str] = []
    cur_start = None
    cur_words = 0
    for seg in segments:
        text = seg.text.strip()
        if not text or not re.search(r"\w", text):
            continue
        if cur_start is None:
            cur_start = seg.start
        cur.append(text)
        cur_words += len(text.split())
        if (seg.end - cur_start) >= 12 or cur_words >= 18:
            slides.append(cur)
            cur, cur_start, cur_words = [], None, 0
    if cur:
        slides.append(cur)

    # Drop slides with fewer than 4 words (noise / crowd / instrumental)
    slides = [sl for sl in slides if sum(len(l.split()) for l in sl) >= 4]

    lyrics_path = PLAYLIST_DIR / f"{s}.txt"
    lyrics_path.write_text("\n\n".join("\n".join(sl) for sl in slides) + "\n")
    logger.info(f"Wrote {len(slides)} slides → {lyrics_path.name}")

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"songs": []}
    manifest["songs"] = [e for e in manifest["songs"] if e["slug"] != s]
    manifest["songs"].append({"track": track, "slug": s, "duration": dur, "slides": len(slides)})
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return lyrics_path


# ---------------------------------------------------------------- pass B

class MockBridge:
    """Records go_to_slide calls instead of talking to ProPresenter."""

    def __init__(self):
        self.fired: list[tuple[float, int]] = []

    def go_to_slide(self, index: int):
        self.fired.append((time.time(), index))
        logger.info(f"[MockPP] go_to_slide({index})")


def live_test(track: str) -> dict:
    """Replay the track through speakers; the pipeline must hit every slide in order."""
    s = slug(track)
    lyrics_path = PLAYLIST_DIR / f"{s}.txt"
    if not lyrics_path.exists():
        raise FileNotFoundError(f"No reference lyrics for '{track}' — run build first")

    config = AppConfig()
    engine = LyricEngine(config.whisper, config.matching)
    engine.load_song(lyrics_path.read_text())
    n_slides = len(engine.get_slides())
    bridge = MockBridge()

    def on_audio(audio: np.ndarray, sample_rate: int):
        text = engine.transcribe(audio, sample_rate)
        if not text:
            return
        result = engine.match_lyrics(text)
        sug = result.suggestion
        if sug and config.matching.auto_fire:
            logger.info(f"AUTO → slide {sug.index} (conf {sug.confidence:.2f}) [{sug.reason}]")
            bridge.go_to_slide(sug.index)
            engine.confirm_move(sug.index)

    capture = AudioCapture(config.audio, callback=on_audio)
    dur = music_play(track)
    logger.info(f"LIVE TEST '{track}' — {n_slides} slides, {dur:.0f}s")
    capture.start()
    t0 = time.time()
    try:
        while time.time() - t0 < dur + 3:
            time.sleep(2)
            state = osa('tell application "Music" to get player state as string')
            if state != "playing":
                break
    finally:
        capture.stop()
        music_stop()

    fired = [i for _, i in bridge.fired]
    perfect = fired == list(range(n_slides))
    result = {
        "track": track,
        "slides": n_slides,
        "fired": fired,
        "perfect": perfect,
    }
    logger.info(f"RESULT: {'PERFECT PASS' if perfect else 'FAIL'} — fired {fired} of 0..{n_slides-1}")
    return result


def main():
    cmd, track = sys.argv[1], sys.argv[2]
    if cmd == "build":
        build_reference(track)
    elif cmd == "test":
        print(json.dumps(live_test(track)))
    elif cmd == "run":
        if not (PLAYLIST_DIR / f"{slug(track)}.txt").exists():
            build_reference(track)
        print(json.dumps(live_test(track)))
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
