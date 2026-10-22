"""Collapse Whisper repetition loops."""

from __future__ import annotations

import re
from typing import List


def normalize_word(w: str) -> str:
    """Strip leading/trailing punctuation for comparison."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", w, flags=re.UNICODE).lower()


def filter_repetitions(text: str) -> str:
    """Collapse consecutive repeated words or short phrases (window up to 5 words).

    Examples:
        "hello hello hello"      -> "hello"
        "I think think think"    -> "I think"
        "я не знаю, я не знаю"  -> "я не знаю,"
        "Добре. Добре"           -> "Добре."
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 2:
        return text

    normed = [normalize_word(w) for w in words]

    result: List[str] = []
    i = 0
    while i < len(words):
        result.append(words[i])
        moved = False
        for win in range(1, min(6, len(words) - i + 1)):
            seq_n = normed[i : i + win]
            j = i + win
            count = 0
            while j + win <= len(words) and normed[j : j + win] == seq_n:
                j += win
                count += 1
            if count > 0:
                if win > 1:
                    result.extend(words[i + 1 : i + win])
                i = j
                moved = True
                break
        if not moved:
            i += 1

    return " ".join(result)
