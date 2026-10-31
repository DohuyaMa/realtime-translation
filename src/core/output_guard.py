"""Guard against LLM output that is not a translation.

Small models sometimes answer the transcript instead of translating it
("The content is missing. Please provide the Polish text ..."), most often
for one- to three-word fragments. Such a reply must never reach TTS (owner
2026-10-31). The checks are deliberately plain so they never reject a real
translation for the words it uses:

* ``too_long``: output length against source length;
* ``foreign_script``: Cyrillic letters in the (English) translation - an
  untranslated or misrecognized source word left in the output
  ("("Niшки")", live test 2026-10-31 §4);
* ``next_leak`` (simultaneous mode, spec 2026-10-31 §4): the request's
  ``NEXT (do not translate)`` words got into the output - the prompt labels
  (``TEXT:`` / ``HINT:`` / ``NEXT``) are echoed, a run of
  ``NEXT_COPY_WORDS`` NEXT words is copied, or with NEXT given the output is
  more than ``NEXT_RATIO`` x TEXT + ``NEXT_SLACK`` words (an English
  translation of uk/pl/ru runs ~1.2-1.5 x the source words).
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
NEXT_RATIO = 2.0
NEXT_SLACK = 4
NEXT_COPY_WORDS = 3
_LABELS = re.compile(r"\b(TEXT|HINT)\s*:|\bNEXT\b")
_WORD = re.compile(r"[^\W\d_]+")


def max_output_words(source_text: str) -> int:
    return max(MIN_MAX_WORDS, MAX_WORDS_RATIO * len(source_text.split()))


def _copies(output: str, next_text: str) -> bool:
    out = " " + " ".join(_WORD.findall(output.lower())) + " "
    nxt = _WORD.findall(next_text.lower())
    return any(
        " " + " ".join(nxt[i : i + NEXT_COPY_WORDS]) + " " in out
        for i in range(len(nxt) - NEXT_COPY_WORDS + 1)
    )


def reject_reason(source_text: str, output: str, next_text: str = "") -> Optional[str]:
    """Why ``output`` (or the output so far) is not a translation of
    ``source_text`` (the request's TEXT; ``next_text`` = its NEXT):
    ``"too_long"`` / ``"foreign_script"`` / ``"next_leak"``, else None."""
    if len(output.split()) > max_output_words(source_text):
        return "too_long"
    if _FOREIGN_SCRIPT.search(output):
        return "foreign_script"
    if _LABELS.search(output):
        return "next_leak"
    if next_text.strip():
        if len(output.split()) > NEXT_RATIO * len(source_text.split()) + NEXT_SLACK:
            return "next_leak"
        if _copies(output, next_text):
            return "next_leak"
    return None
