"""Tests for the predictive slide tracker.

These prove the *when-to-advance* core: it fires as the singer reaches the tail
of the current slide, tolerates noisy/partial transcripts, never fires early or
twice, and declines to predict on too-short slides.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.slide_tracker import SlideProgressTracker, TrackerConfig, word_similarity

LINE = "Amazing grace how sweet the sound"  # content words: amazing grace how sweet sound (n=5; 'the' dropped)


def test_fires_when_penultimate_word_done():
    """Default trigger_words_from_end=1: fires as the LAST content word starts.

    content=[amazing, grace, sweet, sound]; trigger at pos>=3. Feeding through
    'sweet' (pos=3) must fire — 'sound' (the last word) is only just starting.
    """
    t = SlideProgressTracker(LINE)
    assert t.feed("amazing grace") is False  # pos=2, not yet
    assert t.feed("sweet") is True  # pos=3 == n-1 -> fire as 'sound' begins


def test_does_not_fire_early():
    t = SlideProgressTracker(LINE)
    assert t.feed("amazing") is False
    assert t.feed("grace") is False
    assert t.progress() < 1.0


def test_fires_only_once():
    t = SlideProgressTracker(LINE)
    assert t.feed("amazing grace sweet sound") is True
    # further audio (e.g. last word still ringing out) must not re-fire
    assert t.feed("sound sound") is False


def test_noisy_transcript_still_reaches_end():
    """Mishears around the real words must not stop the pointer advancing."""
    t = SlideProgressTracker(LINE)
    # 'amazing' heard, junk, 'grace', junk, 'sweet' -> should reach trigger
    fired = (
        t.feed("amazing uh")
        or t.feed("grace yeah")
        or t.feed("sweet")
    )
    assert fired is True


def test_full_line_in_one_chunk_fires():
    t = SlideProgressTracker(LINE)
    assert t.feed("amazing grace how sweet the sound") is True


def test_earlier_lead_with_words_from_end_2():
    """trigger_words_from_end=2 fires one content-word earlier (more lead)."""
    t = SlideProgressTracker(LINE, TrackerConfig(trigger_words_from_end=2))
    # n=5 [amazing grace how sweet sound]; trigger at pos>=3, i.e. one word
    # earlier than the default (which would need pos>=4).
    assert t.feed("amazing grace") is False  # pos=2
    assert t.feed("how") is True  # pos=3 -> fire (two words from the end)


def test_short_slide_does_not_predict():
    """A one-content-word slide shouldn't fire off a single ambiguous word."""
    t = SlideProgressTracker("Hallelujah")  # n=1 < min_words_for_predict
    assert t.can_predict is False
    assert t.feed("hallelujah") is False


def test_all_stopword_slide_safe():
    t = SlideProgressTracker("you are the one")  # 'one' is content; you/are/the stop
    # n likely 1 ('one') -> no predict, must not crash or fire
    assert t.feed("you are the one") is False


def test_out_of_order_words_do_not_overshoot():
    """Hearing a later word early shouldn't skip the whole line (monotonic +
    bounded lookahead keeps it honest)."""
    t = SlideProgressTracker("I once was lost but now am found")
    # content: once lost now found (n=4). Hearing 'found' first is >lookahead away
    # from pos 0, so it must NOT jump the pointer to the end.
    assert t.feed("found") is False


def test_progress_monotonic_and_bounded():
    t = SlideProgressTracker(LINE)
    p0 = t.progress()
    t.feed("amazing")
    p1 = t.progress()
    assert 0.0 <= p0 <= p1 <= 1.0


def test_word_similarity_basics():
    assert word_similarity("sound", "sound") == 1.0
    assert word_similarity("reigns", "reign") >= 0.8  # tense drift tolerated
    assert word_similarity("sound", "ground") < 0.8  # near-rhyme is NOT a match? check
    assert word_similarity("grace", "xyzqw") < 0.5


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__}  {e}")
        except Exception as e:
            print(f"  ERROR {fn.__name__}  {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
