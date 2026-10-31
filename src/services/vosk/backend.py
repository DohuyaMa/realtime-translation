"""rt-vosk backend interface, result types and the fake backend."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

import numpy as np

from ..common.model_store import Progress
from .settings import VoskSettings

__all__ = ["FakeVoskBackend", "Transcript", "VoskBackend", "Word", "make_backend"]


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


def make_backend(settings: VoskSettings) -> VoskBackend:
    if settings.backend == "fake":
        return FakeVoskBackend(settings.language, settings.model)
    from .vosk_backend import KaldiVoskBackend

    return KaldiVoskBackend(settings)
