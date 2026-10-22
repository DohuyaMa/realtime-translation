from __future__ import annotations

import io
import wave
from typing import Optional, Tuple

import numpy as np

from .base import ServiceClient, ServiceError


def decode_wav(data: bytes) -> Tuple[np.ndarray, int]:
    """PCM16 WAV -> mono float32 in [-1, 1] and its sample rate."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"expected PCM16 WAV, got sample width {w.getsampwidth()} bytes")
        channels = w.getnchannels()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.float32)
    return audio, rate


class TtsClient(ServiceClient):
    service = "tts"

    def synthesize(
        self, text: str, voice: Optional[str] = None, speed: Optional[float] = None
    ) -> Tuple[np.ndarray, int]:
        body: dict = {"input": text, "response_format": "wav"}
        if voice:
            body["voice"] = voice
        if speed is not None:
            body["speed"] = float(speed)
        resp = self._request("POST", "/v1/audio/speech", json=body)
        try:
            audio, rate = decode_wav(resp.content)
        except (wave.Error, ValueError, EOFError) as e:
            raise ServiceError(self.service, "http", f"invalid WAV from TTS: {e}") from e
        header = resp.headers.get("x-sample-rate")
        if header and header.isdigit():
            rate = int(header)
        return audio, rate
