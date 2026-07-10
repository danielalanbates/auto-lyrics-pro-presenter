"""Main entry point for AutoLyrics."""

__version__ = "1.0.0"

BANNER = rf"""
  ┌─────────────────────────────────────────────┐
  │   ♪ AutoLyrics for ProPresenter  v{__version__}     │
  │   listens · follows · fires the right slide │
  └─────────────────────────────────────────────┘
"""

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


class AutoLyricsApp:
    """Main application that ties all components together."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._running = False

        # Initialize components
        self.song_loader = SongLoader(config.songs_directory)
        self.lyric_engine = LyricEngine(config.whisper, config.matching)
        self.pp_bridge = ProPresenterBridge(config.propresenter)
        self.audio_capture = AudioCapture(config.audio, callback=self._on_audio)

        logger.info("Auto Lyrics Pro Presenter initialized")

    def start(self, song_name: Optional[str] = None):
        """Start the auto-lyrics service.
        
        Args:
            song_name: Optional song name to load lyrics for
        """
        connected = self.pp_bridge.connect()
        if not connected:
            logger.warning("ProPresenter unreachable — running in suggest-only mode")

        if song_name:
            lyrics = self.song_loader.load_song_by_name(song_name)
            if lyrics:
                self.lyric_engine.load_song(lyrics)
                if connected:
                    self._prepare_deck(song_name, lyrics)
            else:
                logger.warning(f"Could not load song '{song_name}', running without lyrics")

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
                time.sleep(5.0)
                self._print_status()
        except KeyboardInterrupt:
            self._handle_shutdown(None, None)

    def _prepare_deck(self, song_name: str, lyrics: str):
        """Export the song as a native PP deck and target it for triggers."""
        from .pro_export import deck_name, export_song

        name = deck_name(song_name, lyrics)
        uuid = self.pp_bridge.find_presentation(name)
        if uuid is None:
            export_song(name, lyrics)
            for _ in range(30):  # PP watches the library dir; wait for the scan
                time.sleep(1)
                uuid = self.pp_bridge.find_presentation(name)
                if uuid:
                    break
        if uuid:
            self.pp_bridge.focus_presentation(uuid)
            logger.info(f"Targeting PP deck '{name}' ({uuid})")
        else:
            logger.warning(f"PP never indexed '{name}'; triggering active presentation")

    def _on_audio(self, audio: np.ndarray, sample_rate: int):
        """Called when new audio buffer is available."""
        if not self._running:
            return

        # Transcribe the raw mic buffer — the SAME path the reference decks
        # were built with and the acoustic test suite verifies. (Bandpass
        # "vocal isolation" was removed from the hot path: it made live audio
        # differ from what the decks were tuned on, hurting match accuracy.)
        text = self.lyric_engine.transcribe(audio, sample_rate)
        if not text:
            return

        logger.debug(f"Transcribed: '{text}'")

        # Step 3: Match against lyrics
        result = self.lyric_engine.match_lyrics(text)
        suggestion = result.suggestion
        if not suggestion:
            return

        # Step 4: Fire (or suggest) the slide move
        if self.config.matching.auto_fire:
            logger.info(
                f"AUTO → slide {suggestion.index} "
                f"(conf {suggestion.confidence:.2f}) [{suggestion.reason}]"
            )
            self.pp_bridge.go_to_slide(suggestion.index)
            self.lyric_engine.confirm_move(suggestion.index)
        else:
            # Suggest-and-confirm: surface it; an operator confirms (UI/hotkey).
            logger.info(
                f"SUGGEST → slide {suggestion.index} "
                f"(conf {suggestion.confidence:.2f}) [{suggestion.reason}] "
                f"'{result.matched_line.display[:60]}'"
            )

    def _print_status(self):
        """Print periodic status."""
        progress = self.lyric_engine.get_progress()
        current = self.lyric_engine.get_current_line()
        if current:
            logger.info(f"Progress: {progress:.0%} | Next: '{current.display[:60]}'")

    def _handle_shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False
        self.audio_capture.stop()
        sys.exit(0)


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="autolyrics",
        description="AutoLyrics for ProPresenter — listens to the room, follows the song, fires the right slide.",
        epilog='Example: autolyrics --song "For All My Days (Live at Camp)"',
    )
    parser.add_argument("--config", type=Path, help="Path to config YAML")
    parser.add_argument("--song", type=str, help="Song name to load")
    parser.add_argument("--list-songs", action="store_true", help="List available songs")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices")
    parser.add_argument("--version", action="version", version=f"AutoLyrics {__version__}")
    args = parser.parse_args()
    print(BANNER)

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
