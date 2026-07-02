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
    buffer_size: int = 12  # seconds of audio to keep in buffer (≥ one slide span)


@dataclass
class WhisperConfig:
    """Whisper transcription settings."""
    model_size: str = "small.en"  # faster-whisper: tiny.en, base.en, small.en, distil-small.en...
    language: str = "en"
    compute_type: str = "int8"  # int8 keeps small.en real-time on CPU
    beam_size: int = 1
    temperature: float = 0.0


@dataclass
class ProPresenterConfig:
    """ProPresenter connection settings."""
    host: str = "127.0.0.1"
    osc_port: int = 53000  # Default ProPresenter OSC port
    http_port: int = 53001  # Default ProPresenter HTTP API port
    password: Optional[str] = None
    use_osc: bool = True
    use_http: bool = False


@dataclass
class MatchingConfig:
    """Lyric matching settings."""
    confidence_threshold: float = 0.55  # Minimum confidence to trigger slide change
    min_words_match: int = 3  # Minimum transcribed words before matching
    debounce_seconds: float = 2.5  # Minimum time between slide advances
    auto_fire: bool = True  # Fire slide changes automatically vs. suggest-and-confirm
    lookahead_slides: int = 3  # How many slides ahead to consider
    # Forward-only by default: slide decks built in performance order encode
    # repeats as sequential slides, and back-jumps cause chorus oscillation.
    lookbehind_slides: int = 0
    next_slide_bias: float = 0.08  # Score bonus for the expected next slide


@dataclass
class AppConfig:
    """Main application configuration."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    propresenter: ProPresenterConfig = field(default_factory=ProPresenterConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
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

    # Environment variables override everything
    if os.getenv("PP_HOST"):
        config.propresenter.host = os.getenv("PP_HOST", config.propresenter.host)
    if os.getenv("PP_OSC_PORT"):
        config.propresenter.osc_port = int(os.getenv("PP_OSC_PORT", config.propresenter.osc_port))
    if os.getenv("LOG_LEVEL"):
        config.log_level = os.getenv("LOG_LEVEL", config.log_level)

    return config
