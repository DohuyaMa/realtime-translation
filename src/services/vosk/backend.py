"""rt-vosk backend interface, result types and the fake backend."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

import numpy as np

from ..common.model_store import Progress
from .settings import VoskSettings

__all__ = [
    "FakeRecognizer",
    "FakeVoskBackend",
    "Recognizer",
    "Transcript",
    "VoskBackend",
    "Word",
    "make_backend",
]


@dataclass(frozen=True)
class Word:
    word: str
    conf: float  # Kaldi word posterior, 0..1
    start: float  # seconds
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    words: Tuple[Word, ...]


class VoskBackend(Protocol):
    name: str
    model_id: str
    language: str  # of the (single) model

    def fetch(self, progress: Optional[Progress] = None) -> None:
        """Provision the model files (may download; no timeout)."""
        ...

    def load(self) -> None:
        """Load the fetched models (bounded by the load timeout)."""
        ...

    def unload(self) -> None:
        """Free the models now."""
        ...

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        """float32 mono 16 kHz -> words with per-word confidence."""
        ...

    def recognizer(self) -> "Recognizer":
        """A fresh streaming recognizer (one per WS session)."""
        ...


class Recognizer(Protocol):
    """The part of ``vosk.KaldiRecognizer`` the stream uses (JSON strings,
    words with ``SetWords``/``SetPartialWords``)."""

    def AcceptWaveform(self, data: bytes) -> bool: ...  # noqa: N802 — vosk API

    def Result(self) -> str: ...  # noqa: N802

    def PartialResult(self) -> str: ...  # noqa: N802

    def FinalResult(self) -> str: ...  # noqa: N802


class FakeRecognizer:
    """Deterministic recognizer: one word ``w<i>`` per 0.5 s of non-silent
    audio; 0.5 s of digital silence after speech ends the utterance."""

    WORD_S = 0.5
    ENDPOINT_S = 0.5

    def __init__(self) -> None:
        self._t = 0.0  # seconds fed
        self._words: List[dict] = []
        self._speech = 0.0  # speech seconds fed
        self._silence = 0.0
        self._n = 0

    def AcceptWaveform(self, data: bytes) -> bool:  # noqa: N802
        samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2")
        dt = len(samples) / 16000
        self._t += dt
        if np.any(samples):
            self._silence = 0.0
            self._speech += dt
            while self._speech >= (self._n + 1) * self.WORD_S - 1e-9:
                end = self._t - (self._speech - (self._n + 1) * self.WORD_S)
                self._words.append(
                    {
                        "word": f"w{self._n}",
                        "conf": 0.9,
                        "start": round(end - self.WORD_S, 3),
                        "end": round(end, 3),
                    }
                )
                self._n += 1
            return False
        self._silence += dt
        return bool(self._words) and self._silence >= self.ENDPOINT_S - 1e-9

    def _take(self) -> dict:
        words, self._words = self._words, []
        return {"text": " ".join(w["word"] for w in words), "result": words}

    def Result(self) -> str:  # noqa: N802
        return json.dumps(self._take())

    def FinalResult(self) -> str:  # noqa: N802
        return json.dumps(self._take())

    def PartialResult(self) -> str:  # noqa: N802
        return json.dumps(
            {
                "partial": " ".join(w["word"] for w in self._words),
                "partial_result": self._words,
            }
        )


class FakeVoskBackend:
    name = "fake"

    def __init__(
        self,
        language: str,
        model_id: str = "fake",
        load_delay: float = 0.0,
        fail_on_load: Optional[str] = None,
    ) -> None:
        self.language = language
        self.model_id = model_id
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self.calls: List[Tuple[int, str]] = []

    def fetch(self, progress: Optional[Progress] = None) -> None:
        pass  # nothing to provision

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def unload(self) -> None:
        pass  # holds no model

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        self.calls.append((len(audio), language))
        if not np.any(audio):
            return Transcript("", ())
        end = round(len(audio) / 16000, 3)
        words = (
            Word(f"fake-{language}", 0.9, 0.0, end / 2),
            Word("words", 0.5, end / 2, end),
        )
        return Transcript(" ".join(w.word for w in words), words)

    def recognizer(self) -> FakeRecognizer:
        return FakeRecognizer()


def make_backend(settings: VoskSettings) -> VoskBackend:
    if settings.backend == "fake":
        return FakeVoskBackend(settings.language, settings.model)
    from .vosk_backend import KaldiVoskBackend

    return KaldiVoskBackend(settings)
