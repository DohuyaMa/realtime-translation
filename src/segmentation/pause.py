"""Streaming pause-aware segmenter (edge side, spec section 5.1).

The segmenter receives 32 ms windows (512 samples at 16 kHz) together with
the speech probability from a VAD and cuts the stream only at pauses, so
words are never split in half.

Rules chosen where the spec leaves details open:

* Time is measured in windows of the real stream: window ``i`` covers
  ``[i * 0.032, (i + 1) * 0.032)`` seconds. ``start_s``/``end_s`` follow the
  real stream even when silence was dropped from the audio
  (``keep_silence_ms``).
* Pre-roll: while idle the segmenter keeps the last ``preroll_ms // 32``
  windows that were not emitted. A speech window starts a segment made of
  all of them plus itself.
* ``end``: the first ``min(silence_run_ms, 300) // 32`` windows of the
  trailing pause are kept, the rest goes back to the pre-roll ring.
* ``soft``: the current pause has ``n`` windows; the cut is placed before
  its window ``n // 2``. The tail is silence by construction, so (as the
  spec says) no new segment starts; the tail seeds the pre-roll ring. The
  next segment therefore starts exactly at ``prev.end_s`` when speech
  resumes within ``preroll_ms``; a longer pause is cut-out silence.
* ``hard``: among the last ``lookback_ms // 32`` windows (never the first
  window of the segment, so the head is non-empty) the one with the lowest
  probability is chosen, ties going to the latest; the cut is placed before
  it and the tail continues as the new segment.
* A segment (any reason) is emitted only if it holds at least
  ``min_speech_ms`` of speech; otherwise it is discarded.
* ``flush()`` does not trim trailing silence (only ``keep_silence_ms``
  applies) and leaves the segmenter idle with an empty pre-roll ring; the
  stream clock keeps running. ``reset()`` also restarts the clock at 0.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512
WINDOW_MS = 32

END_TAIL_MS = 300  # trailing silence kept after an "end"

Reason = Literal["end", "soft", "hard", "flush"]


@dataclass(frozen=True)
class SegmenterConfig:
    threshold: float = 0.5
    min_speech_ms: int = 400
    end_silence_ms: int | None = 700
    soft_max_ms: int = 8_000
    soft_silence_ms: int = 250
    hard_max_ms: int = 20_000
    lookback_ms: int = 1_500
    keep_silence_ms: int | None = None
    preroll_ms: int = 300

    def validate(self) -> None:
        """Raise ValueError unless the configuration is usable."""
        if not 0 < self.threshold <= 1:
            raise ValueError(f"threshold must be in (0, 1], got {self.threshold}")
        positive = {
            "min_speech_ms": self.min_speech_ms,
            "end_silence_ms": self.end_silence_ms,
            "soft_max_ms": self.soft_max_ms,
            "soft_silence_ms": self.soft_silence_ms,
            "hard_max_ms": self.hard_max_ms,
            "lookback_ms": self.lookback_ms,
            "keep_silence_ms": self.keep_silence_ms,
            "preroll_ms": self.preroll_ms,
        }
        for name, value in positive.items():
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0, got {value}")
        if self.soft_max_ms >= self.hard_max_ms:
            raise ValueError("soft_max_ms must be < hard_max_ms")
        if self.lookback_ms > self.hard_max_ms:
            raise ValueError("lookback_ms must be <= hard_max_ms")

    @classmethod
    def translate(cls) -> "SegmenterConfig":
        """Phrase-sized segments for live translation (the defaults)."""
        return cls()

    @classmethod
    def transcribe(
        cls, chunk_min_s: float = 300, chunk_max_s: float = 600
    ) -> "SegmenterConfig":
        """Long chunks (chunk_min_s..chunk_max_s) for transcription."""
        return cls(
            end_silence_ms=None,
            soft_max_ms=int(chunk_min_s * 1000),
            soft_silence_ms=500,
            hard_max_ms=int(chunk_max_s * 1000),
            lookback_ms=5000,
            keep_silence_ms=2000,
        )


@dataclass(frozen=True)
class Segment:
    audio: np.ndarray  # int16 mono 16 kHz
    start_s: float
    end_s: float
    reason: Reason


@dataclass(frozen=True)
class _Win:
    idx: int  # stream window index
    audio: np.ndarray
    prob: float
    speech: bool
    kept: bool  # False: silence dropped by keep_silence_ms


def _time(idx: int) -> float:
    return idx * WINDOW_SAMPLES / SAMPLE_RATE


class PauseSegmenter:
    """Cuts a stream of VAD-scored windows into segments at pauses."""

    def __init__(self, cfg: SegmenterConfig) -> None:
        cfg.validate()
        self._cfg = cfg
        self._pos = 0
        self._ring: deque[_Win] = deque(maxlen=self._preroll_windows())
        self._seg: list[_Win] = []
        self._speech = 0  # speech windows in the current segment
        self._silence_run = 0  # trailing silent windows in the segment

    def feed(self, window: np.ndarray, prob: float) -> list[Segment]:
        """Consume one window; return the segments completed by it."""
        window = np.asarray(window)
        if window.dtype != np.int16 or window.shape != (WINDOW_SAMPLES,):
            raise ValueError(
                f"window must be int16 of shape ({WINDOW_SAMPLES},), "
                f"got {window.dtype} {window.shape}"
            )
        cfg = self._cfg
        speech = prob >= cfg.threshold
        idx = self._pos
        self._pos += 1

        if not self._seg:
            if not speech:
                self._ring.append(_Win(idx, window, prob, False, True))
                return []
            self._seg = list(self._ring)
            self._ring.clear()
            self._speech = 0
            self._silence_run = 0

        if speech:
            self._speech += 1
            self._silence_run = 0
            kept = True
        else:
            self._silence_run += 1
            kept = (
                cfg.keep_silence_ms is None
                or self._silence_run * WINDOW_MS <= cfg.keep_silence_ms
            )
        self._seg.append(_Win(idx, window, prob, speech, kept))

        silence_ms = self._silence_run * WINDOW_MS
        len_ms = len(self._seg) * WINDOW_MS
        if cfg.end_silence_ms is not None and silence_ms >= cfg.end_silence_ms:
            keep = min(silence_ms, END_TAIL_MS) // WINDOW_MS
            return self._cut(len(self._seg) - self._silence_run + keep, "end")
        if len_ms >= cfg.soft_max_ms and silence_ms >= cfg.soft_silence_ms:
            run = self._silence_run
            return self._cut(len(self._seg) - run + run // 2, "soft")
        if len_ms >= cfg.hard_max_ms:
            return self._cut(self._hard_cut_index(), "hard")
        return []

    def flush(self) -> Segment | None:
        """Return the pending segment if it holds enough speech; go idle."""
        out = None
        if self._seg and self._speech * WINDOW_MS >= self._cfg.min_speech_ms:
            out = self._segment(self._seg, "flush")
        self._go_idle()
        self._ring.clear()
        return out

    def reconfigure(self, cfg: SegmenterConfig) -> None:
        """Switch configuration; it applies from the next window."""
        cfg.validate()
        self._cfg = cfg
        self._ring = deque(self._ring, maxlen=self._preroll_windows())

    def reset(self) -> None:
        """Drop all state, including the stream clock."""
        self._go_idle()
        self._ring.clear()
        self._pos = 0

    def _go_idle(self) -> None:
        self._seg = []
        self._speech = self._silence_run = 0

    def _preroll_windows(self) -> int:
        return self._cfg.preroll_ms // WINDOW_MS

    def _hard_cut_index(self) -> int:
        n = max(1, self._cfg.lookback_ms // WINDOW_MS)
        first = max(1, len(self._seg) - n)
        best = first
        for i in range(first, len(self._seg)):
            if self._seg[i].prob <= self._seg[best].prob:
                best = i
        return best

    def _cut(self, at: int, reason: Reason) -> list[Segment]:
        """Emit ``self._seg[:at]`` and route the rest of it.

        ``hard``: the rest continues as the current segment. Otherwise the
        segmenter goes idle and the rest (silence) seeds the pre-roll ring.
        """
        head, tail = self._seg[:at], self._seg[at:]
        enough = sum(w.speech for w in head) * WINDOW_MS >= self._cfg.min_speech_ms
        out = [self._segment(head, reason)] if enough else []
        if reason == "hard":
            self._seg = tail
            self._speech = sum(w.speech for w in tail)
            run = 0
            for w in reversed(tail):
                if w.speech:
                    break
                run += 1
            self._silence_run = run
        else:
            self._go_idle()
            self._ring.extend(_Win(w.idx, w.audio, w.prob, False, True) for w in tail)
        return out

    @staticmethod
    def _segment(wins: list[_Win], reason: Reason) -> Segment:
        kept = [w.audio for w in wins if w.kept]
        audio = np.concatenate(kept) if kept else np.zeros(0, np.int16)
        return Segment(audio, _time(wins[0].idx), _time(wins[-1].idx + 1), reason)
