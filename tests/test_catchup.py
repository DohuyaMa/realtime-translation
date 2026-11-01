"""Playback catch-up (owner 2026-11-01): faster TTS only while a backlog
of unplayed audio exists; ``core.catchup`` + the TTS stage."""

import threading

import numpy as np
import pytest

from src.clients.asr import AsrResult
from src.core.catchup import MAX_FACTOR, CatchUp, target
from test_pipeline_stages import SlowAsr, SlowLlm, system
from test_translation_system import FakeTts, wait_for


@pytest.mark.parametrize(
    "backlog, factor",
    [(1.0, 1.05), (4.5, 1.1), (6.0, 1.15), (8.0, 1.25), (20.0, 1.25)],
)
def test_target_grows_with_the_backlog_and_is_capped(backlog, factor):
    assert target(backlog) == pytest.approx(factor)


def test_no_backlog_keeps_the_configured_speed():
    c = CatchUp()
    for backlog in (0.0, 2.0, 3.9, 4.0):
        assert c.update(backlog) is None
        assert c.speed(0.9) == 0.9 and c.speed(None) is None
    assert c.stats() == {"active": False, "factor": 1.0, "episodes": 0, "pieces": 0}


def test_speeds_up_one_step_per_piece_and_back_below_two_seconds():
    c = CatchUp()
    assert "catch-up on" in c.update(9.0)
    assert c.speed(1.0) == 1.05 and c.speed(None) == 1.05  # None = default 1.0
    factors = []
    for _ in range(6):
        c.update(9.0)
        factors.append(c.factor)
    assert factors == [1.1, 1.15, 1.2, 1.25, 1.25, 1.25]
    assert c.update(9.0) is None  # no change, no log line
    assert c.update(3.0) is not None and c.factor == 1.05  # down at once
    assert c.active  # hysteresis: still on between 2 and 4 s
    assert "off" in c.update(1.9)
    assert c.speed(0.9) == 0.9 and c.factor == 1.0
    assert c.stats()["episodes"] == 1 and c.stats()["pieces"] == 9
    assert MAX_FACTOR == 1.25


def test_settle_turns_off_after_playback_drained():
    c = CatchUp()
    c.update(9.0)
    assert c.settle(2.5) is None and c.active
    assert "off" in c.settle(1.0)
    assert not c.active and c.factor == 1.0 and c.settle(0.0) is None


class LongTts(FakeTts):
    """Each sentence is 3 s of (non-silent) audio; records the speed."""

    def synthesize(self, text, voice=None, speed=None):
        if text == "Hello.":
            return super().synthesize(text, voice, speed)
        self.calls.append((text, voice, speed))
        return np.full(3 * 24000, 0.1, dtype=np.float32), 24000


def test_tts_speaks_faster_only_while_playback_lags():
    """The owner's case: English audio longer than the speech - the queue
    grows, later pieces go faster; once played out, the base speed again."""
    release = threading.Event()
    played = []

    def sink(audio, sr):
        assert release.wait(5)  # the device is busy: the backlog builds up
        played.append(len(audio))

    n = 5
    asr = SlowAsr([AsrResult(f"p{i}.", "uk") for i in range(n)], 0.0)
    tts = LongTts()
    ts, speak, _ = system(asr, SlowLlm(0.0), tts, sink)
    assert ts.start()
    try:
        speak(n)
        assert wait_for(lambda: len(tts.calls) == n, timeout=5)
        speeds = [s for _, _, s in tts.calls]
        # backlog 0, 3, 6 (> 4: on), 9, ... s before each request
        assert speeds[:2] == [None, None]
        assert speeds[2] == 1.05 and speeds[3] == 1.1
        stats = ts.get_stats()["tts_catchup"]
        assert stats["active"] and stats["episodes"] == 1
        release.set()
        assert wait_for(lambda: len(played) == n, timeout=5)
        # played out: off already, not only at the next request
        assert wait_for(lambda: not ts.get_stats()["tts_catchup"]["active"])
        speak_after = AsrResult("p9.", "uk")
        asr.results.append(speak_after)
        speak(1)
        assert wait_for(lambda: len(tts.calls) == n + 1, timeout=5)
        assert tts.calls[-1][2] is None  # backlog gone: base speed
        assert not ts.get_stats()["tts_catchup"]["active"]
    finally:
        release.set()
        ts.stop()


def test_panel_shows_the_catch_up():
    from src.ui.widgets.service_status_panel import format_pipeline_stats

    status = {
        "playback_backlog_s": 6.2,
        "tts_catchup": {"active": True, "factor": 1.15, "episodes": 1, "pieces": 3},
    }
    assert format_pipeline_stats(status) == (
        "черга відтворення 6.2 с: озвучка ×1.15; озвучено пришвидшено: 3"
    )
    assert format_pipeline_stats({"tts_catchup": {"active": False}}) == ""
