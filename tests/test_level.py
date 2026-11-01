"""Edge input level normalization (live 2026-10-31 §13: quiet microphone)
and the edge-silence trim of synthesized speech."""

import numpy as np

from src.audio.level import (
    MAX_GAIN_DB,
    QUIET_DBFS,
    TARGET_DBFS,
    LevelNormalizer,
    dbfs,
)
from src.audio.trim import LEAD_PAD_S, TRAIL_PAD_S, trim_silence

RATE = 48000
BLOCK = 1024


def tone(db, seconds, rate=RATE, f=220.0):
    t = np.arange(int(seconds * rate)) / rate
    # sine RMS = amplitude / sqrt(2)
    return (np.sqrt(2) * 10 ** (db / 20) * np.sin(2 * np.pi * f * t)).astype(np.float32)


def noise(db, seconds, rate=RATE, seed=1):
    rng = np.random.default_rng(seed)
    return (10 ** (db / 20) * rng.standard_normal(int(seconds * rate))).astype(
        np.float32
    )


def run(n, x):
    return np.concatenate([n(x[i : i + BLOCK]) for i in range(0, len(x), BLOCK)])


def test_quiet_speech_is_raised_toward_the_target():
    n = LevelNormalizer(RATE)
    out = run(n, np.concatenate([noise(-75, 1.0), tone(-40, 4.0)]))
    assert abs(dbfs(out[-RATE:]) - TARGET_DBFS) < 1.5
    assert 18.0 < n.gain_db <= MAX_GAIN_DB


def test_gain_is_capped_and_silence_never_raises_it():
    n = LevelNormalizer(RATE)
    run(n, noise(-80, 3.0))
    assert n.gain_db == 0.0 and n.speech_db is None  # only noise so far
    run(n, tone(-60, 3.0))
    assert n.gain_db == MAX_GAIN_DB


def test_loud_input_is_not_attenuated_but_limited():
    n = LevelNormalizer(RATE)
    out = run(n, tone(-3, 2.0) * 1.5)
    assert n.gain_db == 0.0
    assert np.abs(out).max() < 1.0


def test_quiet_flag_after_seconds_of_low_speech_and_back():
    n = LevelNormalizer(RATE)
    run(n, tone(QUIET_DBFS - 5, 1.0))
    assert not n.quiet  # not persistent yet
    run(n, tone(QUIET_DBFS - 5, 3.0))
    assert n.quiet
    run(n, tone(-20, 3.0))
    assert not n.quiet


def test_trim_keeps_the_speech_and_short_pads():
    sr = 24000
    speech = tone(-20, 1.0, sr)
    audio = np.concatenate([np.zeros(int(0.3 * sr)), speech, np.zeros(int(0.5 * sr))])
    out = trim_silence(audio.astype(np.float32), sr)
    pads = (LEAD_PAD_S + TRAIL_PAD_S + 0.03) * sr
    assert len(speech) <= len(out) <= len(speech) + pads
    assert np.isclose(np.sum(out**2), np.sum(speech**2))  # no speech cut
    assert len(trim_silence(np.zeros(100, np.float32), sr)) == 100


def test_pipeline_warns_about_a_quiet_microphone_and_feeds_gained_audio():
    from test_phonetic_crosscheck import system

    ts, streams, _, events = system()
    assert ts.start()
    try:
        x = tone(QUIET_DBFS - 6, 4.0)
        for i in range(0, len(x), BLOCK):
            ts._feed(x[i : i + BLOCK])
        stats = ts.get_stats()
    finally:
        ts.stop()
    warn = [e for e in events if e.get("status") == "mic_level"]
    assert warn and warn[0]["quiet"] and warn[0]["dbfs"] < QUIET_DBFS
    assert stats["mic"]["quiet"] and stats["mic"]["gain_db"] > 10
    assert stats["audio_level_input"] < 0.1  # the UI meter shows the raw level

    from src.ui.controller.ui_controller import describe_pipeline_event

    level, msg = describe_pipeline_event(warn[0])
    assert level == "WARN" and msg.startswith("Мікрофон тихий")
