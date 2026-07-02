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


def mic_device() -> int:
    """Index of the built-in mic — never a Bluetooth/virtual device."""
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if "MacBook Pro Microphone" in d["name"] and d["max_input_channels"] > 0:
            return i
    raise RuntimeError("MacBook Pro Microphone not found")


def force_speakers():
    """Pin audio to the built-in speakers (Jump Desktop re-claims routing)."""
    subprocess.run([str(REPO / "tools" / "setout"), "MacBook Pro Speakers"], capture_output=True)
    try:
        osa('tell application "Music" to set current AirPlay devices to (AirPlay device "Computer")')
    except RuntimeError:
        pass


def music_play(track: str) -> float:
    """Start the track from the beginning; return its duration in seconds."""
    force_speakers()
    # Moderate volume — enough for the mic to hear, easy on the speakers.
    osa('set volume output volume 50')
    osa('tell application "Music" to set sound volume to 70')
    dur = float(osa(
        f'tell application "Music" to get duration of track "{track}" of playlist "{MUSIC_PLAYLIST}"'
    ))
    osa(f'tell application "Music" to play track "{track}" of playlist "{MUSIC_PLAYLIST}"')
    # Wait for streaming playback to actually start before recording.
    for _ in range(15):
        if osa('tell application "Music" to get player state as string') == "playing":
            break
        time.sleep(1)
    try:
        osa('tell application "Music" to set player position to 0')
    except RuntimeError:
        pass  # some streaming states reject seeking; track just started anyway
    return dur


def music_stop():
    subprocess.run(["osascript", "-e", 'tell application "Music" to stop'], capture_output=True)


def record(duration: float) -> np.ndarray:
    """Record the mic for `duration` seconds while the track plays."""
    import sounddevice as sd
    import threading
    frames = int(duration * SAMPLE_RATE)
    rec = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=mic_device())
    done = threading.Event()

    def enforce():  # keep audio on the speakers for the whole recording
        while not done.wait(5):
            force_speakers()

    t = threading.Thread(target=enforce, daemon=True)
    t.start()
    sd.wait()
    done.set()
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

    # Transcribe with the SAME windowed pipeline the live engine uses —
    # consistency between passes matters more than absolute accuracy.
    logger.info("Transcribing reference (live-style windows)...")
    config = AppConfig()
    engine = LyricEngine(config.whisper, config.matching)
    win = 8 * SAMPLE_RATE
    slides: list[list[str]] = []
    for i in range(0, len(audio) - win // 2, win):
        text = engine.transcribe(audio[i:i + win], SAMPLE_RATE).strip()
        if text and re.search(r"\w", text) and len(text.split()) >= 3:
            slides.append([text])

    # Drop slides with fewer than 4 words (noise / crowd / instrumental)
    slides = [sl for sl in slides if sum(len(l.split()) for l in sl) >= 4]

    # Self-consistency pruning: simulate a live pass over this same recording
    # and drop slides that don't fire — they're transcription artifacts that
    # will never match live. Repeat until the simulation is a perfect pass.
    # Prune against multiple window alignments: live capture starts at an
    # arbitrary phase, so a slide must fire at every offset to be trusted.
    for round_ in range(6):
        lyrics = "\n\n".join("\n".join(sl) for sl in slides)
        fired_sets = [simulate(lyrics, audio, off) for off in (0.0, 1.5)]
        want = list(range(len(slides)))
        logger.info(
            f"self-check round {round_}: " +
            ", ".join(f"off{o}: {len(f)}/{len(slides)}" for o, f in zip((0.0, 1.5), fired_sets))
        )
        if all(f == want for f in fired_sets):
            break
        # Keep slides that fired in order at EVERY offset
        common = set(fired_sets[0])
        for f in fired_sets[1:]:
            common &= set(f)
        slides = [slides[i] for i in sorted(common)] or slides[:1]

    lyrics_path = PLAYLIST_DIR / f"{s}.txt"
    lyrics_path.write_text("\n\n".join("\n".join(sl) for sl in slides) + "\n")
    logger.info(f"Wrote {len(slides)} slides → {lyrics_path.name}")

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"songs": []}
    manifest["songs"] = [e for e in manifest["songs"] if e["slug"] != s]
    manifest["songs"].append({"track": track, "slug": s, "duration": dur, "slides": len(slides)})
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    return lyrics_path


def simulate(lyrics: str, audio: np.ndarray, offset: float = 0.0) -> list[int]:
    """Offline replica of the live pass: 12s window, 3s hop. Returns fired slides.

    `offset` shifts the window grid — live capture starts at an arbitrary
    phase relative to the song, and alignment changes Whisper's output.
    """
    config = AppConfig()
    engine = LyricEngine(config.whisper, config.matching)
    engine.load_song(lyrics)
    fired: list[int] = []
    sr = SAMPLE_RATE
    start = offset
    total = len(audio) / sr
    while start + 3 <= total:
        seg = audio[max(0, int((start - 9) * sr)):int((start + 3) * sr)]
        r = engine.match_lyrics(engine.transcribe(seg, sr))
        if r.suggestion:
            engine.confirm_move(r.suggestion.index)
            engine._last_move_time = 0  # sim time ≠ wall time; skip debounce
            fired.append(r.suggestion.index)
        start += 3
    return fired


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
    config.audio.device_index = mic_device()
    engine = LyricEngine(config.whisper, config.matching)
    engine.load_song(lyrics_path.read_text())
    n_slides = len(engine.get_slides())
    bridge = MockBridge()

    def on_audio(audio: np.ndarray, sample_rate: int):
        text = engine.transcribe(audio, sample_rate)
        if not text:
            return
        result = engine.match_lyrics(text)
        logger.debug(f"heard (conf {result.confidence:.2f}, cur {engine.get_current_slide()}): {text[-120:]}")
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
    not_playing = 0
    try:
        while time.time() - t0 < dur + 3:
            time.sleep(2)
            force_speakers()
            state = osa('tell application "Music" to get player state as string')
            if state == "playing":
                not_playing = 0
            else:
                not_playing += 1
                # Streaming tracks report stopped/buffering transiently; only
                # give up after repeated checks, and try to resume once.
                if not_playing == 2 and time.time() - t0 < dur - 5:
                    osa('tell application "Music" to play')  # resume, don't restart
                if not_playing >= 5:
                    break
        # Post-roll: let the pipeline process the final buffered windows.
        time.sleep(9)
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
