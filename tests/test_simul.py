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


# -- sentence-end finality (live 2026-11-01 §14) -----------------------------


def lw(*items):
    """Words with explicit times: (text, start, end)."""
    return [Word(t, s, e) for t, s, e in items]


def test_period_at_a_pause_inside_a_sentence_does_not_cut():
    """«…оновлюється кожній екрані.» + «ноль ,5 секунды.»: whisper's period
    with no audio gap before a lowercase word goes on the sentence."""
    led = SourceLedger()
    led.commit(
        lw(
            ("Потокове", 42.2, 42.9),
            ("розпізнавання", 42.9, 43.8),
            ("оновлюється", 43.8, 44.7),
            ("кожній", 44.7, 45.1),
            ("екрані.", 45.1, 45.64),
            ("ноль", 45.66, 46.0),
            (",5", 46.0, 46.3),
            ("секунды.", 46.3, 47.36),
        ),
        eos=True,
    )
    (unit,) = led.units()
    assert unit.text == (
        "Потокове розпізнавання оновлюється кожній екрані. ноль ,5 секунды."
    )
    assert unit.reason == SENTENCE


def test_tiny_trailing_piece_joins_the_sentence_before_it():
    """«…модель індексів.» + «так.» (204.48-204.58 s) + «перекладає
    краще.» after a 0.82 s pause: «так.» is never a unit of its own."""
    led = SourceLedger()
    led.commit(
        lw(
            ("тестувати", 201.22, 201.8),
            ("не", 201.8, 201.9),
            ("пропоную,", 201.9, 202.5),
            ("бо", 202.5, 202.7),
            ("українська", 202.7, 203.3),
            ("модель", 203.3, 203.7),
            ("індексів.", 203.7, 204.48),
            ("так.", 204.48, 204.58),
            ("перекладає", 205.4, 205.9),
            ("краще.", 205.9, 206.38),
        ),
        eos=True,
    )
    units = led.units()
    assert [u.text for u in units] == [
        "тестувати не пропоную, бо українська модель індексів. так.",
        "перекладає краще.",
    ]


def test_tiny_piece_at_the_head_joins_the_next_one():
    led = SourceLedger()
    led.commit(lw(("так.", 204.48, 204.58), ("перекладає", 205.4, 205.9)))
    led.commit(lw(("краще.", 205.9, 206.38)), eos=True)
    (unit,) = led.units()
    assert unit.text == "так. перекладає краще."


def test_new_sentence_or_a_gap_cuts_at_the_period():
    led = SourceLedger()
    led.commit(lw(("Добре.", 0.0, 0.5), ("Далі", 0.5, 0.9), ("йдемо.", 0.9, 1.4)))
    led.commit(lw(("це", 1.5, 1.7), ("краще.", 1.7, 2.0)), eos=True)
    assert [u.text for u in led.units()] == ["Добре.", "Далі йдемо.", "це краще."]


def test_sentence_end_waits_briefly_for_a_lowercase_continuation():
    led = SourceLedger()
    led.commit(
        lw(("зараз", 0.0, 0.4), ("кожній", 0.4, 0.8), ("екрані.", 0.8, 1.2)), now=10.0
    )
    led.partial = "ноль ,5 секунди"
    assert led.next_unit(now=10.2) is None  # holds
    led.commit(lw(("ноль", 1.2, 1.5)), now=10.4)
    assert led.next_unit(now=10.4) is None  # merged, no sentence end yet
    led.commit(lw(("секунди.", 1.5, 2.0)), now=11.0)
    led.partial = ""
    assert led.next_unit(now=11.0).text == "зараз кожній екрані. ноль секунди."


def test_sentence_end_hold_is_bounded_and_skipped_when_nothing_follows():
    led = SourceLedger()
    led.commit(lw(("Добрий", 0.0, 0.4), ("день.", 0.4, 0.8)), now=10.0)
    led.partial = "як справи"
    assert led.next_unit(now=10.3) is None
    assert led.next_unit(now=10.0 + 0.5).text == "Добрий день."  # SENTENCE_HOLD_S
    led.commit(lw(("Як", 1.0, 1.2), ("справи?", 1.2, 1.6)), now=11.0)
    led.partial = ""  # whisper has nothing after it: no wait
    assert led.next_unit(now=11.0).text == "Як справи?"
    led.commit(lw(("Добре.", 2.0, 2.4)), eos=True, now=12.0)
    led.partial = "далі"
    assert led.next_unit(now=12.0).text == "Добре."  # endpoint: no wait


def test_hard_limit_wins_over_a_continuation():
    led = SourceLedger()
    text = " ".join(f"w{i}" for i in range(19)) + " кінець."
    led.commit(ws(text + " далі"))
    unit = led.next_unit()
    assert len(unit.words) == 20 and unit.reason == SENTENCE


def test_kaldi_idle_covers_heard_silence_minus_its_lag():
    v = VoskTrack()
    v.on_message(
        {"type": "final", "words": [{"word": "або", "start": 44.8, "end": 45.21}]}
    )
    assert not v.covers(60.79)  # «Дякую.» 59.67-60.79: no word since 45.21
    v.on_message({"type": "idle", "at": 61.5})
    assert not v.covers(60.79)  # Kaldi may still be ~1 s behind
    v.on_message({"type": "idle", "at": 61.8})
    assert v.covers(60.79) and v.hint(59.67, 60.79) == []
    assert v.covered == 45.21  # word coverage unchanged
