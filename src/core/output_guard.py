"""Guard against LLM output that is not a translation.

Small models sometimes answer the transcript instead of translating it
("The content is missing. Please provide the Polish text ..."), most often
for one- to three-word fragments. Such a reply must never reach TTS (owner
2026-10-31). The checks are deliberately plain so they never reject a real
translation for the words it uses:

* ``too_long``: output length against source length;
* ``foreign_script``: Cyrillic letters in the (English) translation - an
  untranslated or misrecognized source word left in the output
  ("("Niшки")", live test 2026-10-31 §4).
"""

from __future__ import annotations

import re
from typing import Optional

# A translation is at most a few times longer than its source; the floor keeps
# short sources (one word -> "Thank you very much.") from tripping it.
MIN_MAX_WORDS = 8
MAX_WORDS_RATIO = 3
# the target language is English (core.prompt.TARGET_LANG)
_FOREIGN_SCRIPT = re.compile(r"[\u0400-\u052f]")


def max_output_words(source_text: str) -> int:
    return max(MIN_MAX_WORDS, MAX_WORDS_RATIO * len(source_text.split()))


def reject_reason(source_text: str, output: str) -> Optional[str]:
    """Why ``output`` (or the output so far) is not a translation of
    ``source_text``: ``"too_long"`` / ``"foreign_script"``, else None."""
    if len(output.split()) > max_output_words(source_text):
        return "too_long"
    if _FOREIGN_SCRIPT.search(output):
        return "foreign_script"
    return None
