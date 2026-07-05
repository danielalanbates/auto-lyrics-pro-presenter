"""Run the full live test playlist: build references and live-test each song.

Usage: python -m tests.run_playlist [--tests-only]
Writes results to tests/live_playlist/results.json as it goes.
"""

import json
import sys
from pathlib import Path

from loguru import logger

from tests.harness import PLAYLIST_DIR, build_reference, live_test, slug

# Live recordings only, per operator request — shortest ten from "My Praise".
SONGS = [
    "For All My Days (Live at Camp)",
    "The God You Are (Live)",
    "Abundantly More (feat. Clay Finnesand) [Live at Decatur City]",
    "Glory, Honor, Power (Live)",
    "First Love (Live)",
    "Who You Say I Am (Live)",
    "Holy Ground (feat. Melodie Malone) [Live]",
    "One (Live At Church)",
    "Know (Be Still) [Live in Anaheim, California]",
    "Freedom (feat. Kim Walker-Smith) [Live]",
]

RESULTS = PLAYLIST_DIR / "results.json"


def preflight():
    """Fail fast with a remedy if the audio stack is broken (it happens)."""
    import subprocess
    probe = "import sounddevice as sd\nwith sd.InputStream(channels=1): pass\nprint('ok')"
    try:
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=20)
        if "ok" in r.stdout:
            return
        detail = (r.stderr or "").strip().splitlines()[-1:] or ["unknown error"]
        detail = detail[0]
    except subprocess.TimeoutExpired:
        detail = "opening the input stream hangs"
    logger.error(
        f"Microphone input is broken ({detail}). CoreAudio is likely wedged — "
        "run 'sudo killall coreaudiod' or reboot, then re-run."
    )
    sys.exit(2)


def main():
    preflight()
    tests_only = "--tests-only" in sys.argv
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    for track in SONGS:
        s = slug(track)
        if results.get(s, {}).get("perfect"):
            logger.info(f"SKIP (already perfect): {track}")
            continue
        try:
            if not (PLAYLIST_DIR / f"{s}.txt").exists():
                if tests_only:
                    logger.warning(f"No reference for {track}, skipping")
                    continue
                build_reference(track)
            results[s] = live_test(track)
        except Exception as e:
            logger.error(f"{track} failed: {e}")
            results[s] = {"track": track, "error": str(e), "perfect": False}
        RESULTS.write_text(json.dumps(results, indent=2) + "\n")
        passed = sum(1 for r in results.values() if r.get("perfect"))
        print(f"PROGRESS: {passed}/{len(SONGS)} perfect", flush=True)

    passed = sum(1 for r in results.values() if r.get("perfect"))
    print(f"FINAL: {passed}/{len(SONGS)} perfect", flush=True)


if __name__ == "__main__":
    main()
