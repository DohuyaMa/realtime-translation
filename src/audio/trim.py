"""Edge silence of synthesized speech.

Kokoro pads every piece with ~0.3 s of silence before and ~0.5 s after the
speech (measured 2026-10-31 on rt-tts CPU): with one piece per unit that is
~0.8 s of silence per unit in the playback queue, which is what made the
queue back up behind dense speech. ``trim_silence`` keeps ``LEAD_PAD_S``
before the first and ``TRAIL_PAD_S`` after the last frame louder than
``THRESHOLD_DB`` below the piece's peak; the speech itself is never cut.
"""

from __future__ import annotations

import numpy as np

FRAME_S = 0.01
THRESHOLD_DB = -40.0
LEAD_PAD_S = 0.05
TRAIL_PAD_S = 0.15


def trim_silence(audio: np.ndarray, sr: int) -> np.ndarray:
    """``audio`` without its edge silence (unchanged when all silent)."""
    x = np.asarray(audio).reshape(-1)
    frame = max(1, int(sr * FRAME_S))
    n = len(x) // frame
    if n == 0:
        return x
    peaks = np.abs(x[: n * frame]).reshape(n, frame).max(axis=1)
    top = float(peaks.max())
    if top <= 0.0:
        return x
    loud = np.nonzero(peaks >= top * 10.0 ** (THRESHOLD_DB / 20.0))[0]
    start = max(0, loud[0] * frame - int(sr * LEAD_PAD_S))
    end = min(len(x), (loud[-1] + 1) * frame + int(sr * TRAIL_PAD_S))
    return x[start:end]
