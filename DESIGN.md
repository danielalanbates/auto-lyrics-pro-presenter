# Auto Lyrics — Design & Research (updated 2026-06-28)

Mission: help under-staffed churches run lyrics with **fewer/no volunteers** by
listening to live worship and advancing ProPresenter slides automatically. Two
behaviours: (1) **display the correct slide** (follow the singing), and (2)
**predictive auto-advance** — fire the next slide as the singer reaches the tail
of the current line, so the next slide is already up before they finish it.

## ✅ VERIFIED LIVE — 2026-06-28 (ProPresenter 21.4, this MacBook)

Everything below was tested end-to-end against the real ProPresenter, not in
theory:

- **Network API:** enabled (Settings → Network → Enable Network). Port is
  **62595** (PP auto-assigns it; the bridge auto-rediscovers via `/version`).
- **The answer-key fork is RESOLVED — Scenario A.** `GET /v1/presentation/active`
  returns **full per-slide text** (`groups[].slides[].text`). No protobuf / `.pro`
  parsing needed. (greyshirtguy's proto repo is therefore *not* a dependency.)
- **Verified endpoints:** `/v1/presentation/active` (answer key),
  `/v1/presentation/slide_index` → `presentation_index.index` (live position),
  `/v1/status/slide` (current+next text), `/v1/presentation/active/{i}/trigger`
  (jump to slide, 204), `/v1/trigger/next|previous` (204).
- **STT:** faster-whisper (MIT) `base` model, CPU/int8, primed with the song's
  lyrics. Transcribed clean TTS lines verbatim.
- **End-to-end (`tools/e2e_live.py`):** TTS each line → faster-whisper → engine →
  ProPresenter. **7/7 slide advances landed correctly**; predictive advance fired
  on **7/7** slides with an **average 1.7-word lead** before the last word.
- **Licensing:** project is **MIT**; runtime deps are all MIT/BSD; ProPresenter
  HTTP uses the Python stdlib (no Apache `requests`).

Still **not** verified (honest): real-room sung audio with a full band — only TTS
was used here. That remains an empirical question for recorded services.

---

(Original v1 framing: a suggest-and-confirm operator assist, with auto-fire as an
opt-in mode once measured accuracy earns it. Auto-fire is now implemented and
verified on clean audio; keep it gated behind real-service testing before trusting
it unattended.)

## The core insight (and why it works)

We are not doing open lyric transcription. We already know the answer key — the
ordered slide text of the current song, pulled live from ProPresenter. So the
problem collapses from *"what words are these?"* to *"which of these ~15 known
lines best matches this noisy partial transcript?"*. Getting 50–60% of sung
words roughly right is plenty to pick the right line out of a tiny candidate set.

That insight is exploited at **two** layers, not one:

1. **STT layer (contextual biasing).** Today's models get dramatically more
   accurate *for this task* when primed with the song's own lyrics as
   `hotwords` / `initial_prompt`. We don't wait for better Whisper — we feed it
   the answer key so it leans toward the words we expect. This is the concrete
   form of Daniel's bet that "understanding gets solved by better audio AI": we
   bias today's model instead of waiting.
2. **Match layer (constrained search).** Fuzzy-match the rolling transcript
   against the candidate set with a **position prior** centered on the slide
   currently showing. Tiny search space + position prior = robust to sloppy STT.

## Honest risk read

The hard part is **not** the matcher (we already had a v1). It's:

- **Audio.** Full band + congregation + monitors through one feed. "50–60% of
  words" is optimistic in a loud room; real-time vocal isolation adds latency.
- **Non-linearity.** Bridges repeated 4×, verses skipped, spontaneous reprises,
  reorders. A forward-only matcher desyncs the instant the leader improvises.

The redesign targets the non-linearity directly (candidate-set + position prior +
operator-sync). Audio quality is an empirical question we answer by testing on
**real recorded services**, not clean studio audio.

## Architecture

```
 mic/feed ─▶ AudioCapture ─▶ [vocal isolation?] ─▶ Streaming STT ─▶ rolling transcript
                                                       ▲                     │
                                   prime: song lyrics  │                     ▼
                                   as hotwords/prompt ──┘            Candidate-set Matcher
                                                                     (position prior, hysteresis)
                                                                              │ suggestion(index, conf)
 ProPresenter ◀── trigger slide by index ◀── suggest-and-confirm / auto-fire ─┘
      │
      └─ /v1/status/slide (chunked) ─▶ current slide index + text  ──▶ keeps matcher in sync
         /v1/presentation/active     ─▶ answer key (full slide text, if exposed)
```

### Answer-key sourcing — the architectural fork

The official PP7 REST API (port 1025) is the sanctioned path. Two ways to get the
candidate set, and we support both because the first is **version-dependent**:

- **Scenario A — full song text available.** `GET /v1/presentation/active` (or
  `/focused`) → presentation id; `GET /v1/presentation/{id}` → ordered groups →
  slides. *If* slide `text` is present, build the full candidate set and do real
  candidate-set matching with real ProPresenter indices. Best case.
- **Scenario B — only current+next.** `GET /v1/status/slide` (chunked stream)
  always gives the **current** and **next** slide text live. If full text isn't
  exposed, degrade to a 2-candidate decision: "has the singing moved onto the
  next slide's text?" Loses non-linear jumps but is rock-solid for linear songs.

**RESOLVED 2026-06-28: Scenario A confirmed.** `GET /v1/presentation/active` on a
real song returns per-slide `text` in full, so we always have the complete answer
key with real ProPresenter indices. The bridge still parses defensively (and the
`/v1/status/slide` current+next stream remains the Scenario-B safety net), but the
strong path is live.

The `/v1/status/slide` chunked stream is the backbone either way: it tells us
which slide is **actually live** (so a human operator moving a slide re-syncs our
position prior instead of fighting us), and supplies current/next text.

### Matcher (`src/matcher.py`) — the novel core

Pure, dependency-free, unit-tested in isolation from audio/STT/PP.

- `text_score(transcript, slide)` — recall of the slide's **content words**
  (stopwords stripped) in the noisy transcript, plus a sequence-similarity
  tie-breaker. Recall dominates because the real question is "are this slide's
  distinctive words showing up?"
- `position_weight(i, current)` — prior in (0,1]: `next > current > +2 > −1 …`,
  asymmetric (forward favored), decaying with distance, capped at `max_jump`.
- Decision: combined = text × prior; require best ≥ `confidence_threshold`,
  beat runner-up by `margin`, and not equal current. A **linear-stability bias**
  prefers the next slide when it's within `margin` of the best, so a repeated
  chorus elsewhere can't steal the jump.

## Predictive auto-advance (`src/slide_tracker.py`) — the WHEN core

The matcher answers *which* slide; the tracker answers *when to leave it*. Given
the current slide's known words, it advances a monotonic pointer as the singer's
transcribed words arrive (fuzzy match within a small lookahead, so mishears and
skips don't stall it), and fires the next slide exactly once when the pointer
reaches `len − trigger_words_from_end` (default 1 → as the **last content word
begins**, i.e. the 2nd-to-last word just finished — Daniel's spec). Pure and
unit-tested (11 tests). Slides too short to predict from defer to the matcher.

Engine flow per transcript chunk (`src/lyric_engine.py::process`): predictive
tracker first (self-limiting, once per slide) → matcher second (debounced, for
non-linear jumps / resync). On any move, the tracker resets to the new slide.

## STT decision (settled: faster-whisper, MIT)

Target machine: Daniel's Apple-Silicon MacBook.

- **Chosen: faster-whisper** (MIT, CTranslate2). No PyTorch, runs on CPU/int8,
  supports lyric priming via `initial_prompt`. Picked over **WhisperLiveKit**
  specifically because WhisperLiveKit is **Apache-2.0** and the project is
  MIT-only; faster-whisper gives the same Whisper quality under MIT. Verified
  transcribing primed TTS lines verbatim.
- **Alternative backend (also MIT):** openai-whisper, selectable via
  `WhisperConfig.engine="whisper"` (pulls in torch).
- **Phase 2 / native:** Apple `SpeechAnalyzer` / `SpeechTranscriber` (on-device,
  streaming, `contextualStrings` biasing). Lowest latency, Swift-only — the right
  long-term native path.

Current code transcribes audio **chunks** (not yet token-streaming partials).
True streaming (LocalAgreement, à la ufal/whisper_streaming — MIT) is the Phase-2
latency upgrade; the matcher + tracker are independent of it.

## Phased plan

1. **Matcher redesign** ✅ candidate-set + position prior + hysteresis, unit-tested.
2. **Live answer key** — bridge methods to pull active presentation + slide status;
   verify Scenario A vs B against the real install (needs Network API on).
3. **Wire it** — `main.py` builds the candidate set from PP, tracks current index
   from the status stream, calls `go_to_slide(index)`; suggest-vs-auto-fire toggle.
4. **Lyric-primed STT** — swap batch Whisper for streaming + feed lyrics as
   hotwords/initial_prompt.
5. **Measure on real recorded services** before enabling auto-fire.

## How to run / re-verify

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# In ProPresenter: Settings → Network → Enable Network (port auto-detected).
# Have a song open and a slide live.
python -m pytest tests/ -q                 # 22 unit tests (matcher + tracker)
python tools/e2e_live.py                   # live end-to-end against ProPresenter
python -m src.main                         # run for real (mic). --song NAME optional.
```

`tools/make_test_song.py` recreates the 8-slide test presentation via
ProPresenter's "Import → Text from Clipboard" if you need a fresh one.

## Remaining work (honest)

- **Real-room audio.** Everything is verified on clean TTS, not a live band +
  congregation through one feed. This is the actual make-or-break and needs
  recorded-service testing.
- **Streaming partials.** Move from chunked transcription to token-streaming for
  lower latency (Phase 2).
- **Non-linear live test.** The matcher's repeated-chorus/jump handling is proven
  in unit tests; not yet exercised live (the test song has 8 distinct lines).
