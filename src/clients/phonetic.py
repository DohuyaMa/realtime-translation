"""rt-phonetic client: the rt-asr request shape, answered with the letters a
character-level CTC model heard.

``POST /v1/audio/transcriptions`` (multipart ``file`` = 16 kHz mono wav +
``language``) -> ``{"text": "<lowercase letters, stress marks stripped>",
"language", "words": [{"word", "start", "end"}], "duration"}``; word times
are seconds from the start of the sent audio; silence -> ``"text": ""``. A
language without a loaded model -> HTTP 409 ``model_language_mismatch``
(``ServiceError`` kind ``wrong_language``). The model cannot detect the
language, so ``language`` is always sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .asr import encode_wav
from .base import ServiceClient


@dataclass(frozen=True)
class PhoneticWord:
    word: str
    start: float  # seconds from the start of the sent audio
    end: float


@dataclass(frozen=True)
class PhoneticResult:
    text: str
    language: Optional[str] = None
    words: tuple[PhoneticWord, ...] = ()
    duration: Optional[float] = None


def _word(d: dict) -> PhoneticWord:
    return PhoneticWord(
        word=str(d.get("word") or ""),
        start=float(d.get("start") or 0.0),
        end=float(d.get("end") or 0.0),
    )


class PhoneticClient(ServiceClient):
    service = "phonetic"

    def transcribe(
        self,
        audio_int16: np.ndarray,
        language: str,
        sample_rate: int = 16000,
        *,
        timeout_s: Optional[float] = None,
    ) -> PhoneticResult:
        if len(audio_int16) == 0:
            return PhoneticResult(text="", language=language, duration=0.0)
        files = {
            "file": ("unit.wav", encode_wav(audio_int16, sample_rate), "audio/wav")
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
        duration = payload.get("duration")
        return PhoneticResult(
            text=str(payload.get("text") or "").strip(),
            language=payload.get("language") or language,
            words=tuple(_word(w) for w in words if isinstance(w, dict)),
            duration=None if duration is None else float(duration),
        )
