"""Sentence completeness of recognized text (live test 2026-10-31 §2/§4-§6)."""

import pytest

from src.segmentation import ends_sentence, split_complete


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
        ("рейси...", False),
        ("ДОКИ", False),
        ("рядки,", False),
    ],
)
def test_ends_sentence(word, ends):
    assert ends_sentence(word) is ends


@pytest.mark.parametrize(
    "text, cut, ready, rest",
    [
        ("Чикає ДОКИ", False, "", "Чикає ДОКИ"),
        ("До паузи, по факту.", False, "До паузи, по факту.", ""),
        (
            "Не витрачайте час. Без фізичної карти, інформація про...",
            False,
            "Не витрачайте час.",
            "Без фізичної карти, інформація про...",
        ),
        # cut without a pause: Whisper's final full stop is not trusted
        (
            "Можна обійтися без друку. Використовуйте екстремент.",
            True,
            "Можна обійтися без друку.",
            "Використовуйте екстремент.",
        ),
        ("Використовуйте екстремент.", True, "", "Використовуйте екстремент."),
        ("", False, "", ""),
    ],
)
def test_split_complete(text, cut, ready, rest):
    assert split_complete(text, cut) == (ready, rest)
