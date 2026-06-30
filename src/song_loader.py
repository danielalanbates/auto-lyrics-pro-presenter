"""Song loader — manages song lyric databases."""

import json
from pathlib import Path
from typing import Optional

from loguru import logger


class SongLoader:
    """Loads and manages song lyrics from files."""

    def __init__(self, songs_directory: Path):
        self.songs_dir = songs_directory
        self.songs_dir.mkdir(parents=True, exist_ok=True)

    def load_song_from_file(self, file_path: Path) -> str:
        """Load lyrics from a text file.

        Args:
            file_path: Path to lyrics file (.txt or .lyrics)

        Returns:
            Lyrics text
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Lyrics file not found: {file_path}")

        with open(file_path) as f:
            return f.read()

    def load_song_by_name(self, song_name: str) -> Optional[str]:
        """Find and load a song by name from the songs directory.

        Searches for {song_name}.txt in the songs directory.

        Args:
            song_name: Name of the song (without extension)

        Returns:
            Lyrics text or None if not found
        """
        # Try exact match
        txt_file = self.songs_dir / f"{song_name}.txt"
        if txt_file.exists():
            return self.load_song_from_file(txt_file)

        # Try case-insensitive search
        for f in self.songs_dir.glob("*.txt"):
            if f.stem.lower() == song_name.lower():
                return self.load_song_from_file(f)

        logger.warning(f"Song not found: {song_name}")
        return None

    def list_songs(self) -> list[str]:
        """List all available songs in the songs directory."""
        return sorted([f.stem for f in self.songs_dir.glob("*.txt")])

    def save_song(self, song_name: str, lyrics: str) -> Path:
        """Save lyrics to a file.

        Args:
            song_name: Name of the song
            lyrics: Lyrics text

        Returns:
            Path to saved file
        """
        file_path = self.songs_dir / f"{song_name}.txt"
        with open(file_path, "w") as f:
            f.write(lyrics)
        logger.info(f"Saved song: {song_name} ({file_path})")
        return file_path

    def load_song_from_json(self, json_path: Path) -> dict[str, str]:
        """Load multiple songs from a JSON file.

        Expected format: {"songs": [{"name": "...", "lyrics": "..."}]}

        Args:
            json_path: Path to JSON file

        Returns:
            Dict of {song_name: lyrics}
        """
        with open(json_path) as f:
            data = json.load(f)

        songs = {}
        for song in data.get("songs", []):
            name = song.get("name", "unknown")
            lyrics = song.get("lyrics", "")
            songs[name] = lyrics
            self.save_song(name, lyrics)

        logger.info(f"Loaded {len(songs)} songs from {json_path}")
        return songs
