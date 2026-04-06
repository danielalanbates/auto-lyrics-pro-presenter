"""Lyric engine — transcribes vocals and matches against loaded lyrics."""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from loguru import logger

from .config import MatchingConfig, WhisperConfig


@dataclass
class LyricLine:
    """A single line of lyrics with timing metadata."""
    text: str
    line_number: int
    matched: bool = False
    match_confidence: float = 0.0
    matched_at: Optional[float] = None  # Timestamp when matched


@dataclass
class MatchResult:
    """Result of matching transcribed text against lyrics."""
    matched_line: Optional[LyricLine] = None
    confidence: float = 0.0
    matched_words: int = 0
    total_words: int = 0


class LyricEngine:
    """Transcribes live vocals and matches against loaded song lyrics."""

    def __init__(self, whisper_config: WhisperConfig, matching_config: MatchingConfig):
        self.whisper_config = whisper_config
        self.matching_config = matching_config
        self._model = None
        self._current_song_lines: list[LyricLine] = []
        self._current_line_index: int = 0
        self._last_match_time: float = 0

        self._load_whisper()

    def _load_whisper(self):
        """Load Whisper model."""
        try:
            import whisper
            self._model = whisper.load_model(self.whisper_config.model_size)
            logger.info(f"Whisper model loaded: {self.whisper_config.model_size}")
        except Exception as e:
            logger.error(f"Failed to load Whisper: {e}")

    def load_song(self, lyrics_text: str):
        """Load a song's lyrics for matching.
        
        Args:
            lyrics_text: Full lyrics text, one line per line
        """
        self._current_song_lines = [
            LyricLine(text=line.strip(), line_number=i)
            for i, line in enumerate(lyrics_text.split("\n"))
            if line.strip()
        ]
        self._current_line_index = 0
        logger.info(f"Loaded song with {len(self._current_song_lines)} lyric lines")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe audio using Whisper.
        
        Args:
            audio: Audio array (mono, 16kHz)
            sample_rate: Sample rate
            
        Returns:
            Transcribed text
        """
        if self._model is None:
            return ""

        try:
            result = self._model.transcribe(
                audio,
                language=self.whisper_config.language,
                temperature=self.whisper_config.temperature,
                beam_size=self.whisper_config.beam_size,
                fp16=self.whisper_config.compute_type == "float16",
            )
            return result.get("text", "").strip()
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    def match_lyrics(self, transcribed_text: str) -> MatchResult:
        """Match transcribed text against the next expected lyric line.
        
        Uses fuzzy word matching to handle transcription errors.
        
        Args:
            transcribed_text: Text from Whisper
            
        Returns:
            MatchResult with confidence and matched line
        """
        if not self._current_song_lines or self._current_line_index >= len(self._current_song_lines):
            return MatchResult()

        # Check debounce
        now = time.time()
        if now - self._last_match_time < self.matching_config.debounce_seconds:
            return MatchResult()

        expected_line = self._current_song_lines[self._current_line_index]
        result = self._fuzzy_match(transcribed_text, expected_line.text)

        if result.confidence >= self.matching_config.confidence_threshold:
            expected_line.matched = True
            expected_line.match_confidence = result.confidence
            expected_line.matched_at = now
            self._current_line_index += 1
            self._last_match_time = now
            logger.info(
                f"Matched line {expected_line.line_number}: '{expected_line.text[:50]}...' "
                f"(confidence: {result.confidence:.2f})"
            )

        return result

    def _fuzzy_match(self, transcribed: str, expected: str) -> MatchResult:
        """Fuzzy match transcribed text against expected lyrics.
        
        Returns confidence based on word overlap and sequence similarity.
        """
        transcribed_words = set(self._normalize(transcribed).split())
        expected_words = self._normalize(expected).split()

        if not expected_words:
            return MatchResult(confidence=0.0)

        matched = sum(1 for w in expected_words if w in transcribed_words)
        total = len(expected_words)
        word_ratio = matched / total

        # Also check sequence similarity for short phrases
        from difflib import SequenceMatcher
        seq_ratio = SequenceMatcher(None, self._normalize(transcribed), self._normalize(expected)).ratio()

        # Combine: word overlap matters more, but sequence helps
        confidence = (word_ratio * 0.7) + (seq_ratio * 0.3)

        return MatchResult(
            confidence=confidence,
            matched_words=matched,
            total_words=total,
        )

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for comparison."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)  # Remove punctuation
        text = re.sub(r"\s+", " ", text)  # Collapse whitespace
        return text

    def get_current_line(self) -> Optional[LyricLine]:
        """Get the next expected lyric line."""
        if self._current_line_index < len(self._current_song_lines):
            return self._current_song_lines[self._current_line_index]
        return None

    def get_progress(self) -> float:
        """Get song progress (0.0 to 1.0)."""
        if not self._current_song_lines:
            return 0.0
        return self._current_line_index / len(self._current_song_lines)
