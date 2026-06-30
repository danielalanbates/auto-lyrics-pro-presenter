"""Offline test for the 10-song playlist, bypassing ProPresenter."""
import os
import sys
import glob

import soundfile as sf
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.lyric_engine import LyricEngine

SCRATCH = "/tmp/auto_lyrics_test"

def tts_to_array(text: str, idx: int):
    os.makedirs(SCRATCH, exist_ok=True)
    aiff = f"{SCRATCH}/tts_{idx}.aiff"
    wav = f"{SCRATCH}/tts_{idx}.wav"
    subprocess.run(["say", "-o", aiff, text], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-ar", "16000", "-ac", "1", wav],
        check=True,
    )
    data, _ = sf.read(wav, dtype="float32")
    return data

def test_song(filepath: str):
    with open(filepath, "r") as f:
        content = f.read()
    
    slides = [p.strip() for p in content.split("\n\n") if p.strip()]
    print(f"\n--- Testing {os.path.basename(filepath)} ({len(slides)} slides) ---")
    
    cfg = load_config()
    cfg.matching.auto_fire = True
    
    engine = LyricEngine(cfg.whisper, cfg.matching, cfg.predict)
    engine.load_song(slides)
    engine.set_current_index(0)
    
    for i in range(len(slides) - 1):
        line = slides[i]
        audio = tts_to_array(line, i)
        heard = engine.transcribe(audio, cfg.audio.sample_rate)
        words = heard.split()
        
        target = None
        for w in words:
            dec = engine.process(w)
            if dec and dec.action in ("advance", "jump"):
                target = dec.index
                engine.confirm_move(dec.index)
                break
                
        if target == i + 1:
            print(f"Slide {i} -> {target} OK")
            engine.set_current_index(target)
        else:
            print(f"Slide {i} -> FAILED (target={target}) | Heard: {heard}")
            return False
            
    print(f"PASS: {os.path.basename(filepath)}")
    return True

if __name__ == "__main__":
    playlist = sorted(glob.glob("tests/playlist/*.txt"))
    all_ok = True
    for song_file in playlist:
        if not test_song(song_file):
            all_ok = False
            break
            
    if all_ok:
        print("\nAll 10 songs passed perfectly!")
        sys.exit(0)
    else:
        print("\nTesting failed.")
        sys.exit(1)
