"""Simultaneous translation units (spec 2026-10-31 §4, owner decision 1)."""

import pytest

from src.core.simul import (
    CLAUSE,
    ENDPOINT,
    HARD,
    SENTENCE,
    STOP,
    SourceLedger,
    VoskTrack,
    Word,
    ends_sentence,
)


def ws(text, start=0.0, step=0.3):
    return [
        Word(w, start + i * step, start + (i + 1) * step)
        for i, w in enumerate(text.split())
    ]


@pytest.mark.parametrize(
    "word, ends",
    [
        ("факту.", True),
        ("так?", True),
        ("ні!", True),
        ("вертикально».", True),
        ('"вертикально."', True),
        ("про...", False),
        ("про…", False),
        ("ДОКИ", False),
        ("рядки,", False),
    ],
)
def test_ends_sentence(word, ends):
    assert ends_sentence(word) is ends


def test_sentence_end_cuts_a_unit_and_the_rest_waits():
    led = SourceLedger()
    led.commit(ws("Не витрачайте час. Картки без"))
    (unit,) = led.units()
    assert (unit.text, unit.reason) == ("Не витрачайте час.", SENTENCE)
    assert (unit.t0, unit.t1) == (0.0, pytest.approx(0.9))
    led.partial = "фізичної"
    assert led.tail() == "Картки без фізичної"
    assert led.after(unit.end_index) == "Картки без фізичної"
    assert led.next_unit() is None


def test_endpoint_closes_the_committed_words():
    led = SourceLedger()
    led.commit(ws("інформація про..."), eos=True)
    (unit,) = led.units()
    assert (unit.text, unit.reason) == ("інформація про...", ENDPOINT)
    led.commit(ws("далі", 5.0))
    assert led.next_unit() is None
    led.endpoint()
    assert led.next_unit().text == "далі"


def test_clause_mark_cuts_only_after_twelve_words():
    led = SourceLedger()
    short = "раз, два три чотири п'ять шість сім вісім дев'ять десять одинадцять,"
    led.commit(ws(short))  # 11 words: the comma after word 1 does not cut
    assert led.next_unit() is None
    led.commit(ws("дванадцять, далі", 4.0))
    unit = led.next_unit()
    assert unit.reason == CLAUSE and len(unit.words) == 12
    assert led.tail() == "далі"


def test_hard_cut_at_twenty_words():
    led = SourceLedger()
    led.commit(ws(" ".join(f"w{i}" for i in range(25))))
    unit = led.next_unit()
    assert unit.reason == HARD and len(unit.words) == 20
    assert led.next_unit() is None
    assert led.flush().reason == STOP and led.flush() is None


def test_units_never_overlap():
    led = SourceLedger()
    led.commit(ws("Раз. Два. Три."))
    units = led.units()
    assert [u.text for u in units] == ["Раз.", "Два.", "Три."]
    assert [w for u in units for w in u.words] == led.words


def test_vosk_hint_is_the_words_of_the_span_with_margin():
    v = VoskTrack()
    v.on_message(
        {
            "type": "final",
            "language": "uk",
            "words": [
                {"word": "раз", "start": 0.0, "end": 0.4, "conf": 0.9},
                {"word": "два", "start": 1.0, "end": 1.4, "conf": 0.8},
            ],
            "audio_end": 1.6,
        }
    )
    v.on_message(
        {
            "type": "partial",
            "words": [{"word": "три", "start": 2.0, "end": 2.2}],
            "audio_end": 2.4,
        }
    )
    assert v.covered == 2.2 and v.language == "uk"  # its latest word, not audio_end
    assert v.covers(2.4) and not v.covers(2.5)
    assert [w.text for w in v.hint(0.0, 0.5)] == ["раз"]
    assert [w.text for w in v.hint(1.0, 2.0)] == ["два", "три"]
    v.forget_before(1.0)
    assert [w.text for w in v.finals] == ["два"]
