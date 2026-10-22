"""ASR backend interface + fake backend."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Protocol, Sequence, Tuple

import numpy as np

from ..common.audio import TARGET_RATE
from .settings import AsrSettings, normalize_language

__all__ = [
    "AsrBackend",
    "DecodeOptions",
    "FakeAsrBackend",
    "Segment",
    "Transcription",
    "make_backend",
    "normalize_language",
]


@dataclass(frozen=True)
class DecodeOptions:
    language: Optional[str]
    prompt: Optional[str]
    temperature: float = 0.0
    beam_size: int = 5
    vad_filter: bool = False
    without_timestamps: bool = False


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcription:
    text: str
    language: Optional[str]
    duration: float
    segments: List[Segment]


class AsrBackend(Protocol):
    name: str
    model_id: str

    def load(self) -> None: ...

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription: ...

    def transcribe_iter(
        self, audio: np.ndarray, opts: DecodeOptions
    ) -> Iterator[Segment]:
        """Lazy segments; opts.language is required."""
        ...

    def detect_language(self, audio: np.ndarray, allowed: Sequence[str]) -> str: ...


class FakeAsrBackend:
    """Deterministic backend for tests and GPU-less development (RT_ASR_BACKEND=fake)."""

    name = "fake"

    def __init__(
        self,
        model_id: str = "fake",
        text_fn: Optional[Callable[[np.ndarray, DecodeOptions], str]] = None,
        load_delay: float = 0.0,
        fail_on_load: Optional[str] = None,
        decode_delay: float = 0.0,
        detected: str = "uk",
        segment_seconds: float = 10.0,
    ) -> None:
        self.model_id = model_id
        self._text_fn = text_fn
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self._decode_delay = decode_delay
        self.detected = detected
        self.segment_seconds = segment_seconds
        self.calls: List[Tuple[int, DecodeOptions]] = []

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def _text(self, audio: np.ndarray, opts: DecodeOptions) -> str:
        if self._text_fn:
            return self._text_fn(audio, opts)
        return f"speech {len(audio) / TARGET_RATE:.2f}s"

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription:
        self.calls.append((len(audio), opts))
        time.sleep(self._decode_delay)
        duration = len(audio) / TARGET_RATE
        text = self._text(audio, opts)
        segments = [Segment(0.0, duration, text)] if text else []
        return Transcription(
            text=text,
            language=opts.language or self.detected,
            duration=duration,
            segments=segments,
        )

    def transcribe_iter(
        self, audio: np.ndarray, opts: DecodeOptions
    ) -> Iterator[Segment]:
        if opts.language is None:
            raise ValueError("transcribe_iter requires opts.language")
        return self._iter(audio, opts)

    def _iter(self, audio: np.ndarray, opts: DecodeOptions) -> Iterator[Segment]:
        self.calls.append((len(audio), opts))
        step = max(1, int(self.segment_seconds * TARGET_RATE))
        for lo in range(0, len(audio), step):
            time.sleep(self._decode_delay)
            piece = audio[lo : lo + step]
            text = self._text(piece, opts)
            if text:
                yield Segment(lo / TARGET_RATE, (lo + len(piece)) / TARGET_RATE, text)

    def detect_language(self, audio: np.ndarray, allowed: Sequence[str]) -> str:
        if not allowed:
            raise ValueError("allowed languages must not be empty")
        return self.detected if self.detected in allowed else allowed[0]


def make_backend(settings: AsrSettings) -> AsrBackend:
    if settings.backend == "fake":
        return FakeAsrBackend(model_id=settings.model)
    from .faster_whisper_backend import FasterWhisperBackend

    return FasterWhisperBackend(settings)
