import builtins

import numpy as np

from src.audio.vad_stream import VadStream, make_vad
from src.segmentation import SegmenterConfig

RATE = 48000


def _energy(window: np.ndarray) -> float:
    return 1.0 if np.sqrt(np.mean((window / 32768.0) ** 2)) >= 0.008 else 0.0


def _signal() -> np.ndarray:
    t = np.arange(RATE * 3) / RATE
    tone = 0.3 * np.sin(2 * np.pi * 300 * t).astype(np.float32)
    gate = np.zeros_like(tone)
    gate[int(0.5 * RATE) : int(1.5 * RATE)] = 1.0
    return tone * gate


def _run(block: int, flush: bool = False):
    stream = VadStream(_energy, SegmenterConfig.translate(), RATE)
    sig = _signal()
    out = []
    for i in range(0, len(sig), block):
        out += stream.feed_block(sig[i : i + block])
    if flush:
        tail = stream.flush()
        if tail is not None:
            out.append(tail)
    return out


def test_blocks_of_any_size_produce_same_segments():
    a, b = _run(1024), _run(4000)
    assert len(a) == len(b) >= 1
    for x, y in zip(a, b):
        assert (x.start_s, x.end_s) == (y.start_s, y.end_s)
        assert np.array_equal(x.audio, y.audio)


def test_windows_match_whole_signal_resample():
    from scipy.signal import resample_poly

    seen = []

    def rec(window):
        seen.append(window.copy())
        return 0.0

    stream = VadStream(rec, SegmenterConfig.translate(), RATE)
    sig = _signal()
    for i in range(0, len(sig), 1000):
        stream.feed_block(sig[i : i + 1000])
    got = np.concatenate(seen).astype(np.float64) / 32767.0
    ref = resample_poly(sig, 1, 3)[: len(got)]
    assert len(got) >= 16000
    assert np.max(np.abs(got - ref)) < 2e-3


def test_flush_after_stop_returns_tail():
    stream = VadStream(_energy, SegmenterConfig.translate(), RATE)
    sig = _signal()[: int(1.0 * RATE)]  # speech still going
    for i in range(0, len(sig), 2048):
        stream.feed_block(sig[i : i + 2048])
    tail = stream.flush()
    assert len(tail) == 1 and len(tail[0].audio) > 0


def test_reconfigure_switches_mode():
    stream = VadStream(_energy, SegmenterConfig.translate(), RATE)
    stream.reconfigure(SegmenterConfig.transcribe())
    sig = _signal()
    out = []
    for i in range(0, len(sig), 4096):
        out += stream.feed_block(sig[i : i + 4096])
    # translate mode would have ended the phrase after 0.7 s of silence;
    # transcribe mode keeps accumulating until flush.
    assert out == []
    assert len(stream.flush()) == 1


def test_last_level_is_block_rms():
    stream = VadStream(_energy, SegmenterConfig.translate(), RATE)
    stream.feed_block(np.full(480, 0.5, dtype=np.float32))
    assert abs(stream.last_level() - 0.5) < 1e-4


def test_energy_fallback_when_silero_missing(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("pysilero_vad"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    vad = make_vad()
    assert vad(np.zeros(512, dtype=np.int16)) == 0.0
    assert vad(np.full(512, 10000, dtype=np.int16)) == 1.0


def test_flush_keeps_all_segments_from_padded_remainder():
    cfg = SegmenterConfig(
        min_speech_ms=64,
        soft_max_ms=160,
        soft_silence_ms=64,
        hard_max_ms=192,
        lookback_ms=96,
        preroll_ms=64,
    )
    stream = VadStream(lambda w: 1.0, cfg, RATE)
    n = 1536 * 20 + 700  # constant speech; remainder is a partial frame
    emitted = stream.feed_block(np.full(n, 0.3, dtype=np.float32))
    flushed = stream.flush()
    allsegs = emitted + flushed
    assert flushed
    ends = [s.end_s for s in allsegs]
    assert ends == sorted(ends)
    total = sum(len(s.audio) for s in allsegs)
    assert total >= 21 * 512  # 20 full windows plus the padded remainder
