"""LLM output guard: a chat/meta reply never reaches TTS (owner 2026-10-31)."""

import pytest

from src.core.output_guard import reject_reason

OWNER_REPLY = (
    "The content is missing. Please provide the Polish text you would like me"
    " to translate into English, and I will give you a natural-sounding"
    " translation with any necessary context-based corrections for"
    " speech-recognition errors."
)


@pytest.mark.parametrize("source", ["медіа", "медіа контент"])
def test_owner_cases_are_rejected(source):
    assert reject_reason(source, OWNER_REPLY) == "too_long"


@pytest.mark.parametrize(
    "source, output",
    [
        ("media", "Media"),
        ("dziękuję bardzo", "Thank you very much."),
        ("no dobra", "Okay then."),
        ("jak się masz", "How are you?"),
        ("tak", "one two three four five six seven eight"),  # floor of 8
    ],
)
def test_short_translations_pass(source, output):
    assert reject_reason(source, output) is None


def test_normal_long_translation_passes():
    source = (
        "Wczoraj wieczorem poszliśmy z przyjaciółmi do nowej restauracji w"
        " centrum miasta i jedzenie było naprawdę bardzo dobre"
    )
    output = (
        "Last night we went with friends to a new restaurant in the city"
        " centre and the food was very good."
    )
    assert len(output.split()) == 20
    assert reject_reason(source, output) is None


def test_output_over_three_times_the_source_is_rejected():
    assert reject_reason("tak", "one two three four five six seven eight nine") == (
        "too_long"
    )
    source = "a b c d e"  # 5 words -> limit 15
    assert reject_reason(source, " ".join(["w"] * 15)) is None
    assert reject_reason(source, " ".join(["w"] * 16)) == "too_long"


@pytest.mark.parametrize(
    "output",
    [
        # live test 2026-10-31 §4: "миші" -> ASR "Нишки" -> left in the output
        'To move tabs, press the "Tabs" panel with the right mouse button'
        ' ("Niшки") and select "Show Tabs Vertically".',
        "Okay, привіт.",
    ],
)
def test_cyrillic_in_the_english_translation_is_rejected(output):
    source = "Щоб перемістити вкладки, натисніть панель вкладок правою кнопкою"
    assert reject_reason(source, output) == "foreign_script"


def test_latin_diacritics_and_dashes_pass():
    assert reject_reason("kawiarnia", "Café — naïve façade.") is None


def test_next_leakage_is_rejected():
    text = "Не витрачайте час на пошук інформації."  # 6 words: too_long > 18
    nxt = "Картки постійного покупця отримуйте бонуси"
    assert reject_reason(text, "Don't waste time searching for information.", nxt) is None
    # the NEXT part translated too: 17 words > 2 x 6 + 4
    leaked = (
        "Don't waste time searching for information. Loyalty cards from regular"
        " customers, get your bonuses right now please."
    )
    assert reject_reason(text, leaked, nxt) == "next_leak"
    # without NEXT the length rule is the plain too_long one
    assert reject_reason(text, leaked) is None
    # prompt labels echoed
    assert reject_reason(text, "TEXT: Don't waste time.", nxt) == "next_leak"
    assert reject_reason(text, "Don't waste time. NEXT", "") == "next_leak"
    # NEXT words copied (Latin source)
    out = "Don't waste time karty stałego klienta"
    assert reject_reason("Nie trać czasu.", out, "karty stałego klienta") == "next_leak"
