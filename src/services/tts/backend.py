"""TTS backend interface + fake backend."""

from __future__ import annotations

import re
import time
from typing import Iterator, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from .settings import TtsSettings
from .voices import Voice, catalog

__all__ = [
    "FakeTtsBackend",
    "TtsBackend",
    "UnsupportedVoiceLanguage",
    "make_backend",
]


class UnsupportedVoiceLanguage(RuntimeError):
    """The voice exists but its language pipeline cannot be used on this host."""


class TtsBackend(Protocol):
    name: str
    model_id: str
    sample_rate: int

    def load(self) -> None: ...

    def voices(self) -> List[Voice]: ...

    def prepare_voice(self, voice: str) -> None: ...

    def synthesize(
        self, text: str, voice: str, speed: float
    ) -> Iterator[np.ndarray]: ...


class FakeTtsBackend:
    name = "fake"
    sample_rate = 24000

    def __init__(
        self,
        model_id: str = "fake",
        load_delay: float = 0.0,
        fail_on_load: Optional[str] = None,
        unsupported_prefixes: Sequence[str] = (),
    ) -> None:
        self.model_id = model_id
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self._unsupported = tuple(unsupported_prefixes)
        self.calls: List[Tuple[str, str, float]] = []

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def voices(self) -> List[Voice]:
        return catalog()

    def prepare_voice(self, voice: str) -> None:
        if voice[0] in self._unsupported:
            raise UnsupportedVoiceLanguage(
                f"language pipeline {voice[0]!r} unavailable"
            )

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]:
        self.calls.append((text, voice, speed))
        amplitude = 0.1 + (sum(map(ord, voice)) % 50) / 100.0
        for sentence in (s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s):
            n = max(1, int(self.sample_rate * 0.05 * len(sentence) / speed))
            t = np.arange(n) / self.sample_rate
            yield (amplitude * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)


def make_backend(settings: TtsSettings) -> TtsBackend:
    if settings.backend == "fake":
        return FakeTtsBackend(model_id=settings.model)
    from .kokoro_backend import KokoroBackend

    return KokoroBackend(settings)
