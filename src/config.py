"""Configuration management for Auto Lyrics Pro Presenter."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AudioConfig:
    """Audio capture settings."""
    device_index: Optional[int] = None  # None = default input
    sample_rate: int = 16000  # Whisper expects 16kHz
    chunk_duration: float = 3.0  # seconds of audio to process at once
    buffer_size: int = 10  # seconds of audio to keep in buffer


@dataclass
class WhisperConfig:
    """Speech-to-text settings.

    Default engine is faster-whisper (MIT, CTranslate2 backend) — no PyTorch, runs
    well on Apple-Silicon CPU, and supports lyric priming via `initial_prompt`.
    """
    engine: str = "faster-whisper"  # "faster-whisper" (MIT) | "whisper" (openai, MIT)
    model_size: str = "large-v3"  # tiny, base, small, medium, large, large-v3
    language: str = "en"
    compute_type: str = "int8"  # int8 = fast on CPU; float16 for GPU
    beam_size: int = 5
    temperature: float = 0.0
    prime_with_lyrics: bool = True  # feed the song's lyrics as initial_prompt (contextual biasing)


@dataclass
class ProPresenterConfig:
    """ProPresenter connection settings."""
    host: str = "127.0.0.1"
    # ProPresenter assigns this in Settings → Network → Port (62595 on this Mac).
    # The bridge re-discovers it automatically if it ever changes.
    port: int = 62595
    password: Optional[str] = None  # Network password, if set in ProPresenter


@dataclass
class PredictConfig:
    """Predictive auto-advance: fire the NEXT slide as the singer reaches the
    tail of the CURRENT slide, so the next line is up before they finish."""
    enabled: bool = True
    trigger_words_from_end: int = 1  # 1 = fire as the last content word begins
    fuzzy_threshold: float = 0.8  # word match similarity (0-1)
    lookahead: int = 3  # words ahead to search when aligning
    min_words_for_predict: int = 2  # shorter slides defer to the matcher


@dataclass
class MatchingConfig:
    """Lyric matching settings (consumed by the candidate-set matcher)."""
    confidence_threshold: float = 0.45  # min text-match quality of a slide to act
    margin: float = 0.08  # winner must beat runner-up by this (anti-jitter)
    max_jump: int = 4  # never auto-jump farther than this many slides
    debounce_seconds: float = 2.0  # min time between slide moves
    transcript_window_words: int = 14  # rolling transcript length kept for matching
    auto_fire: bool = False  # False = suggest-and-confirm (operator taps); True = autopilot
    use_propresenter_answer_key: bool = True  # pull slide text live from PP, not .txt files


@dataclass
class AppConfig:
    """Main application configuration."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    propresenter: ProPresenterConfig = field(default_factory=ProPresenterConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    predict: PredictConfig = field(default_factory=PredictConfig)
    songs_directory: Path = field(default_factory=lambda: Path.home() / "songs")
    log_level: str = "INFO"


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from YAML file or environment variables."""
    config = AppConfig()

    if config_path and config_path.exists():
        import yaml
        with open(config_path) as f:
            data = yaml.safe_load(f)
        # Override defaults with loaded config
        if "audio" in data:
            for k, v in data["audio"].items():
                setattr(config.audio, k, v)
        if "whisper" in data:
            for k, v in data["whisper"].items():
                setattr(config.whisper, k, v)
        if "propresenter" in data:
            for k, v in data["propresenter"].items():
                setattr(config.propresenter, k, v)
        if "matching" in data:
            for k, v in data["matching"].items():
                setattr(config.matching, k, v)
        if "predict" in data:
            for k, v in data["predict"].items():
                setattr(config.predict, k, v)

    # Environment variables override everything
    if os.getenv("PP_HOST"):
        config.propresenter.host = os.getenv("PP_HOST", config.propresenter.host)
    if os.getenv("PP_PORT"):
        config.propresenter.port = int(os.getenv("PP_PORT", config.propresenter.port))
    if os.getenv("PP_PASSWORD"):
        config.propresenter.password = os.getenv("PP_PASSWORD")
    if os.getenv("LOG_LEVEL"):
        config.log_level = os.getenv("LOG_LEVEL", config.log_level)

    return config
