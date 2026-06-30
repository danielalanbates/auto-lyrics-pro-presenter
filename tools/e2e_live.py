"""Live end-to-end test against the real ProPresenter on this machine.

For each slide of the live song it:
  1. synthesizes the slide's line to audio with macOS `say` (stand-in for a
     singer — TTS is cleaner than singing, so this proves the *chain*, not
     real-room accuracy),
  2. transcribes it with faster-whisper (primed with the song's lyrics),
  3. feeds the transcript word-by-word to the LyricEngine, and
  4. when the engine predictively decides to advance, drives ProPresenter via
     the HTTP API and checks the live slide index actually moved.

It reports, per slide, how many words from the end the advance fired (the
predictive lead) and whether ProPresenter followed. Exit 0 iff every advance
landed on the right slide.
"""

import os
import subprocess
import sys
import time

import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.lyric_engine import LyricEngine
from src.propresenter_bridge import ProPresenterBridge

SCRATCH = "/private/tmp/claude-501/-Users-daniel/f100eedc-812b-414a-be1b-281ab191ccbc/scratchpad"


def tts_to_array(text: str, idx: int):
    """macOS `say` -> 16kHz mono float32 numpy array."""
    aiff = f"{SCRATCH}/tts_{idx}.aiff"
    wav = f"{SCRATCH}/tts_{idx}.wav"
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ar", "16000", "-ac", "1", wav],
        check=True,
    )
    data, _ = sf.read(wav, dtype="float32")
    return data


def main():
    cfg = load_config()
    cfg.matching.auto_fire = True  # autopilot for the test

    bridge = ProPresenterBridge(cfg.propresenter)
    if not bridge.connect():
        print("FAIL: cannot reach ProPresenter API")
        return 1

    slides = bridge.get_song_slides()
    if len(slides) < 2:
        print(f"FAIL: need a multi-slide song live; got {len(slides)} slides")
        return 1
    print(f"Answer key from ProPresenter: {len(slides)} slides")
    for i, s in enumerate(slides):
        print(f"   [{i}] {s}")

    engine = LyricEngine(cfg.whisper, cfg.matching, cfg.predict)
    engine.load_song(slides)

    # Start live at slide 0.
    bridge.go_to_slide(0)
    time.sleep(0.6)
    engine.set_current_index(bridge.get_current_slide_index() or 0)

    rows = []
    ok = True
    for i in range(len(slides) - 1):
        line = slides[i]
        audio = tts_to_array(line, i)
        heard = engine.transcribe(audio, cfg.audio.sample_rate)
        words = heard.split()

        fired_word = None
        target = None
        for k, w in enumerate(words, start=1):
            dec = engine.process(w)
            if dec and dec.action in ("advance", "jump"):
                fired_word = k
                target = dec.index
                bridge.go_to_slide(dec.index)
                engine.confirm_move(dec.index)
                break

        time.sleep(0.5)
        actual = bridge.get_current_slide_index()
        landed = (actual == i + 1)
        ok = ok and landed and (target == i + 1)
        lead = (len(words) - fired_word) if fired_word else None
        rows.append({
            "slide": i,
            "heard": heard.strip(),
            "words": len(words),
            "fired_at_word": fired_word,
            "lead_words": lead,
            "target": target,
            "pp_index": actual,
            "landed": landed,
        })
        # resync from PP for the next slide
        engine.set_current_index(actual if actual is not None else i + 1)

    print("\n=== RESULTS ===")
    for r in rows:
        status = "OK " if r["landed"] and r["target"] == r["slide"] + 1 else "BAD"
        lead = f"{r['lead_words']} word(s) before end" if r["lead_words"] is not None else "NO FIRE"
        print(
            f"[{status}] slide {r['slide']}->{r['target']} (PP now {r['pp_index']}) | "
            f"fired at word {r['fired_at_word']}/{r['words']} ({lead}) | heard: {r['heard']!r}"
        )

    fired = [r for r in rows if r["fired_at_word"]]
    avg_lead = sum(r["lead_words"] for r in fired) / len(fired) if fired else 0
    print(f"\n{sum(1 for r in rows if r['landed'])}/{len(rows)} advances landed correctly")
    print(f"predictive: fired on {len(fired)}/{len(rows)} slides, avg lead {avg_lead:.1f} words before the last word")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
