"""Lyric engine — transcribes vocals and matches against slide-grouped lyrics.

Slides are groups of lyric lines (as they appear in ProPresenter). The engine
tracks the current slide, matches live transcription against a window of
nearby slides, and emits SlideSuggestions that the app either auto-fires or
surfaces for operator confirmation.
"""

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import numpy as np
from loguru import logger

from .config import MatchingConfig, WhisperConfig


@dataclass
class Slide:
    """A ProPresenter slide: one or more lyric lines shown together."""
    index: int
    lines: list[str]
    text: str = ""  # normalized full text, filled in load_song

    @property
    def display(self) -> str:
        return " / ".join(self.lines)


@dataclass
class SlideSuggestion:
    """A proposed slide move."""
    index: int
    confidence: float
    reason: str  # e.g. "next-slide match", "jump +2", "repeat section"


@dataclass
class MatchResult:
    """Result of matching transcribed text against lyrics."""
    matched_line: Optional[Slide] = None
    confidence: float = 0.0
    suggestion: Optional[SlideSuggestion] = None


class LyricEngine:
    """Transcribes live vocals and matches against slide-grouped lyrics."""

    def __init__(self, whisper_config: WhisperConfig, matching_config: MatchingConfig):
        self.whisper_config = whisper_config
        self.matching_config = matching_config
        self._model = None
        self._slides: list[Slide] = []
        self._current_slide: int = -1  # -1 = song not started
        self._last_move_time: float = 0.0
        self._soft_idx: int = -1  # candidate accumulating sub-threshold hits
        self._soft_hits: int = 0
        self._load_whisper()

    # ------------------------------------------------------------------ setup

    def _load_whisper(self):
        """Load faster-whisper model (MIT-licensed CTranslate2 backend)."""
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.whisper_config.model_size,
                device="cpu",
                compute_type=self.whisper_config.compute_type,
                # Cap worker threads: an uncapped CTranslate2 saturates every
                # core, starving coreaudiod's real-time threads → audible static.
                cpu_threads=2,
                num_workers=1,
            )
            logger.info(f"faster-whisper model loaded: {self.whisper_config.model_size}")
        except Exception as e:
            logger.error(f"Failed to load faster-whisper: {e}")

    def load_song(self, lyrics_text: str):
        """Load a song. Blank lines separate slides; each slide holds its lines."""
        self._slides = []
        block: list[str] = []
        for raw in lyrics_text.split("\n"):
            line = raw.strip()
            if line:
                block.append(line)
            elif block:
                self._add_slide(block)
                block = []
        if block:
            self._add_slide(block)
        self._current_slide = -1
        self._last_move_time = 0.0
        logger.info(f"Loaded song with {len(self._slides)} slides")

    def _add_slide(self, lines: list[str]):
        s = Slide(index=len(self._slides), lines=list(lines))
        s.text = self._normalize(" ".join(lines))
        self._slides.append(s)

    # ------------------------------------------------------------- transcribe

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe audio (mono float32 @16kHz) with faster-whisper."""
        if self._model is None:
            return ""
        try:
            # Normalize: quiet room audio trips Whisper's no-speech gating.
            peak = float(np.abs(audio).max())
            if peak > 1e-4:
                audio = audio * (0.9 / peak)
            segments, _ = self._model.transcribe(
                audio,
                no_speech_threshold=None,
                log_prob_threshold=None,
                language=self.whisper_config.language,
                beam_size=self.whisper_config.beam_size,
                temperature=self.whisper_config.temperature,
                vad_filter=False,  # VAD drops sung vocals over instruments
                condition_on_previous_text=False,
            )
            return " ".join(s.text for s in segments).strip()
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    # ---------------------------------------------------------------- matching

    def match_lyrics(self, transcribed_text: str) -> MatchResult:
        """Match transcription against a window of candidate slides.

        Considers the current slide (singer still on it — no move), the next
        slides within lookahead, and small backward jumps (repeats).
        """
        if not self._slides:
            return MatchResult()

        norm = self._normalize(transcribed_text)
        if len(norm.split()) < self.matching_config.min_words_match:
            return MatchResult()

        now = time.time()
        cur = self._current_slide

        scored: list[tuple[int, float]] = []
        for idx in self._candidate_indices(cur):
            conf = self._score(norm, self._slides[idx])
            # Slight bias toward the immediate next slide — the common case —
            # and a growing penalty on jumps, so near-identical repeated
            # sections (choruses) resolve to the closest forward copy.
            if idx == cur + 1:
                conf += self.matching_config.next_slide_bias
            elif idx > cur + 1:
                conf -= self.matching_config.next_slide_bias * (idx - cur - 1)
            scored.append((idx, conf))

        logger.debug(
            "window '{}' | cur {} | {}",
            norm[:80], cur, " ".join(f"{i}:{c:.2f}" for i, c in scored),
        )

        best_idx, best_conf = -1, 0.0
        if scored:
            top = max(c for _, c in scored)
            # Earliest candidate within epsilon of the top score — repeated
            # sections make later copies score the same; nearest wins.
            for idx, conf in scored:
                if conf >= top - self.matching_config.tie_epsilon:
                    best_idx, best_conf = idx, conf
                    break

        m = self.matching_config
        threshold = m.confidence_threshold
        if best_idx > cur + 1 and cur >= 0:
            threshold += m.jump_margin

        if best_idx >= 0 and best_conf < threshold and best_conf >= m.soft_threshold:
            # Sub-threshold but plausible: fire anyway if the same candidate
            # keeps winning consecutive windows (noisy sections stay matched).
            if best_idx == self._soft_idx:
                self._soft_hits += 1
            else:
                self._soft_idx, self._soft_hits = best_idx, 1
            needed = m.soft_hits_step if best_idx <= cur + 1 else m.soft_hits_jump
            if self._soft_hits >= needed:
                threshold = best_conf  # accept
        elif best_conf >= threshold:
            pass
        else:
            self._soft_idx, self._soft_hits = -1, 0

        if best_idx < 0 or best_conf < threshold:
            return MatchResult(confidence=best_conf)
        self._soft_idx, self._soft_hits = -1, 0

        slide = self._slides[best_idx]
        result = MatchResult(matched_line=slide, confidence=min(best_conf, 1.0))

        if best_idx == cur:
            return result  # already showing the right slide

        if now - self._last_move_time < self.matching_config.debounce_seconds:
            return result  # matched, but too soon to move again

        step = best_idx - cur
        if step == 1 or cur < 0:
            reason = "next-slide match"
        elif step > 1:
            reason = f"jump +{step}"
        else:
            reason = f"repeat (back {-step})"
        result.suggestion = SlideSuggestion(index=best_idx, confidence=result.confidence, reason=reason)
        return result

    def _candidate_indices(self, cur: int) -> list[int]:
        m = self.matching_config
        if cur < 0:  # song not started: allow entry anywhere in the first window
            return list(range(0, min(len(self._slides), m.lookahead_slides)))
        # Widen the window when stalled so a missed section (instrumental,
        # crowd noise) doesn't strand the matcher behind the singer.
        extra = 0
        if self._last_move_time > 0:
            stalled = time.time() - self._last_move_time
            extra = min(6, max(0, int((stalled - 20) / 8)))
        lo = max(0, cur - m.lookbehind_slides)
        hi = min(len(self._slides), cur + 1 + m.lookahead_slides + extra)
        return list(range(lo, hi))

    def _score(self, norm_transcribed: str, slide: Slide) -> float:
        """Confidence that the transcription contains the slide's lyrics."""
        t_words = norm_transcribed.split()
        s_words = slide.text.split()
        if not s_words or not t_words:
            return 0.0

        t_set = set(t_words)
        word_ratio = sum(1 for w in s_words if w in t_set) / len(s_words)

        seq_ratio = SequenceMatcher(None, norm_transcribed, slide.text).ratio()
        # The rolling buffer often holds the previous slide too — also score the tail.
        tail = " ".join(t_words[-max(len(s_words) + 4, 8):])
        seq_tail = SequenceMatcher(None, tail, slide.text).ratio()

        return word_ratio * 0.6 + max(seq_ratio, seq_tail) * 0.4

    # ------------------------------------------------------------------ state

    def confirm_move(self, index: int):
        """Record that a suggested move was executed (auto or by operator)."""
        if 0 <= index < len(self._slides):
            self._current_slide = index
            self._last_move_time = time.time()
            logger.debug(f"Now on slide {index}: '{self._slides[index].display[:60]}'")

    def get_current_slide(self) -> int:
        return self._current_slide

    def get_current_line(self) -> Optional[Slide]:
        """The next expected slide (for status display)."""
        nxt = self._current_slide + 1
        if 0 <= nxt < len(self._slides):
            return self._slides[nxt]
        return None

    def get_slides(self) -> list[Slide]:
        return self._slides

    def get_progress(self) -> float:
        if not self._slides:
            return 0.0
        return max(0, self._current_slide + 1) / len(self._slides)

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text
