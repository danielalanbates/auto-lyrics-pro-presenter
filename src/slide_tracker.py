"""Predictive within-slide progress tracker — the *when-to-advance* core.

`matcher.py` decides WHICH slide we are on. This module decides WHEN to leave
the current slide: it aligns the live transcript to the *current* slide's known
words and fires the advance to the next slide as the singer reaches the tail of
the current line — so the next slide is already on screen by the time they
finish the current one (Daniel's "advance as the 2nd-to-last word finishes").

Like the matcher, this has NO dependency on Whisper / audio / ProPresenter so it
can be proven correct in isolation.

How it works
------------
- The current slide's text is tokenised into content words (stopwords dropped —
  "the/and/you" carry no timing signal and are the words STT mangles most).
- Each chunk of transcribed text advances a monotonic pointer: for every heard
  word we look a few words ahead in the slide and, on a fuzzy match, jump the
  pointer past it. Monotonic + small lookahead = robust to mishears, skips, and
  repeated words without ever going backwards.
- We fire exactly once, when the pointer reaches `len - trigger_words_from_end`
  (default 1 → the penultimate content word is done and the last word is
  starting). `trigger_words_from_end=2` fires a word earlier for more lead.

Slides whose content is shorter than `min_words_for_predict` (e.g. a one-word
tag, or an all-stopword line) don't get a prediction — they fall back to the
matcher, which is safer than firing off one or two ambiguous words.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from .matcher import content_words


@dataclass
class TrackerConfig:
    """Tuning for predictive advance."""

    trigger_words_from_end: int = 1  # 1 = fire as the last content word begins
    fuzzy_threshold: float = 0.8  # word similarity (0-1) to count as a match
    lookahead: int = 3  # how far ahead to search for the next expected word
    min_words_for_predict: int = 2  # below this many content words: don't predict


def word_similarity(a: str, b: str) -> float:
    """Similarity of two normalized words in [0,1]. Exact match short-circuits;
    otherwise character-sequence ratio absorbs tense/plural/mishear drift
    (e.g. 'reigns'~'reign', 'savior'~'saviour')."""
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


class SlideProgressTracker:
    """Tracks how far through the current slide the singer is, and fires once
    when the slide is near-complete."""

    def __init__(self, slide_text: str, cfg: Optional[TrackerConfig] = None):
        self.cfg = cfg or TrackerConfig()
        self.words: list[str] = content_words(slide_text)
        self.pos: int = 0  # number of the slide's content words consumed, in order
        self.fired: bool = False

    @property
    def n(self) -> int:
        return len(self.words)

    def progress(self) -> float:
        """Fraction of the slide consumed, in [0,1]."""
        return self.pos / self.n if self.n else 0.0

    @property
    def can_predict(self) -> bool:
        return self.n >= self.cfg.min_words_for_predict

    def _trigger_pos(self) -> int:
        """Pointer value at which we fire."""
        return max(1, self.n - self.cfg.trigger_words_from_end)

    def feed(self, text: str) -> bool:
        """Consume newly transcribed text. Returns True exactly once — on the
        chunk that pushes us to/over the trigger point — and False otherwise."""
        if self.fired or not self.can_predict:
            return False

        for heard in content_words(text):
            # Find the earliest expected word within the lookahead window that
            # this heard word matches; advance the pointer past it.
            upper = min(self.pos + self.cfg.lookahead + 1, self.n)
            for j in range(self.pos, upper):
                if word_similarity(heard, self.words[j]) >= self.cfg.fuzzy_threshold:
                    self.pos = j + 1
                    break

        if self.pos >= self._trigger_pos():
            self.fired = True
            return True
        return False
