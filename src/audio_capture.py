"""Audio capture module — handles live audio input from macOS."""

import queue
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from loguru import logger

from .config import AudioConfig


class AudioCapture:
    """Captures audio from microphone or system audio and streams it in chunks."""

    def __init__(self, config: AudioConfig, callback: Optional[Callable] = None):
        self.config = config
        self.user_callback = callback
        self._audio_queue: queue.Queue = queue.Queue()
        self._stream: Optional[sd.InputStream] = None
        self._running = False
        self._processor_thread: Optional[threading.Thread] = None

    def list_devices(self) -> list[dict]:
        """List available audio input devices."""
        devices = sd.query_devices()
        input_devices = [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]
        return input_devices

    def start(self):
        """Start capturing audio."""
        self._running = True

        device = self.config.device_index
        if device is None:
            default = sd.query_devices(kind="input")
            device = default["index"]
            logger.info(f"Using default input device: {default['name']}")

        # Capture at the device's NATIVE rate: asking CoreAudio to deliver a
        # resampled stream (e.g. 16 kHz on a 48 kHz mic) intermittently yields
        # corrupt buffers (NaN/garbage samples). We decimate ourselves instead.
        native_rate = int(sd.query_devices(device)["default_samplerate"])
        self._decimate = max(1, round(native_rate / self.config.sample_rate))
        capture_rate = self.config.sample_rate * self._decimate
        self._stream = sd.InputStream(
            device=device,
            channels=1,
            samplerate=capture_rate,
            dtype=np.float32,
            callback=self._audio_callback,
            blocksize=int(capture_rate * self.config.chunk_duration),
            # Generous buffering: capture shares the machine with playback and
            # transcription; small buffers underrun and turn into static.
            latency="high",
        )
        self._stream.start()
        logger.info("Audio capture started")

        # Start processor thread
        self._processor_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._processor_thread.start()

    def stop(self):
        """Stop capturing audio."""
        self._running = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
        if self._processor_thread:
            self._processor_thread.join(timeout=5)
        logger.info("Audio capture stopped")

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice when new audio is available."""
        if status:
            logger.warning(f"Audio status: {status}")
        if self._running:
            audio_data = indata[:, 0]  # Mono
            if self._decimate > 1:
                n = len(audio_data) - len(audio_data) % self._decimate
                audio_data = audio_data[:n].reshape(-1, self._decimate).mean(axis=1)
            else:
                audio_data = audio_data.copy()
            self._audio_queue.put(audio_data)

    def _process_loop(self):
        """Process audio chunks and send to callback."""
        buffer = np.zeros(
            int(self.config.sample_rate * self.config.buffer_size), dtype=np.float32
        )

        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
                # If processing fell behind, fold queued chunks in now so we
                # always analyze the freshest audio instead of a backlog.
                while not self._audio_queue.empty():
                    chunk = np.concatenate([chunk, self._audio_queue.get_nowait()])
                chunk = chunk[-len(buffer):]
                # Roll buffer and add new chunk
                chunk_len = len(chunk)
                buffer[:-chunk_len] = buffer[chunk_len:]
                buffer[-chunk_len:] = chunk

                if self.user_callback:
                    self.user_callback(buffer.copy(), self.config.sample_rate)
            except queue.Empty:
                continue
