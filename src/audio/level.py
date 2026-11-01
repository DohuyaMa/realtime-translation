"""Input level normalization on the edge, once, before the speech stream
(the unit audio sent to rt-phonetic is cut from the same normalized audio).

Live 2026-10-31 §13: the microphone at 39.5% input volume - whisper still
heard the speech, the then cross-check recognizer returned no words at all,
so the per-unit gate dropped every phrase as ``no_word``. ``LevelNormalizer`` brings the
speech level toward ``TARGET_DBFS``:

* the speech level is an envelope of the block RMS of speech blocks only
  (fast attack, slow release); a block is speech when it is louder than
  ``SPEECH_GATE_DBFS`` and ``NOISE_MARGIN_DB`` above the tracked noise
  floor - silence and steady noise never raise the gain;
* gain = target - speech level, never below 0 dB (loud input is left to
  the limiter) and never above ``MAX_GAIN_DB``; it moves smoothly inside a
  block (no clicks);
* a soft limiter keeps the peaks below full scale.

``quiet`` turns on when the raw (before gain) speech level stays below
``QUIET_DBFS`` for ``QUIET_AFTER_S`` seconds of speech and off again
``QUIET_HYSTERESIS_DB`` above it: the UI warns «Мікрофон тихий» with the
measured dBFS - the gain is a remedy, not a reason to leave the input low.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

TARGET_DBFS = -20.0
MAX_GAIN_DB = 24.0
SPEECH_GATE_DBFS = -65.0
NOISE_MARGIN_DB = 10.0
# envelope time constants, seconds
ATTACK_S = 0.05
RELEASE_S = 1.5
NOISE_RISE_S = 5.0
LIMIT_KNEE = 0.8  # soft limiter: linear below, tanh above
QUIET_DBFS = -38.0
QUIET_AFTER_S = 3.0
QUIET_HYSTERESIS_DB = 4.0
_FLOOR_DB = -100.0


def dbfs(x: np.ndarray) -> float:
    """RMS of a float block in dBFS (full scale = 1.0)."""
    if not x.size:
        return _FLOOR_DB
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return max(_FLOOR_DB, 20.0 * math.log10(r)) if r > 0 else _FLOOR_DB


def _alpha(block_s: float, tau_s: float) -> float:
    return 1.0 - math.exp(-block_s / tau_s)


def soft_limit(y: np.ndarray, knee: float = LIMIT_KNEE) -> np.ndarray:
    """Identity below ``knee``, smoothly saturating toward 1.0 above it."""
    a = np.abs(y)
    over = a > knee
    if not over.any():
        return y
    out = y.copy()
    head = 1.0 - knee
    out[over] = np.sign(y[over]) * (knee + head * np.tanh((a[over] - knee) / head))
    return out


class LevelNormalizer:
    """Float blocks in, gain-normalized float blocks out (same rate)."""

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.speech_db: Optional[float] = None  # raw speech level envelope
        self.noise_db = SPEECH_GATE_DBFS
        self.gain_db = 0.0
        self.quiet = False
        self._quiet_s = 0.0

    def _speech(self, level: float) -> bool:
        return level > max(SPEECH_GATE_DBFS, self.noise_db + NOISE_MARGIN_DB)

    def _track(self, level: float, block_s: float) -> bool:
        speech = self._speech(level)
        if level < self.noise_db:  # the floor follows quiet blocks at once
            self.noise_db = max(level, _FLOOR_DB)
        elif not speech:
            self.noise_db += _alpha(block_s, NOISE_RISE_S) * (level - self.noise_db)
        if not speech:
            return False
        if self.speech_db is None:
            self.speech_db = level
        else:
            tau = ATTACK_S if level > self.speech_db else RELEASE_S
            self.speech_db += _alpha(block_s, tau) * (level - self.speech_db)
        return True

    def _quiet_update(self, speech: bool, block_s: float) -> None:
        if not speech or self.speech_db is None:
            return
        if self.speech_db < QUIET_DBFS:
            self._quiet_s += block_s
            if self._quiet_s >= QUIET_AFTER_S:
                self.quiet = True
        elif self.speech_db >= QUIET_DBFS + QUIET_HYSTERESIS_DB:
            self._quiet_s = 0.0
            self.quiet = False

    def __call__(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, dtype=np.float32).reshape(-1)
        if not x.size:
            return x
        block_s = x.size / self.rate
        speech = self._track(dbfs(x), block_s)
        self._quiet_update(speech, block_s)
        old = self.gain_db
        if self.speech_db is not None:
            self.gain_db = min(MAX_GAIN_DB, max(0.0, TARGET_DBFS - self.speech_db))
        if old == 0.0 and self.gain_db == 0.0:
            return soft_limit(x)
        ramp = np.linspace(old, self.gain_db, x.size, dtype=np.float32)
        return soft_limit(x * np.power(10.0, ramp / 20.0, dtype=np.float32))
