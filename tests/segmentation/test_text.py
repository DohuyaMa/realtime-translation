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
    "text, ready, rest",
    [
        ("Чикає ДОКИ", "", "Чикає ДОКИ"),
        ("До паузи, по факту.", "До паузи, по факту.", ""),
        (
            "Не витрачайте час. Без фізичної карти, інформація про...",
            "Не витрачайте час.",
            "Без фізичної карти, інформація про...",
        ),
        # §11 #8: whisper's final full stop is trusted (also after a cut)
        (
            "Підписуйте і доповнюйте примітками файли PDF. Коли вам"
            " знадобиться підписати файл PDF, можна обійтися без завантаження"
            " і друку.",
            "Підписуйте і доповнюйте примітками файли PDF. Коли вам"
            " знадобиться підписати файл PDF, можна обійтися без завантаження"
            " і друку.",
            "",
        ),
        # §12 #18: an ellipsis is not an end
        (
            "сайти і веб-додатки для бізнесу, плюс авто-тестер, плейврайд...",
            "",
            "сайти і веб-додатки для бізнесу, плюс авто-тестер, плейврайд...",
        ),
        ("", "", ""),
    ],
)
def test_split_complete(text, ready, rest):
    assert split_complete(text) == (ready, rest)
