from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .base import ServiceClient


@dataclass(frozen=True)
class AsrResult:
    text: str
    language: Optional[str] = None
    segments: list = field(default_factory=list)


def encode_wav(audio_int16: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(np.asarray(audio_int16, dtype="<i2").tobytes())
    return buf.getvalue()


class AsrClient(ServiceClient):
    service = "asr"

    def transcribe(
        self,
        audio_int16: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        *,
        priority: str = "realtime",
        chunk_seconds: Optional[float] = None,
        timeout_s: Optional[float] = None,
    ) -> AsrResult:
        if len(audio_int16) == 0:
            return AsrResult(text="")
        data = {"response_format": "verbose_json", "priority": priority}
        if language:
            data["language"] = language
        if prompt:
            data["prompt"] = prompt
        if chunk_seconds is not None:
            data["chunk_seconds"] = str(chunk_seconds)
        files = {
            "file": ("chunk.wav", encode_wav(audio_int16, sample_rate), "audio/wav")
        }
        payload = self._json(
            self._request(
                "POST",
                "/v1/audio/transcriptions",
                data=data,
                files=files,
                timeout_s=timeout_s,
            )
        )
        return AsrResult(
            text=str(payload.get("text") or "").strip(),
            language=payload.get("language"),
            segments=list(payload.get("segments") or []),
        )
