"""Streaming 48 kHz float32 -> 16 kHz PCM16 for the WS speech streams.

A linear-phase low-pass FIR (cut below 8 kHz) runs with its filter state
kept between blocks, then every third sample is taken - the phase is kept
across blocks, so the output does not depend on the capture block size and
has no click per block. Latency: half the filter (48 taps = 1 ms).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import firwin, lfilter

OUTPUT_RATE = 16000
TAPS = 97


class Downsampler:
    def __init__(self, input_rate: int = 48000) -> None:
        if input_rate % OUTPUT_RATE:
            raise ValueError(f"input_rate must be a multiple of {OUTPUT_RATE}")
        self._down = input_rate // OUTPUT_RATE
        self._fir = (
            firwin(TAPS, 0.45 * OUTPUT_RATE, fs=input_rate)
            if self._down > 1
            else np.array([1.0])
        )
        self._zi = np.zeros(len(self._fir) - 1)
        self._phase = 0  # input samples to skip before the next kept one

    def __call__(self, block: np.ndarray) -> np.ndarray:
        """Float32 block at the input rate -> int16 at 16 kHz."""
        x = np.asarray(block, dtype=np.float64).reshape(-1)
        if not x.size:
            return np.zeros(0, dtype=np.int16)
        y, self._zi = lfilter(self._fir, 1.0, x, zi=self._zi)
        kept = y[self._phase :: self._down]
        self._phase = (self._phase - x.size) % self._down
        return (np.clip(kept, -1.0, 1.0) * 32767.0).astype(np.int16)


def rms(block: np.ndarray) -> float:
    """Signal level of a float block (0 = silence)."""
    x = np.asarray(block, dtype=np.float32)
    return float(np.sqrt(np.mean(x**2))) if x.size else 0.0
