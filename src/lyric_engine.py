"""Lyric engine — transcribes vocals and decides slide moves.

It composes the two provably-correct cores around noisy STT:

1. `matcher.best_match` — WHICH slide are we on? (handles operator jumps,
   repeated choruses, resync). Position-aware, debounced.
2. `slide_tracker.SlideProgressTracker` — WHEN do we leave the current slide?
   Fires the next slide predictively as the singer reaches the current line's
   tail, so the next slide is up before they finish it.

Plus transcription primed with the song's own lyrics (Whisper `initial_prompt`)
so today's STT is good enough *for this constrained task* — we tell it which
words to expect. Default backend is faster-whisper (MIT, CPU-friendly).
"""

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from loguru import logger

from .config import MatchingConfig, PredictConfig, WhisperConfig
from .matcher import MatcherConfig, best_match, content_words
from .slide_tracker import SlideProgressTracker, TrackerConfig


@dataclass
class Decision:
    """What the engine thinks should happen now."""
    action: str  # "advance" (linear, next slide) | "jump" (non-linear correction)
    index: int  # target slide index
    reason: str
    confidence: float = 1.0


class LyricEngine:
    """Transcribes live vocals and decides slide moves against the loaded song."""

    def __init__(
        self,
        whisper_config: WhisperConfig,
        matching_config: MatchingConfig,
        predict_config: Optional[PredictConfig] = None,
    ):
        self.whisper_config = whisper_config
        self.matching_config = matching_config
        self.predict_config = predict_config or PredictConfig()

        self._model = None       # openai-whisper handle
        self._fw = None          # faster-whisper handle

        self._slides: list[str] = []
        self._current_index: int = 0
        self._transcript_words: list[str] = []
        self._last_move_time: float = 0.0
        self._bias_prompt: str = ""
        self._tracker: Optional[SlideProgressTracker] = None

        self._matcher_cfg = MatcherConfig(
            confidence_threshold=matching_config.confidence_threshold,
            margin=matching_config.margin,
            max_jump=matching_config.max_jump,
        )
        self._tracker_cfg = TrackerConfig(
            trigger_words_from_end=self.predict_config.trigger_words_from_end,
            fuzzy_threshold=self.predict_config.fuzzy_threshold,
            lookahead=self.predict_config.lookahead,
            min_words_for_predict=self.predict_config.min_words_for_predict,
        )

        self._load_model()

    # ---- STT model ---------------------------------------------------------

    def _load_model(self):
        engine = self.whisper_config.engine
        if engine == "faster-whisper":
            try:
                from faster_whisper import WhisperModel
                self._fw = WhisperModel(
                    self.whisper_config.model_size,
                    device="cpu",
                    compute_type=self.whisper_config.compute_type,
                )
                logger.info(f"faster-whisper loaded: {self.whisper_config.model_size}")
                return
            except Exception as e:
                logger.warning(f"faster-whisper unavailable ({e}); falling back to openai-whisper")
        try:
            import whisper
            self._model = whisper.load_model(self.whisper_config.model_size)
            logger.info(f"openai-whisper loaded: {self.whisper_config.model_size}")
        except Exception as e:
            logger.error(f"No STT backend available: {e}")

    # ---- song / answer key -------------------------------------------------

    def load_song(self, slides) -> None:
        """Load the current song's ordered slide texts (the candidate set)."""
        if isinstance(slides, str):
            slides = [ln.strip() for ln in slides.split("\n") if ln.strip()]
        self._slides = list(slides)
        self._current_index = 0
        self._transcript_words = []
        self._last_move_time = 0.0
        self._bias_prompt = self._build_bias_prompt(self._slides)
        self._reset_tracker()
        logger.info(f"Loaded song with {len(self._slides)} slides")

    @staticmethod
    def _build_bias_prompt(slides: list[str]) -> str:
        """Compact Whisper initial_prompt from the song's lyrics (last ~200 words;
        Whisper only consumes ~224 prompt tokens)."""
        words = " ".join(slides).split()
        if len(words) > 200:
            words = words[-200:]
        return " ".join(words)

    def _reset_tracker(self) -> None:
        if self.predict_config.enabled and 0 <= self._current_index < len(self._slides):
            self._tracker = SlideProgressTracker(
                self._slides[self._current_index], self._tracker_cfg
            )
        else:
            self._tracker = None

    def set_current_index(self, index: int) -> None:
        """Re-anchor to the slide ProPresenter is actually showing (operator
        moved, or we just advanced). Resets the predictive tracker for the new
        slide so we start counting its words from zero."""
        if 0 <= index < len(self._slides) and index != self._current_index:
            self._current_index = index
            self._transcript_words = []
            self._reset_tracker()
            logger.debug(f"Position synced to slide {index}")

    # ---- transcription -----------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe an audio chunk, biased toward the loaded song's lyrics."""
        prompt = self._bias_prompt if (self.whisper_config.prime_with_lyrics and self._bias_prompt) else None
        try:
            if self._fw is not None:
                segments, _ = self._fw.transcribe(
                    audio,
                    language=self.whisper_config.language,
                    beam_size=self.whisper_config.beam_size,
                    temperature=self.whisper_config.temperature,
                    initial_prompt=prompt,
                    condition_on_previous_text=False,
                )
                return " ".join(s.text for s in segments).strip()
            if self._model is not None:
                kwargs = dict(
                    language=self.whisper_config.language,
                    temperature=self.whisper_config.temperature,
                    beam_size=self.whisper_config.beam_size,
                    fp16=self.whisper_config.compute_type == "float16",
                )
                if prompt:
                    kwargs["initial_prompt"] = prompt
                return self._model.transcribe(audio, **kwargs).get("text", "").strip()
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
        return ""

    # ---- decision ----------------------------------------------------------

    def process(self, text: str) -> Optional[Decision]:
        """Feed newly-transcribed text; return a slide Decision or None.

        Predictive advance (the tracker) is checked first and is self-limiting —
        it fires at most once per slide, as the current line nears its end. The
        matcher then acts as a correction/safety net: it catches non-linear jumps
        and resyncs, under a debounce to avoid jitter.
        """
        if not self._slides or not text:
            return None

        # Rolling transcript for the matcher.
        self._transcript_words.extend(content_words(text))
        window = self.matching_config.transcript_window_words
        if len(self._transcript_words) > window:
            self._transcript_words = self._transcript_words[-window:]

        # 1) Predictive advance — fire next slide as the current line ends.
        if self._tracker is not None and self._tracker.feed(text):
            nxt = self._current_index + 1
            if nxt < len(self._slides):
                return Decision(
                    action="advance",
                    index=nxt,
                    reason=f"predictive: {self._tracker.pos}/{self._tracker.n} words of current slide",
                )

        # 2) Matcher correction (debounced) — wrong slide / non-linear jump.
        now = time.time()
        if now - self._last_move_time < self.matching_config.debounce_seconds:
            return None
        transcript = " ".join(self._transcript_words)
        sugg = best_match(transcript, self._slides, self._current_index, self._matcher_cfg)
        if sugg and sugg.index != self._current_index:
            action = "advance" if sugg.index == self._current_index + 1 else "jump"
            return Decision(action, sugg.index, sugg.reason, sugg.confidence)
        return None

    def confirm_move(self, index: int) -> None:
        """Record that we (or the operator) moved to `index`: advance the prior,
        start the debounce window, reset the tracker, and clear stale transcript."""
        self._current_index = index
        self._last_move_time = time.time()
        self._transcript_words = []
        self._reset_tracker()

    # ---- introspection -----------------------------------------------------

    def get_current_slide(self) -> Optional[str]:
        if 0 <= self._current_index < len(self._slides):
            return self._slides[self._current_index]
        return None

    @property
    def current_index(self) -> int:
        return self._current_index

    def get_progress(self) -> float:
        if not self._slides:
            return 0.0
        return self._current_index / len(self._slides)
