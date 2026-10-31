import random

import pytest

from src.services.asr.chunking import plan_chunks


def test_short_audio_single_chunk():
    assert plan_chunks([], 100, 1000, 100) == [(0, 100)]


def test_empty_audio_no_chunks():
    assert plan_chunks([], 0, 1000, 100) == []


def test_cut_in_middle_of_longest_pause_in_lookback():
    assert plan_chunks([(0, 900), (950, 2500)], 2500, 1000, 200)[0] == (0, 925)


def test_no_pause_hard_cut():
    assert plan_chunks([(0, 2500)], 2500, 1000, 200)[0] == (0, 1000)


def test_picks_longest_overlap_and_ignores_pauses_outside_lookback():
    spans = [(0, 500), (700, 820), (840, 900), (960, 2500)]
    # window [800, 1000]: pauses (820, 840) len 20 and (900, 960) len 60;
    # (500, 700) lies outside the lookback window
    assert plan_chunks(spans, 2500, 1000, 200)[0] == (0, 930)


def test_pause_clipped_to_lookback_window():
    # pause (700, 2000) intersects [800, 1000] as (800, 1000) -> middle 900
    assert plan_chunks([(0, 700), (2000, 3000)], 3000, 1000, 200)[0] == (0, 900)


def test_unsorted_spans():
    assert plan_chunks([(950, 2500), (0, 900)], 2500, 1000, 200)[0] == (0, 925)


def test_last_chunk_ends_at_total():
    chunks = plan_chunks([(0, 2500)], 2500, 1000, 200)
    assert chunks == [(0, 1000), (1000, 2000), (2000, 2500)]


def test_rejects_bad_sizes():
    with pytest.raises(ValueError):
        plan_chunks([], 100, 0, 0)
    with pytest.raises(ValueError):
        plan_chunks([], 100, 10, -1)


@pytest.mark.parametrize("seed", range(50))
def test_chunks_contiguous_cover_all(seed):
    rng = random.Random(seed)
    total = rng.randint(1, 50_000)
    chunk, lookback = rng.randint(500, 5000), rng.randint(0, 400)
    spans, pos = [], 0
    while pos < total:
        start = pos + rng.randint(0, 300)
        end = min(total, start + rng.randint(1, 2000))
        if start < end:
            spans.append((start, end))
        pos = end
    chunks = plan_chunks(spans, total, chunk, lookback)
    assert chunks[0][0] == 0 and chunks[-1][1] == total
    for (a, b), (c, _) in zip(chunks, chunks[1:]):
        assert b == c
    for i, (a, b) in enumerate(chunks):
        assert a < b and b - a <= chunk
        if i < len(chunks) - 1:
            assert b - a >= chunk - lookback
    # each boundary is in the middle of a pause overlapping the lookback
    # window when there is one, otherwise exactly at the hard target
    pauses = [(e, s) for (_, e), (s, _) in zip(spans, spans[1:]) if s > e]
    for a, b in chunks[:-1]:
        t = a + chunk
        lo = max(t - lookback, a + 1)
        if any(min(pe, t) > max(ps, lo) for ps, pe in pauses):
            assert any(ps <= b <= pe for ps, pe in pauses)
        else:
            assert b == t
