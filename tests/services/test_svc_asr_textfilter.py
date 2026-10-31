import pytest

from src.services.asr.textfilter import filter_repetitions


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hello hello hello", "hello"),
        ("I think think think", "I think"),
        ("я не знаю, я не знаю", "я не знаю,"),
        ("Добре. Добре", "Добре."),
        ("one two three", "one two three"),
        ("", ""),
        ("single", "single"),
    ],
)
def test_filter_repetitions(text, expected):
    assert filter_repetitions(text) == expected


from src.services.asr.textfilter import (  # noqa: E402
    HALLUCINATION,
    LOW_CONFIDENCE,
    SegmentFilter,
    drop_reason,
)
from src.services.asr.whisper_engine import SegmentStats  # noqa: E402

GOOD = SegmentStats(no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.3)


@pytest.mark.parametrize(
    "text",
    [
        "Дякую за перегляд!",
        "Napisy stworzone przez społeczność Amara.org",
        "Субтитры сделал DimaTorzok",
        "Thanks for watching!",
        "Субтитри зроблені спільнотою Amara.org",
    ],
)
def test_known_hallucinations_dropped(text):
    assert drop_reason(text, GOOD) == HALLUCINATION
    assert drop_reason(text, None) == HALLUCINATION


@pytest.mark.parametrize(
    "stats",
    [
        SegmentStats(0.61, -1.01, 1.3),
        SegmentStats(0.1, -0.3, 2.41),
    ],
)
def test_low_confidence_dropped(stats):
    assert drop_reason("що можна зробити", stats) == LOW_CONFIDENCE


@pytest.mark.parametrize(
    "stats",
    [SegmentStats(0.61, -0.3, 1.3), SegmentStats(0.1, -1.01, 1.3)],
)
def test_one_bad_silence_signal_alone_keeps_speech(stats):
    # fast/accented speech has low logprob; quiet speech a high no_speech_prob
    assert drop_reason("що можна зробити", stats) is None


def test_phrases_people_really_say_are_kept():
    assert drop_reason("Дякую за увагу.", GOOD) is None


def test_real_speech_kept_even_if_it_contains_a_phrase():
    assert drop_reason("що можна зробити із віспером", GOOD) is None
    assert drop_reason("я хочу сказати дякую за перегляд фільму", GOOD) is None
    assert drop_reason("що можна зробити", None) is None


def test_segment_filter_counts():
    f = SegmentFilter()
    assert f.keep("привіт", GOOD)
    assert not f.keep("Дякую за перегляд!", GOOD)
    assert not f.keep("шум", SegmentStats(0.9, -1.2, 1.0))
    assert f.counts() == {HALLUCINATION: 1, LOW_CONFIDENCE: 1}
