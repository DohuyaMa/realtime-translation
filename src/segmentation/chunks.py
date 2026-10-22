"""Offline chunk planning for long files (server side, spec section 5.2)."""

from __future__ import annotations

from typing import Sequence


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
