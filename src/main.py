"""Main entry point for Auto Lyrics Pro Presenter."""

import signal
import sys
import time
from pathlib import Path

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

        # Initialize components
        self.song_loader = SongLoader(config.songs_directory)
        self.vocal_isolator = VocalIsolator(backend="demucs")
        self.lyric_engine = LyricEngine(config.whisper, config.matching)
        self.pp_bridge = ProPresenterBridge(config.propresenter)
        self.audio_capture = AudioCapture(config.audio, callback=self._on_audio)

        logger.info("Auto Lyrics Pro Presenter initialized")

    def start(self, song_name: Optional[str] = None):
        """Start the auto-lyrics service.
        
        Args:
            song_name: Optional song name to load lyrics for
        """
        if song_name:
            lyrics = self.song_loader.load_song_by_name(song_name)
            if lyrics:
                self.lyric_engine.load_song(lyrics)
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
                time.sleep(0.5)
                self._print_status()
        except KeyboardInterrupt:
            self._handle_shutdown(None, None)

    def _on_audio(self, audio: np.ndarray, sample_rate: int):
        """Called when new audio buffer is available."""
        if not self._running:
            return

        # Step 1: Isolate vocals
        vocals = self.vocal_isolator.isolate_vocals(audio, sample_rate)

        # Step 2: Transcribe
        text = self.lyric_engine.transcribe(vocals, sample_rate)
        if not text:
            return

        logger.debug(f"Transcribed: '{text}'")

        # Step 3: Match against lyrics
        result = self.lyric_engine.match_lyrics(text)
        if result.matched_line and result.confidence >= self.config.matching.confidence_threshold:
            logger.info(
                f"✅ Matched: '{result.matched_line.text}' "
                f"(confidence: {result.confidence:.2f})"
            )
            # Step 4: Advance ProPresenter
            self.pp_bridge.advance_slide("next")

    def _print_status(self):
        """Print periodic status."""
        progress = self.lyric_engine.get_progress()
        current = self.lyric_engine.get_current_line()
        if current:
            logger.info(f"Progress: {progress:.0%} | Next: '{current.text[:60]}...'")

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
