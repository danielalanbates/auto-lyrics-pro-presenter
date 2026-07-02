"""Verify the pipeline against REAL ProPresenter — no mock, no human.

Two stages:
  api               deterministic check: export a deck into the PP library,
                    trigger every slide over the HTTP API, and read back the
                    live slide index from ProPresenter after each trigger.
  live "Track"      the full product loop: play the song through the speakers,
                    transcribe live, and auto-fire REAL ProPresenter slides,
                    verifying each fired slide against PP's own reported state.

Usage:
  python -m tests.pp_verify api
  python -m tests.pp_verify live "For All My Days (Live at Camp)"
  python -m tests.pp_verify live-all        # every song in the test playlist
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.config import AppConfig  # noqa: E402
from src.propresenter_bridge import ProPresenterBridge  # noqa: E402
from src.pro_export import export_song  # noqa: E402
from tests.harness import (  # noqa: E402
    PLAYLIST_DIR, MANIFEST, mic_device, music_play, music_stop, force_speakers, slug, osa,
)


def pp_http_port() -> int:
    """Discover ProPresenter's Network API port from its listening sockets."""
    r = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-c", "ProPresenter"],
        capture_output=True, text=True,
    )
    ports = sorted({int(m) for m in re.findall(r":(\d+) \(LISTEN\)", r.stdout)})
    if not ports:
        raise RuntimeError(
            "ProPresenter is not listening on any TCP port — "
            "enable Preferences → Network → Enable Network"
        )
    import requests
    for p in ports:
        try:
            resp = requests.get(f"http://127.0.0.1:{p}/version", timeout=2)
            if resp.ok and "api_version" in resp.text:
                return p
        except requests.RequestException:
            continue
    raise RuntimeError(f"No ProPresenter API answering on listening ports {ports}")


def make_bridge() -> ProPresenterBridge:
    config = AppConfig()
    config.propresenter.http_port = pp_http_port()
    config.propresenter.use_http = True
    config.propresenter.use_osc = False
    bridge = ProPresenterBridge(config.propresenter)
    if not bridge.connect():
        raise RuntimeError("ProPresenter API unreachable")
    return bridge


def ensure_deck(bridge: ProPresenterBridge, track: str) -> tuple[str, int]:
    """Export the song's deck into the PP library; return (uuid, n_slides)."""
    lyrics_path = PLAYLIST_DIR / f"{slug(track)}.txt"
    if not lyrics_path.exists():
        raise FileNotFoundError(f"No reference lyrics for '{track}'")
    name = f"AutoLyrics - {track}"
    lyrics = lyrics_path.read_text()
    n_slides = lyrics.strip().count("\n\n") + 1
    uuid = bridge.find_presentation(name)
    if uuid is None:
        export_song(name, lyrics)
        for _ in range(30):  # PP watches the library dir; wait for the scan
            time.sleep(1)
            uuid = bridge.find_presentation(name)
            if uuid:
                break
    if uuid is None:
        raise RuntimeError(f"ProPresenter never indexed '{name}'")
    return uuid, n_slides


def api_check() -> bool:
    """Trigger every slide of one deck and read back PP's live slide index."""
    bridge = make_bridge()
    manifest = json.loads(MANIFEST.read_text())
    track = manifest["songs"][0]["track"]
    uuid, n = ensure_deck(bridge, track)
    bridge.focus_presentation(uuid)
    logger.info(f"API check on '{track}' ({n} slides, uuid {uuid})")
    ok = True
    for i in range(n):
        bridge.go_to_slide(i)
        if bridge.verify_slide(i):
            logger.info(f"  slide {i}: PP confirms live")
        else:
            logger.error(f"  slide {i}: PP reports {bridge.live_slide_index()} instead")
            ok = False
    logger.info(f"API CHECK: {'PASS' if ok else 'FAIL'}")
    return ok


def live_pp_test(track: str) -> dict:
    """Full product loop against real ProPresenter, verified via PP state."""
    import numpy as np
    from src.audio_capture import AudioCapture
    from src.lyric_engine import LyricEngine

    bridge = make_bridge()
    uuid, _ = ensure_deck(bridge, track)
    bridge.focus_presentation(uuid)

    config = AppConfig()
    config.audio.device_index = mic_device()
    engine = LyricEngine(config.whisper, config.matching)
    engine.load_song((PLAYLIST_DIR / f"{slug(track)}.txt").read_text())
    n_slides = len(engine.get_slides())

    fired: list[int] = []
    pp_confirmed: list[int] = []

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
            fired.append(sug.index)
            if bridge.verify_slide(sug.index):
                pp_confirmed.append(sug.index)
            else:
                logger.error(f"PP shows {bridge.live_slide_index()}, expected {sug.index}")

    capture = AudioCapture(config.audio, callback=on_audio)
    dur = music_play(track)
    logger.info(f"LIVE PP TEST '{track}' — {n_slides} slides, {dur:.0f}s")
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
                if not_playing == 2 and time.time() - t0 < dur - 5:
                    osa('tell application "Music" to play')
                if not_playing >= 5:
                    break
        time.sleep(9)
    finally:
        capture.stop()
        music_stop()

    want = list(range(n_slides))
    result = {
        "track": track,
        "slides": n_slides,
        "fired": fired,
        "pp_confirmed": pp_confirmed,
        "perfect": fired == want and pp_confirmed == want and n_slides >= 6,
    }
    logger.info(
        f"RESULT: {'PERFECT PP PASS' if result['perfect'] else 'FAIL'} — "
        f"fired {fired}, PP confirmed {pp_confirmed} of 0..{n_slides - 1}"
    )
    return result


def main():
    cmd = sys.argv[1]
    if cmd == "api":
        sys.exit(0 if api_check() else 1)
    elif cmd == "live":
        print(json.dumps(live_pp_test(sys.argv[2])))
    elif cmd == "live-all":
        results_path = PLAYLIST_DIR / "pp_results.json"
        results = json.loads(results_path.read_text()) if results_path.exists() else {}
        manifest = json.loads(MANIFEST.read_text())
        for entry in manifest["songs"]:
            track = entry["track"]
            if results.get(slug(track), {}).get("perfect"):
                logger.info(f"SKIP (already perfect vs PP): {track}")
                continue
            try:
                results[slug(track)] = live_pp_test(track)
            except Exception as e:
                logger.error(f"{track} failed: {e}")
                results[slug(track)] = {"track": track, "error": str(e), "perfect": False}
            results_path.write_text(json.dumps(results, indent=2) + "\n")
            passed = sum(1 for r in results.values() if r.get("perfect"))
            print(f"PP PROGRESS: {passed}/{len(manifest['songs'])} perfect", flush=True)
        passed = sum(1 for r in results.values() if r.get("perfect"))
        print(f"PP FINAL: {passed}/{len(manifest['songs'])} perfect", flush=True)
    else:
        raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    main()
