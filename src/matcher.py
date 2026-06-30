"""Candidate-set lyric matcher — the novel core of the project.

Given the ordered slide texts of the *current* song (the "answer key" pulled
live from ProPresenter) and a rolling transcript of what is being sung right
now, decide which slide best matches — biased toward the slide ProPresenter is
currently showing and the one after it.

This module deliberately has NO dependency on Whisper, audio, or ProPresenter so
it can be unit-tested in isolation. The matcher is the part you can't pull off a
shelf, so it must be provably correct independent of the noisy I/O around it.

Why this is tractable: we are not transcribing blind. We already know the ~15
candidate lines, so the question is only "which known line does this noisy
transcript best match?" — and a position prior keeps us from jumping to a
repeated chorus elsewhere in the song.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Function words carry almost no power to disambiguate between worship slides —
# every line has "the", "and", "you". Stripping them makes recall meaningful.
_STOPWORDS = frozenset(
    """
    the a an and or but of to in on for is are be we you i my your our it that
    this with his her him they them he she as at by from so all your yours me
    """.split()
)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", text).strip()


def content_words(text: str) -> list[str]:
    """Normalized words with stopwords removed."""
    return [w for w in normalize(text).split() if w not in _STOPWORDS]


@dataclass
class MatcherConfig:
    """Tuning knobs for the matcher. Defaults are reasonable starting points;
    tune against recorded services."""

    confidence_threshold: float = 0.45  # min text-match quality to act at all
    margin: float = 0.08  # best must beat runner-up by this (anti-jitter)
    forward_bias: float = 1.0  # position prior for the *next* slide
    current_bias: float = 0.85  # position prior for the *current* slide
    backward_penalty: float = 0.5  # base prior for slides *before* current
    jump_decay: float = 0.6  # per-slide decay for distant jumps
    max_jump: int = 4  # never suggest a jump farther than this many slides


@dataclass
class Suggestion:
    """A proposed slide move. `confidence` is the text-match quality of the
    chosen slide (independent of position prior), so the operator UI can show a
    meaningful number."""

    index: int
    confidence: float
    runner_up: float
    reason: str


def position_weight(i: int, current: int, cfg: MatcherConfig) -> float:
    """Prior in (0, 1]: how plausible is slide `i` given we're showing `current`?

    Shape: next > current > +2 > −1 > +3 > −2 … Forward is favored (songs move
    forward); backward (reprises) is allowed but penalized; distance decays.
    """
    d = i - current
    if d == 0:
        return cfg.current_bias
    if d >= 1:
        return cfg.forward_bias * (cfg.jump_decay ** (d - 1))
    return cfg.backward_penalty * (cfg.jump_decay ** (abs(d) - 1))


def text_score(transcript: str, slide: str) -> float:
    """How well does the slide's text appear in the (noisy) transcript? → [0,1].

    Recall of the slide's content words dominates (0.75): "are this slide's
    distinctive words showing up in what's being sung?" — exactly the constrained
    question that makes sloppy STT good enough. A sequence-similarity term (0.25)
    breaks ties for short slides where word recall is coarse.
    """
    slide_words = content_words(slide)
    if not slide_words:
        return 0.0
    trans_set = set(content_words(transcript))
    if not trans_set:
        return 0.0
    hits = sum(1 for w in slide_words if w in trans_set)
    recall = hits / len(slide_words)
    seq = SequenceMatcher(None, normalize(transcript), normalize(slide)).ratio()
    return 0.75 * recall + 0.25 * seq


def best_match(
    transcript: str,
    slides: list[str],
    current_index: int,
    cfg: Optional[MatcherConfig] = None,
) -> Optional[Suggestion]:
    """Return the best slide to move to, or None to stay put.

    Args:
        transcript: rolling transcript of recent sung audio.
        slides: ordered slide texts of the current song (the candidate set).
                Indices here are the real ProPresenter slide indices.
        current_index: the slide ProPresenter is currently showing.
        cfg: tuning. Defaults if omitted.
    """
    cfg = cfg or MatcherConfig()
    if not slides:
        return None

    scored: list[tuple[float, float, int]] = []  # (combined, text, index)
    for i, slide in enumerate(slides):
        if abs(i - current_index) > cfg.max_jump:
            continue
        ts = text_score(transcript, slide)
        combined = ts * position_weight(i, current_index, cfg)
        scored.append((combined, ts, i))

    if not scored:
        return None
    scored.sort(reverse=True)
    best_combined, best_ts, best_i = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    # Linear-stability bias: if the *next* slide is within `margin` of the best,
    # prefer next. Stops a repeated chorus elsewhere from stealing the jump and
    # keeps behavior stable on the common linear case.
    nxt = current_index + 1
    if best_i != nxt and nxt < len(slides):
        for combined, ts, i in scored:
            if i == nxt and best_combined - combined <= cfg.margin:
                best_combined, best_ts, best_i = combined, ts, nxt
                break

    # Already showing the best slide → nothing to do.
    if best_i == current_index:
        return None
    # Text quality gate (position prior shapes *which* slide; this gates *whether*).
    if best_ts < cfg.confidence_threshold:
        return None
    # Ambiguity gate — unless the winner is just the next slide (linear default).
    if runner_up > 0 and (best_combined - runner_up) < cfg.margin and best_i != nxt:
        return None

    reason = (
        f"slide {best_i}: text={best_ts:.2f} "
        f"prior={position_weight(best_i, current_index, cfg):.2f} "
        f"margin={best_combined - runner_up:.2f}"
    )
    return Suggestion(index=best_i, confidence=best_ts, runner_up=runner_up, reason=reason)
