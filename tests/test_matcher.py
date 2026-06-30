"""Tests for the candidate-set matcher.

These prove the novel core works against the hard cases — non-linearity, noisy
transcripts, repeated choruses — with zero dependency on ProPresenter or audio.
Run: python -m pytest tests/test_matcher.py  (or: python tests/test_matcher.py)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matcher import MatcherConfig, best_match, position_weight, text_score

# A small song with a repeated chorus — the classic non-linear trap.
SONG = [
    "Amazing grace how sweet the sound",          # 0  verse 1a
    "That saved a wretch like me",                # 1  verse 1b
    "I once was lost but now am found",           # 2  verse 2a
    "Was blind but now I see",                    # 3  verse 2b
    "My chains are gone I've been set free",      # 4  chorus a
    "My God my Savior has ransomed me",           # 5  chorus b
    "When we've been there ten thousand years",   # 6  verse 3a
    "Bright shining as the sun",                  # 7  verse 3b
    "My chains are gone I've been set free",      # 8  chorus a (REPEAT of 4)
    "My God my Savior has ransomed me",           # 9  chorus b (REPEAT of 5)
]


def test_linear_advance_to_next():
    """Clean singing of the next line advances exactly one slide."""
    s = best_match("that saved a wretch like me", SONG, current_index=0)
    assert s is not None and s.index == 1


def test_noisy_transcript_still_matches():
    """50-60% of words right is enough — the answer key does the rest."""
    # "lost" and "found" survive; filler/mishears around them.
    s = best_match("i once a lost uh but now i am found yeah", SONG, current_index=1)
    assert s is not None and s.index == 2


def test_stay_put_when_still_on_current_line():
    """Re-hearing the current line must NOT advance."""
    s = best_match("amazing grace how sweet the sound", SONG, current_index=0)
    assert s is None


def test_repeated_chorus_does_not_steal_jump():
    """Singing the chorus near its FIRST occurrence picks 4, not the identical 8.

    This is the whole point of the position prior: text alone can't tell 4 from 8.
    """
    s = best_match("my chains are gone ive been set free", SONG, current_index=3)
    assert s is not None and s.index == 4  # local copy, not the far repeat


def test_repeated_chorus_picks_far_copy_when_local():
    """Near the SECOND chorus, the same words should resolve to 8, not 4."""
    s = best_match("my chains are gone ive been set free", SONG, current_index=7)
    assert s is not None and s.index == 8


def test_garbage_transcript_suggests_nothing():
    s = best_match("la la la mumble hey oh", SONG, current_index=2)
    assert s is None


def test_empty_inputs_safe():
    assert best_match("", SONG, 0) is None
    assert best_match("anything", [], 0) is None


def test_max_jump_blocks_wild_leap():
    """A line far ahead shouldn't be jumped to from the top of the song."""
    cfg = MatcherConfig(max_jump=3)
    s = best_match("bright shining as the sun", SONG, current_index=0, cfg=cfg)
    assert s is None  # slide 7 is 7 away > max_jump


def test_backward_reprise_allowed_when_strong():
    """A genuine reprise back to the chorus is allowed (penalized, not forbidden)."""
    cfg = MatcherConfig(max_jump=6)
    s = best_match("my chains are gone ive been set free", SONG, current_index=7, cfg=cfg)
    assert s is not None  # resolves to a chorus copy (8), proving backward isn't blocked outright


def test_position_weight_ordering():
    """next > current > +2 > -1 — the prior's defining shape."""
    cfg = MatcherConfig()
    nxt = position_weight(5, 4, cfg)
    cur = position_weight(4, 4, cfg)
    fwd2 = position_weight(6, 4, cfg)
    back1 = position_weight(3, 4, cfg)
    assert nxt > cur > fwd2 > back1


def test_text_score_recall_dominates():
    full = text_score("amazing grace how sweet the sound", "Amazing grace how sweet the sound")
    half = text_score("amazing grace", "Amazing grace how sweet the sound")
    none = text_score("totally different words here", "Amazing grace how sweet the sound")
    assert full > half > none
    assert full > 0.9


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
