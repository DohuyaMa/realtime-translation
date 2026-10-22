"""Streaming VAD feeding the PauseSegmenter on the edge.

Resampling approach: input (default 48 kHz) is accumulated and resampled in
fixed, non-overlapping frames of ``input_rate / 16000 * 512`` samples (1536 at
48 kHz) with ``resample_poly(1, 3)``. Every frame yields exactly one 512-sample
VAD window, and frame boundaries depend only on the sample count, never on the
size of the blocks passed in, so results are block-size independent.
"""

from math import gcd
from typing import Protocol

import numpy as np
from scipy.signal import resample_poly

from src.segmentation import (
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    PauseSegmenter,
    Segment,
    SegmenterConfig,
)

ENERGY_THRESHOLD = 0.008


class VadModel(Protocol):
    def __call__(self, window_int16: np.ndarray) -> float: ...


def _energy_vad(window_int16: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean((window_int16.astype(np.float32) / 32768.0) ** 2)))
    return 1.0 if rms >= ENERGY_THRESHOLD else 0.0


def make_vad() -> VadModel:
    """Silero VAD when available, otherwise a simple energy detector."""
    try:
        from pysilero_vad import SileroVoiceActivityDetector

        detector = SileroVoiceActivityDetector()
    except Exception:  # ImportError or model load failure
        return _energy_vad

    def silero(window_int16: np.ndarray) -> float:
        return float(detector(np.ascontiguousarray(window_int16).tobytes()))

    return silero


class VadStream:
    def __init__(
        self, vad: VadModel, cfg: SegmenterConfig, input_rate: int = 48000
    ) -> None:
        self._vad = vad
        self._segmenter = PauseSegmenter(cfg)
        g = gcd(SAMPLE_RATE, input_rate)
        self._up, self._down = SAMPLE_RATE // g, input_rate // g
        self._frame = WINDOW_SAMPLES * self._down // self._up
        self._buf = np.zeros(0, dtype=np.float32)
        self._level = 0.0

    def feed_block(self, block: np.ndarray) -> list[Segment]:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size:
            self._level = float(np.sqrt(np.mean(block**2)))
        self._buf = np.concatenate([self._buf, block])
        out: list[Segment] = []
        while self._buf.size >= self._frame:
            frame, self._buf = self._buf[: self._frame], self._buf[self._frame :]
            res = resample_poly(frame, self._up, self._down)[:WINDOW_SAMPLES]
            window = (np.clip(res, -1.0, 1.0) * 32767.0).astype(np.int16)
            out += self._segmenter.feed(window, self._vad(window))
        return out

    def flush(self) -> Segment | None:
        return self._segmenter.flush()

    def reconfigure(self, cfg: SegmenterConfig) -> None:
        self._segmenter.reconfigure(cfg)

    def last_level(self) -> float:
        return self._level
