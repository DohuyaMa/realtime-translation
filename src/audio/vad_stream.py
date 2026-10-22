"""Streaming VAD feeding the PauseSegmenter on the edge.

Resampling approach: input (default 48 kHz; any multiple of 16 kHz) is
accumulated and cut into frames of ``input_rate / 16000 * 512`` samples (1536 at
48 kHz). Each frame is resampled with ``resample_poly(1, 3)`` together with 96
samples of left context and 96 of look-ahead, and only the centred 512 outputs
are kept, so the output matches a whole-signal resample (up to float error)
instead of showing a click per frame. Frame boundaries depend only on the sample
count, so results are block-size independent. Cost: 96 samples (6 ms) latency.
"""

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
        if input_rate % SAMPLE_RATE:
            raise ValueError(f"input_rate must be a multiple of {SAMPLE_RATE}")
        self._down = input_rate // SAMPLE_RATE
        self._frame = WINDOW_SAMPLES * self._down
        # Left context and look-ahead (input samples, multiples of the
        # decimation factor) so the polyphase filter sees real neighbours.
        self._ctx = 32 * self._down
        self._need = self._ctx + self._frame + self._ctx
        self._buf = np.zeros(self._ctx, dtype=np.float32)  # stream-start zeros
        self._level = 0.0

    def _process(self, out: list[Segment]) -> None:
        """Resample [ctx + frame + look-ahead] and keep the centred window."""
        res = resample_poly(self._buf[: self._need], 1, self._down)
        c = self._ctx // self._down
        window = (np.clip(res[c : c + WINDOW_SAMPLES], -1.0, 1.0) * 32767.0).astype(
            np.int16
        )
        self._buf = self._buf[self._frame :]
        out += self._segmenter.feed(window, self._vad(window))

    def feed_block(self, block: np.ndarray) -> list[Segment]:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if block.size:
            self._level = float(np.sqrt(np.mean(block**2)))
        self._buf = np.concatenate([self._buf, block])
        out: list[Segment] = []
        while self._buf.size >= self._need:
            self._process(out)
        return out

    def flush(self) -> list[Segment]:
        """Process the zero-padded remainder, then flush the segmenter.

        Returns every segment emitted on the way, in order, then the tail.
        """
        out: list[Segment] = []
        # Samples after the ctx left-context are all unprocessed real input.
        windows = -(-(self._buf.size - self._ctx) // self._frame)
        pad = windows * self._frame + 2 * self._ctx - self._buf.size
        self._buf = np.concatenate([self._buf, np.zeros(max(0, pad), np.float32)])
        for _ in range(windows):
            self._process(out)
        tail = self._segmenter.flush()
        if tail is not None:
            out.append(tail)
        return out

    def reconfigure(self, cfg: SegmenterConfig) -> None:
        self._segmenter.reconfigure(cfg)

    def last_level(self) -> float:
        return self._level
