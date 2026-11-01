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
* ``meta``: the model talks about the request instead of translating it
  ("I notice you provided ...", "please provide", "the transcript", an
  explanation of TEXT / SOUND); ~4/35 sound-mode outputs in the 2026-11-01
  bench (context/phonetic-bench-2026-11-01.md), 0 hits on the 39 live
  translations of that day (tests/test_output_guard.py);
* ``next_leak`` (simultaneous mode, spec 2026-10-31 §4): the request's
  ``NEXT (do not translate)`` words got into the output - the prompt labels
  (``TEXT:`` / ``SOUND:`` / ``NEXT``) are echoed, a run of
  ``NEXT_COPY_WORDS`` NEXT words is copied, or with NEXT given the output is
  more than ``NEXT_RATIO`` x TEXT + ``NEXT_SLACK`` words (an English
  translation of uk/pl/ru runs ~1.2-1.5 x the source words);
* ``too_short`` (only on the whole reply, ``complete=True``): a source of
  ``MIN_SOURCE_WORDS``+ words got fewer than ``MIN_WORDS_RATIO`` x its words
  back - the model dropped part of it (live test 2026-11-01, unit 26: a
  15-word enumeration -> 6 words, its second half gone). Calibrated on all
  ``_log_unit`` lines of 2026-11-01 plus the 39 recorded units of session
  20261101-154159: of 119 accepted translations of 5+ words the lowest ratio
  of a real one was 0.67 (6 -> 4 words; 0.88 from 8 words on), unit 26 was
  0.40. The whole-reply check is monotonic (the output only grows), so the
  edge sends a sentence to TTS only once ``min_output_words`` is reached.
"""

from __future__ import annotations

import math
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
_LABELS = re.compile(r"\b(TEXT|SOUND)\s*:|\bNEXT\b")
# talk about the request (case-insensitive) or the prompt's labels named as
# words (upper case, as the prompt writes them)
_META = re.compile(
    r"\bI notice\b"
    r"|\byou(?:'ve| have)? provided\b"
    r"|\bplease provide\b"
    r"|\b(?:me|you) to translate\b|\bno text to translate\b"
    r"|\btranscript(?:s|ion)?\b"
    r"|\bspeech[- ]recogni\w*|\bmisrecogni\w*|\brecogni[sz]er\b"
    r"|\bletter-by-letter\b"
    r"|\bhere(?:'s| is) the translation\b"
    r"|^\W*translation\s*:|\bnote\s*:",
    re.IGNORECASE,
)
_META_LABELS = re.compile(r"\b(?:TEXT|SOUND)\b")
_WORD = re.compile(r"[^\W\d_]+")
MIN_SOURCE_WORDS = 6
MIN_WORDS_RATIO = 0.5
_ALNUM = re.compile(r"[^\W_]")


def max_output_words(source_text: str) -> int:
    return max(MIN_MAX_WORDS, MAX_WORDS_RATIO * len(source_text.split()))


def word_count(text: str) -> int:
    """Words and numbers of ``text`` (what ``too_short`` compares): tokens
    with a letter or digit - a lone dash is none, «п'ять» is one."""
    return sum(1 for token in text.split() if _ALNUM.search(token))


def min_output_words(source_text: str) -> int:
    """Fewest words a whole translation of ``source_text`` may have
    (0 = no floor: a short source may translate into fewer words)."""
    n = word_count(source_text)
    return math.ceil(MIN_WORDS_RATIO * n) if n >= MIN_SOURCE_WORDS else 0


def _copies(output: str, next_text: str) -> bool:
    out = " " + " ".join(_WORD.findall(output.lower())) + " "
    nxt = _WORD.findall(next_text.lower())
    return any(
        " " + " ".join(nxt[i : i + NEXT_COPY_WORDS]) + " " in out
        for i in range(len(nxt) - NEXT_COPY_WORDS + 1)
    )


def reject_reason(
    source_text: str, output: str, next_text: str = "", complete: bool = False
) -> Optional[str]:
    """Why ``output`` (or the output so far) is not a translation of
    ``source_text`` (the request's TEXT; ``next_text`` = its NEXT):
    ``"too_long"`` / ``"foreign_script"`` / ``"meta"`` / ``"next_leak"``,
    with ``complete`` (``output`` is the whole reply) also ``"too_short"``;
    else None."""
    if len(output.split()) > max_output_words(source_text):
        return "too_long"
    if _FOREIGN_SCRIPT.search(output):
        return "foreign_script"
    if _LABELS.search(output):
        return "next_leak"
    if _META.search(output) or _META_LABELS.search(output):
        return "meta"
    if next_text.strip():
        if len(output.split()) > NEXT_RATIO * len(source_text.split()) + NEXT_SLACK:
            return "next_leak"
        if _copies(output, next_text):
            return "next_leak"
    if complete and word_count(output) < min_output_words(source_text):
        return "too_short"
    return None
