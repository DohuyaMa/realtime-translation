"""Simultaneous translation units (spec 2026-10-31 §4, owner decision 1)."""

import pytest

from src.core.simul import (
    CLAUSE,
    ENDPOINT,
    HARD,
    SENTENCE,
    STOP,
    SHORT_HOLD_S,
    SourceLedger,
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


# -- short endpoint pieces (live test 2026-11-01 §18) -------------------------


def test_short_endpoint_piece_joins_the_next_unit_live_example():
    """«використання принципів» + pause + «зеротраст передбачає ...» (live
    units #28/#29): one unit, not a two-word fragment."""
    led = SourceLedger()
    led.commit(ws("використання принципів", 125.7, 0.8), eos=True, now=10.0)
    assert led.next_unit(10.2) is None  # within SHORT_HOLD_S
    led.partial = "зеротраст"
    assert led.next_unit(11.5) is None  # whisper hears more speech
    led.commit(ws("зеротраст передбачає що жоден користувач", 127.5), now=12.0)
    assert led.next_unit(12.0) is None
    led.endpoint(13.0)
    unit = led.next_unit(13.0)
    assert unit.text == (
        "використання принципів зеротраст передбачає що жоден користувач"
    )
    assert unit.reason == ENDPOINT
    assert led.short_merged == 1


def test_short_endpoint_piece_merged_into_a_sentence_cut():
    led = SourceLedger()
    led.commit(ws("автентифікації авторизація"), eos=True, now=0.0)
    assert led.next_unit(0.1) is None
    led.commit(ws("багатофакторна перевірка особи. Далі", 2.0), now=0.5)
    unit = led.next_unit(0.5)
    assert unit.text == "автентифікації авторизація багатофакторна перевірка особи."
    assert unit.reason == SENTENCE
    assert led.short_merged == 1


def test_short_endpoint_piece_is_cut_alone_when_the_speaker_stopped():
    led = SourceLedger()
    led.commit(ws("так звісно"), eos=True, now=0.0)
    assert led.next_unit(SHORT_HOLD_S - 0.1) is None
    unit = led.next_unit(SHORT_HOLD_S + 0.01)
    assert (unit.text, unit.reason) == ("так звісно", ENDPOINT)
    assert led.short_merged == 0


@pytest.mark.parametrize(
    "text",
    [
        "окремі департамент нерідко впроваджують",  # 4 words: a unit
        "Добре.",  # a sentence: a unit
    ],
)
def test_long_or_finished_endpoint_piece_is_cut_at_once(text):
    led = SourceLedger()
    led.commit(ws(text), eos=True, now=0.0)
    led.partial = "далі"
    assert led.next_unit(0.0).text == text


def test_short_piece_without_a_clock_is_cut_at_once():
    led = SourceLedger()
    led.commit(ws("інформація про"), eos=True)
    assert led.next_unit().text == "інформація про"
