"""Server-side long-audio chunk planning (spec 2026-10-22 section 5.2)."""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

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


def plan_chunks(
    speech_spans: Sequence[tuple[int, int]],
    total_samples: int,
    chunk_samples: int,
    lookback_samples: int,
) -> list[tuple[int, int]]:
    """Split ``[0, total_samples)`` into contiguous chunks cut at pauses.

    Pauses are the gaps between adjacent speech spans (spans may come in any
    order and may overlap). For each chunk starting at ``pos`` the target is
    ``t = pos + chunk_samples``; among pauses intersecting
    ``[t - lookback_samples, t]`` the one with the longest intersection wins
    (ties: the latest) and the boundary is the middle of that intersection.
    Without such a pause the boundary is ``t``. The last chunk ends at
    ``total_samples``; every chunk is non-empty and at most
    ``chunk_samples`` long. Empty audio yields no chunks.
    """
    if chunk_samples <= 0:
        raise ValueError("chunk_samples must be > 0")
    if lookback_samples < 0:
        raise ValueError("lookback_samples must be >= 0")
    if total_samples <= 0:
        return []
    if total_samples <= chunk_samples:
        return [(0, total_samples)]

    pauses: list[tuple[int, int]] = []
    reach = None
    for start, end in sorted(speech_spans):
        if reach is not None and start > reach:
            pauses.append((reach, start))
        reach = end if reach is None else max(reach, end)

    chunks: list[tuple[int, int]] = []
    pos = 0
    while total_samples - pos > chunk_samples:
        t = pos + chunk_samples
        lo = max(t - lookback_samples, pos + 1)
        best: tuple[int, int] | None = None
        for p_start, p_end in pauses:
            a, b = max(p_start, lo), min(p_end, t)
            if a < b and (best is None or b - a >= best[1] - best[0]):
                best = (a, b)
        cut = t if best is None else (best[0] + best[1]) // 2
        chunks.append((pos, cut))
        pos = cut
    chunks.append((pos, total_samples))
    return chunks
