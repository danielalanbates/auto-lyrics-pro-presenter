"""Vocal isolation module — separates vocals from music/instruments."""

from typing import Optional

import numpy as np
from loguru import logger


class VocalIsolator:
    """Separates vocals from audio using AI source separation.
    
    Supports multiple backends:
    - Demucs (Facebook) — Best quality, heavier
    - Spleeter (Deezer) — Faster, lighter
    - Simple spectral gating — Fastest, lower quality
    """

    def __init__(self, backend: str = "demucs"):
        self.backend = backend
        self._model = None
        self._load_model()

    def _load_model(self):
        """Load the source separation model."""
        if self.backend == "demucs":
            try:
                import demucs.pretrained
                self._model = demucs.pretrained.get_model("htdemucs")
                logger.info("Demucs model loaded (htdemucs)")
            except Exception as e:
                logger.warning(f"Failed to load Demucs: {e}. Falling back to spectral gating.")
                self.backend = "spectral"
        elif self.backend == "spleeter":
            try:
                from spleeter.separator import Separator
                self._model = Separator("spleeter:2stems")
                logger.info("Spleeter model loaded")
            except Exception as e:
                logger.warning(f"Failed to load Spleeter: {e}. Falling back to spectral gating.")
                self.backend = "spectral"
        else:
            logger.info("Using spectral gating for vocal isolation")

    def isolate_vocals(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Extract vocal component from audio.
        
        Args:
            audio: Input audio array (mono, float32, -1 to 1)
            sample_rate: Sample rate in Hz
            
        Returns:
            Vocal-only audio array
        """
        if self.backend == "demucs":
            return self._isolate_demucs(audio, sample_rate)
        elif self.backend == "spleeter":
            return self._isolate_spleeter(audio, sample_rate)
        else:
            return self._isolate_spectral(audio, sample_rate)

    def _isolate_demucs(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Use Demucs for source separation."""
        # Demucs expects stereo input and returns separated sources
        # This is a placeholder — full implementation needs batching logic
        try:
            import torch
            from demucs.apply import apply_model
            from demucs.audio import convert_audio

            # Convert to tensor
            wav = torch.from_numpy(audio).float().unsqueeze(0).unsqueeze(0)  # (1, 1, samples)
            wav = convert_audio(wav, sample_rate, self._model.samplerate, self._model.audio_channels)
            
            # Run separation
            with torch.no_grad():
                sources = apply_model(self._model, wav)[0]
            
            # Vocals are typically one of the sources (index varies by model)
            # htdemucs: drums, bass, other, vocals
            vocals = sources[3].mean(dim=0).numpy()  # Average stereo to mono
            return vocals
        except Exception as e:
            logger.error(f"Demucs isolation failed: {e}")
            return audio  # Return original on failure

    def _isolate_spleeter(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Use Spleeter for source separation."""
        try:
            # Spleeter expects stereo
            stereo = np.stack([audio, audio], axis=-1)
            result = self._model.separate(stereo)
            return result["vocals"][:, 0]  # Return mono vocal
        except Exception as e:
            logger.error(f"Spleeter isolation failed: {e}")
            return audio

    def _isolate_spectral(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Simple spectral gating — fast but lower quality.
        
        Uses the fact that vocals are typically in 300Hz-3kHz range.
        """
        from scipy import signal

        # Bandpass filter for vocal range
        lowcut = 300.0
        highcut = 3000.0
        nyquist = sample_rate / 2.0
        low = lowcut / nyquist
        high = highcut / nyquist

        b, a = signal.butter(4, [low, high], btype="band")
        filtered = signal.filtfilt(b, a, audio)
        return filtered
