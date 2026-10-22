"""Server-side long-audio chunk planning (spec section 5.2)."""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from src.segmentation.chunks import plan_chunks

SAMPLE_RATE = 16000


def speech_spans(
    audio: np.ndarray, sample_rate: int = SAMPLE_RATE
) -> list[tuple[int, int]]:
    """Speech spans (start, end sample indices) found by faster-whisper's VAD."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    options = VadOptions(threshold=0.5, min_silence_duration_ms=500, speech_pad_ms=200)
    found = get_speech_timestamps(audio, options, sampling_rate=sample_rate)
    return [(int(s["start"]), int(s["end"])) for s in found]


def split_for_transcription(
    audio: np.ndarray,
    chunk_seconds: float,
    lookback_seconds: float,
    *,
    spans_fn: Callable[[np.ndarray], Sequence[tuple[int, int]]] = speech_spans,
) -> list[tuple[int, int]]:
    """Plan chunk boundaries (sample indices) for ``audio`` at 16 kHz."""
    total = len(audio)
    chunk = int(chunk_seconds * SAMPLE_RATE)
    if total <= chunk:
        return plan_chunks([], total, chunk, 0)
    return plan_chunks(
        spans_fn(audio), total, chunk, int(lookback_seconds * SAMPLE_RATE)
    )
