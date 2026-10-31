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
