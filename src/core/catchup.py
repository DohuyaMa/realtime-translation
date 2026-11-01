"""Playback catch-up: Kokoro speaks faster only while playback lags behind.

Owner 2026-11-01 («я повільніше говорю ніж кокоро озвучує» - never faster in
general): English TTS of a unit is often longer than the Ukrainian speech, so
the playback queue grows on long reading (live §18: speech end -> playback
p90 9.9 s, max 14 s). While the unplayed audio (queued + playing) exceeds
``ON_S`` the TTS stage asks for a higher speed - one ``STEP`` per synthesized
piece up to the backlog's target (``target``: linear from ``OFF_S`` to
``FULL_S``, at most ``MAX_FACTOR``); below ``OFF_S`` it is back to the
configured speed at once (checked before each request and after each played
piece, ``settle``). Hysteresis 4 s / 2 s so the voice does not flicker.
Each change is returned as a log line; counters go to ``get_stats``.
"""

from __future__ import annotations

from typing import Optional

ON_S = 4.0
OFF_S = 2.0
FULL_S = 8.0
MAX_FACTOR = 1.25
STEP = 0.05
# base of the factor when the edge does not set ``tts.speed`` (= rt-tts's
# own default ``RT_TTS_SPEED`` 1.0, Kokoro's natural speed)
DEFAULT_SPEED = 1.0


def target(backlog_s: float) -> float:
    """Factor for this much unplayed audio while catching up (>= one step)."""
    share = (backlog_s - OFF_S) / (FULL_S - OFF_S)
    raw = 1.0 + (MAX_FACTOR - 1.0) * min(1.0, max(0.0, share))
    return max(1.0 + STEP, round(round(raw / STEP) * STEP, 2))


class CatchUp:
    def __init__(self) -> None:
        self.active = False
        self.factor = 1.0
        self.episodes = 0  # times catch-up switched on
        self.pieces = 0  # pieces synthesized faster

    def update(self, backlog_s: float) -> Optional[str]:
        """Before one TTS request: the new state for ``backlog_s`` seconds of
        unplayed audio; returns a log line when the factor changed."""
        was = self.factor
        if not self.active and backlog_s > ON_S:
            self.active = True
            self.episodes += 1
        elif self.active and backlog_s < OFF_S:
            self.active = False
        if self.active:
            self.factor = min(target(backlog_s), round(self.factor + STEP, 2))
            self.factor = max(self.factor, 1.0 + STEP)
            self.pieces += 1
        else:
            self.factor = 1.0
        if self.factor == was:
            return None
        if not self.active:
            return f"tts catch-up off: playback backlog {backlog_s:.1f} s -> base speed"
        state = "on" if was == 1.0 else "speed"
        return (
            f"tts catch-up {state}: playback backlog {backlog_s:.1f} s ->"
            f" speed x{self.factor:.2f}"
        )

    def settle(self, backlog_s: float) -> Optional[str]:
        """After a piece played: off once the backlog is below ``OFF_S``
        (the state is right before the next request and in the stats)."""
        if not self.active or backlog_s >= OFF_S:
            return None
        self.active = False
        self.factor = 1.0
        return f"tts catch-up off: playback backlog {backlog_s:.1f} s -> base speed"

    def speed(self, base: Optional[float]) -> Optional[float]:
        """Speed of the next request: ``base`` (None = the service default)
        unless catching up."""
        if not self.active:
            return base
        return round((DEFAULT_SPEED if base is None else base) * self.factor, 3)

    def stats(self) -> dict:
        return {
            "active": self.active,
            "factor": self.factor,
            "episodes": self.episodes,
            "pieces": self.pieces,
        }
