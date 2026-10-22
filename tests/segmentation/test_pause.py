import numpy as np
import pytest

from src.segmentation import (
    WINDOW_SAMPLES,
    PauseSegmenter,
    SegmenterConfig,
)

W = WINDOW_SAMPLES / 16000  # 0.032 s


def feed_probs(seg, probs):
    out = []
    for p in probs:
        window = np.full(WINDOW_SAMPLES, 1000 if p >= 0.5 else 0, np.int16)
        out.extend(seg.feed(window, p))
    return out


def feed_indexed(seg, probs):
    """Feed windows whose samples all equal the window's stream index."""
    out = []
    for i, p in enumerate(probs):
        out.extend(seg.feed(np.full(WINDOW_SAMPLES, i, np.int16), p))
    return out


def window_ids(segment):
    """Stream indices of the windows a segment's audio is made of."""
    return segment.audio.reshape(-1, WINDOW_SAMPLES)[:, 0].tolist()


def test_translate_phrase_ends_on_700ms_silence():
    # ~1 s speech, 704 ms silence
    segs = feed_probs(
        PauseSegmenter(SegmenterConfig.translate()), [0.9] * 31 + [0.0] * 22
    )
    assert [s.reason for s in segs] == ["end"]
    # trailing silence trimmed to 300 ms -> 9 windows
    assert segs[0].start_s == 0.0
    assert segs[0].end_s == pytest.approx((31 + 9) * W)
    assert len(segs[0].audio) == (31 + 9) * WINDOW_SAMPLES
    assert segs[0].audio.dtype == np.int16


def test_short_blip_discarded():
    seg = PauseSegmenter(SegmenterConfig.translate())
    assert feed_probs(seg, [0.9] * 6 + [0.0] * 25) == []  # 0.2 s + 0.8 s
    assert seg.flush() is None


def test_preroll_prepends_last_300ms_before_speech():
    seg = PauseSegmenter(SegmenterConfig.translate())
    feed_indexed(seg, [0.0] * 20 + [0.9] * 20)
    out = seg.flush()
    # 300 // 32 = 9 pre-roll windows: stream windows 11..19
    assert out.start_s == pytest.approx(11 * W)
    assert window_ids(out) == list(range(11, 40))


def test_soft_cut_after_soft_max_on_short_pause():
    probs = (
        [0.9] * 260 + [0.0] * 9 + [0.9] * 40
    )  # 8.3 s speech, 288 ms pause, more speech
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), probs)
    assert segs[0].reason == "soft" and 8.3 <= segs[0].end_s <= 8.6


def test_soft_cut_in_middle_of_pause_and_tail_carried():
    seg = PauseSegmenter(SegmenterConfig.translate())
    segs = feed_indexed(seg, [0.9] * 260 + [0.0] * 9 + [0.9] * 40)
    # soft fires on window 267 (8 silent windows = 256 ms >= 250 ms);
    # the pause so far is windows 260..267, its middle is window 264.
    assert [s.reason for s in segs] == ["soft"]
    assert segs[0].end_s == pytest.approx(264 * W)
    assert window_ids(segs[0]) == list(range(264))
    # the silent tail (264..268) becomes the next segment's pre-roll
    rest = seg.flush()
    assert rest.start_s == segs[0].end_s
    assert window_ids(rest) == list(range(264, 309))


def test_hard_cut_at_quietest_window_in_lookback():
    probs = [0.9] * 625
    probs[600] = 0.1  # 20 s of speech, dip at 19.2 s
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), probs)
    assert segs[0].reason == "hard" and abs(segs[0].end_s - 600 * 0.032) < 1e-6
    assert len(segs) == 1 and len(segs[0].audio) == 600 * WINDOW_SAMPLES


def test_hard_cut_ignores_dip_outside_lookback():
    probs = [0.9] * 625
    probs[500] = 0.0  # 3.9 s before the hard cut: outside the 1.5 s lookback
    probs[610] = 0.3
    segs = feed_probs(PauseSegmenter(SegmenterConfig.translate()), probs)
    assert segs[0].reason == "hard" and segs[0].end_s == pytest.approx(610 * W)


def test_hard_cut_carries_tail_without_loss():
    seg = PauseSegmenter(SegmenterConfig.translate())
    probs = [0.9] * 700 + [0.0] * 30
    segs = feed_probs(seg, probs)
    assert [s.reason for s in segs] == ["hard", "end"]
    assert segs[1].start_s == segs[0].end_s
    # all probs equal in the lookback -> the latest window wins (cut before 624)
    assert segs[0].end_s == pytest.approx(624 * W)
    # "end" fires after 22 silent windows; trailing silence is trimmed
    # to 300 ms -> 300 // 32 = 9 windows
    # nothing lost, nothing duplicated
    assert sum(len(s.audio) for s in segs) == (700 + 300 // 32) * 512


def test_hard_cut_samples_identity():
    seg = PauseSegmenter(SegmenterConfig.translate())
    segs = feed_indexed(seg, [0.9] * 700 + [0.0] * 30)
    ids = [i for s in segs for i in window_ids(s)]
    assert ids == list(range(709))


def test_transcribe_ignores_pauses_before_chunk_min():
    cfg = SegmenterConfig.transcribe(chunk_min_s=10, chunk_max_s=20)
    probs = ([0.9] * 100 + [0.0] * 25) * 4  # 4 x (3.2 s speech + 0.8 s pause)
    segs = feed_probs(PauseSegmenter(cfg), probs)
    assert len(segs) == 1 and segs[0].reason == "soft" and segs[0].end_s >= 10.0
    # cut in the middle of the 16-window pause 350..365
    assert segs[0].end_s == pytest.approx(358 * W)


def test_transcribe_trims_long_silence_to_keep_silence():
    # chunk_min > stream length -> no soft cut
    cfg = SegmenterConfig.transcribe(chunk_min_s=30, chunk_max_s=60)
    seg = PauseSegmenter(cfg)
    feed_probs(seg, [0.9] * 50 + [0.0] * 313 + [0.9] * 50)  # 10 s silence inside
    out = seg.flush()
    assert len(out.audio) <= (100 + 2000 // 32 + 1) * 512
    # exactly 2000 // 32 = 62 silent windows kept; timing follows the real stream
    assert len(out.audio) == (100 + 62) * 512
    assert out.reason == "flush"
    assert out.start_s == 0.0 and out.end_s == pytest.approx(413 * W)


def test_flush_returns_pending_speech_and_none_for_blip():
    seg = PauseSegmenter(SegmenterConfig.translate())
    feed_probs(seg, [0.0] * 3 + [0.9] * 20 + [0.0] * 5)  # 640 ms speech, short pause
    out = seg.flush()
    assert out.reason == "flush"
    assert out.start_s == 0.0 and out.end_s == pytest.approx(28 * W)
    assert len(out.audio) == 28 * WINDOW_SAMPLES
    assert seg.flush() is None  # nothing pending any more

    blip = PauseSegmenter(SegmenterConfig.translate())
    feed_probs(blip, [0.9] * 5)  # 160 ms < min_speech_ms
    assert blip.flush() is None


def test_reconfigure_applies_from_next_window():
    # switch translate -> transcribe mid-stream: no "end" on a 0.8 s pause
    seg = PauseSegmenter(SegmenterConfig.translate())
    assert feed_probs(seg, [0.9] * 50 + [0.0] * 21) == []  # 672 ms < 700 ms
    seg.reconfigure(SegmenterConfig.transcribe())
    assert feed_probs(seg, [0.0] * 4 + [0.9] * 50) == []  # pause reaches 800 ms
    out = seg.flush()
    assert out.start_s == 0.0 and len(out.audio) == 125 * WINDOW_SAMPLES

    # transcribe -> translate: the running pause is judged by the new
    # config on the very next window
    seg = PauseSegmenter(SegmenterConfig.transcribe())
    assert feed_probs(seg, [0.9] * 50 + [0.0] * 30) == []
    seg.reconfigure(SegmenterConfig.translate())
    segs = feed_probs(seg, [0.0])
    assert [s.reason for s in segs] == ["end"]
    assert segs[0].end_s == pytest.approx((50 + 9) * W)


def test_reset_drops_pending_segment_and_restarts_clock():
    seg = PauseSegmenter(SegmenterConfig.translate())
    feed_probs(seg, [0.9] * 50)
    seg.reset()
    assert seg.flush() is None
    feed_probs(seg, [0.9] * 20)
    out = seg.flush()
    assert out.start_s == 0.0 and len(out.audio) == 20 * WINDOW_SAMPLES


def test_feed_rejects_bad_window():
    seg = PauseSegmenter(SegmenterConfig.translate())
    with pytest.raises(ValueError):
        seg.feed(np.zeros(100, np.int16), 0.9)
    with pytest.raises(ValueError):
        seg.feed(np.zeros(WINDOW_SAMPLES, np.float32), 0.9)


def test_validate_rejects_bad_config():
    with pytest.raises(ValueError):
        SegmenterConfig(soft_max_ms=5000, hard_max_ms=4000).validate()
    for bad in (
        SegmenterConfig(soft_max_ms=5000, hard_max_ms=5000),
        SegmenterConfig(lookback_ms=20_001),
        SegmenterConfig(min_speech_ms=0),
        SegmenterConfig(end_silence_ms=0),
        SegmenterConfig(soft_silence_ms=-1),
        SegmenterConfig(keep_silence_ms=0),
        SegmenterConfig(preroll_ms=0),
        SegmenterConfig(threshold=0.0),
    ):
        with pytest.raises(ValueError):
            bad.validate()
    with pytest.raises(ValueError):
        PauseSegmenter(SegmenterConfig(soft_max_ms=5000, hard_max_ms=4000))
    seg = PauseSegmenter(SegmenterConfig.translate())
    with pytest.raises(ValueError):
        seg.reconfigure(SegmenterConfig(lookback_ms=30_000))


def test_presets():
    t = SegmenterConfig.translate()
    assert t == SegmenterConfig()
    t.validate()
    c = SegmenterConfig.transcribe()
    c.validate()
    assert (c.end_silence_ms, c.soft_max_ms, c.soft_silence_ms) == (None, 300_000, 500)
    assert (c.hard_max_ms, c.lookback_ms, c.keep_silence_ms) == (600_000, 5000, 2000)
    assert (c.min_speech_ms, c.preroll_ms, c.threshold) == (400, 300, 0.5)
    assert SegmenterConfig.transcribe(10, 20).soft_max_ms == 10_000
