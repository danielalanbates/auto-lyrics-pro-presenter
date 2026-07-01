"""Run the full live test playlist: build references and live-test each song.

Usage: python -m tests.run_playlist [--tests-only]
Writes results to tests/live_playlist/results.json as it goes.
"""

import json
import sys
from pathlib import Path

from loguru import logger

from tests.harness import PLAYLIST_DIR, build_reference, live_test, slug

SONGS = [
    "Act Justly, Love Mercy, Walk Humbly",
    "Coming Back",
    "For All My Days (Live at Camp)",
    "Sparrows",
    "Our God Is Coming Back",
    "This Won't Take My Praise",
    "Know You Will",
    "Sound Mind",
    "Time and Time Again",
    "What My Father's Like",
]

RESULTS = PLAYLIST_DIR / "results.json"


def main():
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
