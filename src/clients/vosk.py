"""rt-vosk client: the rt-asr request shape, answered with Vosk's words.

``POST /v1/audio/transcriptions`` (multipart ``file`` + ``language``) ->
``{"text", "language", "words": [{"word", "conf", "start", "end"}]}``. Vosk
cannot detect the language, so ``language`` is always sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .asr import encode_wav
from .base import ServiceClient


@dataclass(frozen=True)
class VoskWord:
    word: str
    conf: float
    start: float  # seconds from the start of the sent audio
    end: float


@dataclass(frozen=True)
class VoskResult:
    text: str
    language: Optional[str] = None
    words: tuple[VoskWord, ...] = ()


def _word(d: dict) -> VoskWord:
    return VoskWord(
        word=str(d.get("word") or ""),
        conf=float(d.get("conf") or 0.0),
        start=float(d.get("start") or 0.0),
        end=float(d.get("end") or 0.0),
    )


class VoskClient(ServiceClient):
    service = "vosk"

    def transcribe(
        self,
        audio_int16: np.ndarray,
        language: str,
        sample_rate: int = 16000,
        *,
        timeout_s: Optional[float] = None,
    ) -> VoskResult:
        if len(audio_int16) == 0:
            return VoskResult(text="", language=language)
        files = {
            "file": ("chunk.wav", encode_wav(audio_int16, sample_rate), "audio/wav")
        }
        payload = self._json(
            self._request(
                "POST",
                "/v1/audio/transcriptions",
                data={"language": language},
                files=files,
                timeout_s=timeout_s,
            )
        )
        words = payload.get("words") or []
        return VoskResult(
            text=str(payload.get("text") or "").strip(),
            language=payload.get("language") or language,
            words=tuple(_word(w) for w in words if isinstance(w, dict)),
        )
