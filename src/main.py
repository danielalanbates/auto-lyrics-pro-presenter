"""Main entry point for Auto Lyrics Pro Presenter."""

import signal
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from loguru import logger

from .audio_capture import AudioCapture
from .config import AppConfig, load_config
from .lyric_engine import LyricEngine
from .propresenter_bridge import ProPresenterBridge
from .song_loader import SongLoader
from .vocal_isolation import VocalIsolator


class AutoLyricsApp:
    """Main application that ties all components together."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._running = False
        self._pending_suggestion: Optional[int] = None

        # Initialize components
        self.song_loader = SongLoader(config.songs_directory)
        self.vocal_isolator = VocalIsolator(backend="demucs")
        self.lyric_engine = LyricEngine(config.whisper, config.matching, config.predict)
        self.pp_bridge = ProPresenterBridge(config.propresenter)
        self.audio_capture = AudioCapture(config.audio, callback=self._on_audio)

        logger.info("Auto Lyrics Pro Presenter initialized")

    def start(self, song_name: Optional[str] = None):
        """Start the auto-lyrics service.

        Args:
            song_name: Optional song name to load lyrics for
        """
        # Confirm the ProPresenter API is reachable (auto-discovers the port).
        if not self.pp_bridge.connect():
            logger.warning("Continuing without a confirmed ProPresenter connection.")

        self._load_answer_key(song_name)

        # Set up signal handlers
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        self._running = True
        logger.info("Starting Auto Lyrics Pro Presenter...")
        logger.info("Press Ctrl+C to stop")

        # Start audio capture
        self.audio_capture.start()

        # Keep main thread alive
        try:
            while self._running:
                time.sleep(0.5)
                self._print_status()
        except KeyboardInterrupt:
            self._handle_shutdown(None, None)

    def _load_answer_key(self, song_name: Optional[str]):
        """Load the candidate set (the song's slides). Prefer pulling it live
        from ProPresenter so it's always exactly what's loaded on screen; fall
        back to a local .txt song file."""
        slides = []
        if self.config.matching.use_propresenter_answer_key:
            slides = self.pp_bridge.get_song_slides()
            if slides:
                logger.info(f"Answer key from ProPresenter: {len(slides)} slides")
        if not slides and song_name:
            lyrics = self.song_loader.load_song_by_name(song_name)
            if lyrics:
                slides = lyrics
                logger.info(f"Answer key from song file: '{song_name}'")
        if slides:
            self.lyric_engine.load_song(slides)
        else:
            logger.warning(
                "No answer key (ProPresenter slide text unavailable and no --song). "
                "Running without matching — enable the Network API or pass --song."
            )

    def _sync_position(self):
        """Re-anchor the matcher to whatever slide ProPresenter is actually
        showing, so manual operator moves keep us in sync instead of fighting."""
        idx = self.pp_bridge.get_current_slide_index()
        if idx is not None:
            self.lyric_engine.set_current_index(idx)

    def _on_audio(self, audio: np.ndarray, sample_rate: int):
        """Called when new audio buffer is available."""
        if not self._running:
            return

        # Keep our position prior aligned with the live slide before deciding.
        self._sync_position()

        # Step 1: Isolate vocals
        vocals = self.vocal_isolator.isolate_vocals(audio, sample_rate)

        # Step 2: Transcribe (primed with the song's lyrics)
        text = self.lyric_engine.transcribe(vocals, sample_rate)
        if not text:
            return
        logger.debug(f"Transcribed: '{text}'")

        # Step 3: Decide — predictive advance (tracker) or matcher correction.
        decision = self.lyric_engine.process(text)
        if not decision:
            return

        if self.config.matching.auto_fire:
            logger.info(
                f"AUTO {decision.action.upper()} → slide {decision.index} "
                f"(conf {decision.confidence:.2f}) [{decision.reason}]"
            )
            if self.pp_bridge.go_to_slide(decision.index):
                self.lyric_engine.confirm_move(decision.index)
        else:
            # Suggest-and-confirm: surface it; an operator confirms (UI/hotkey).
            logger.info(
                f"SUGGEST {decision.action} → slide {decision.index} "
                f"(conf {decision.confidence:.2f}) [{decision.reason}]"
            )
            self._pending_suggestion = decision.index

    def _print_status(self):
        """Print periodic status."""
        progress = self.lyric_engine.get_progress()
        current = self.lyric_engine.get_current_slide()
        if current:
            mode = "AUTO" if self.config.matching.auto_fire else "SUGGEST"
            logger.info(f"[{mode}] Progress: {progress:.0%} | On slide: '{current[:60]}'")

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False
        self.audio_capture.stop()
        sys.exit(0)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Auto Lyrics Pro Presenter")
    parser.add_argument("--config", type=Path, help="Path to config YAML")
    parser.add_argument("--song", type=str, help="Song name to load")
    parser.add_argument("--list-songs", action="store_true", help="List available songs")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.list_devices:
        capture = AudioCapture(config.audio)
        for device in capture.list_devices():
            print(f"  [{device['index']}] {device['name']} ({device['channels']}ch)")
        return

    if args.list_songs:
        loader = SongLoader(config.songs_directory)
        songs = loader.list_songs()
        if songs:
            print("Available songs:")
            for s in songs:
                print(f"  - {s}")
        else:
            print(f"No songs found in {config.songs_directory}")
        return

    app = AutoLyricsApp(config)
    app.start(song_name=args.song)


if __name__ == "__main__":
    main()
