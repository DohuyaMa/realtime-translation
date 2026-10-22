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
