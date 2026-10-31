"""Guard against LLM output that is not a translation.

Small models sometimes answer the transcript instead of translating it
("The content is missing. Please provide the Polish text ..."), most often
for one- to three-word fragments. Such a reply must never reach TTS (owner
2026-10-31). The check is deliberately plain - output length against source
length - so it never rejects a real translation for the words it uses.
"""

from __future__ import annotations

from typing import Optional

# A translation is at most a few times longer than its source; the floor keeps
# short sources (one word -> "Thank you very much.") from tripping it.
MIN_MAX_WORDS = 8
MAX_WORDS_RATIO = 3


def max_output_words(source_text: str) -> int:
    return max(MIN_MAX_WORDS, MAX_WORDS_RATIO * len(source_text.split()))


def reject_reason(source_text: str, output: str) -> Optional[str]:
    """``"too_long"`` when ``output`` (or the output so far) is clearly more
    than a translation of ``source_text``, else None."""
    if len(output.split()) > max_output_words(source_text):
        return "too_long"
    return None
