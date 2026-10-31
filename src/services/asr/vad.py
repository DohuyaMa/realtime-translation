"""Speech detectors for the streaming session (per 512-sample window)."""

from __future__ import annotations

from typing import Protocol

import numpy as np

WINDOW = 512  # samples at 16 kHz = 32 ms
_CONTEXT = 64  # Silero v6 looks at the previous 64 samples of each window


class Vad(Protocol):
    def probs(self, windows: np.ndarray) -> np.ndarray:
        """float32 ``(n, WINDOW)`` consecutive windows -> speech prob each."""
        ...


class SileroVad:
    """faster-whisper's bundled Silero VAD (ONNX, CPU), fed window by window.

    The model is stateless apart from the 64-sample context of each window
    (faster-whisper runs it as one batch with zero state too); the context
    of the first window of a call is the tail of the previous call.
    """

    def __init__(self) -> None:
        from faster_whisper.vad import get_vad_model

        self._session = get_vad_model().session  # process-wide, thread-safe run()
        self._ctx = np.zeros(_CONTEXT, dtype=np.float32)
        self._state = np.zeros((1, 1, 128), dtype=np.float32)

    def probs(self, windows: np.ndarray) -> np.ndarray:
        if len(windows) == 0:
            return np.zeros(0, dtype=np.float32)
        ctx = np.concatenate([self._ctx[None, :], windows[:-1, -_CONTEXT:]])
        self._ctx = windows[-1, -_CONTEXT:].copy()
        batch = np.concatenate([ctx, windows], axis=1).astype(np.float32)
        out = self._session.run(
            None, {"input": batch, "h": self._state, "c": self._state}
        )[0]
        return np.asarray(out, dtype=np.float32).reshape(-1)


class EnergyVad:
    """RMS threshold; the deterministic detector of the fake backend."""

    def __init__(self, threshold_rms: float = 0.01) -> None:
        self.threshold_rms = threshold_rms

    def probs(self, windows: np.ndarray) -> np.ndarray:
        rms = np.sqrt(np.mean(np.square(windows), axis=1))
        return (rms >= self.threshold_rms).astype(np.float32)
