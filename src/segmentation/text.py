"""Sentence completeness of recognized text (translate mode).

A pause is not a sentence end: speakers pause mid-sentence ("Чекає доки… |
до паузи"), and a long segment is cut at the best pause even without one
(``soft``/``hard``). A dangling fragment sent alone makes the LLM
transliterate or invent the rest (live test 2026-10-31 §2, §5, §6), so the
edge translates only complete sentences and holds the incomplete tail until
the next recognized segment continues it.

Rules:

* A word ends a sentence when, without closing quotes/brackets, it ends with
  ``.``, ``!`` or ``?`` - but not with an ellipsis (``..`` / ``…``): an
  ellipsis is speech trailing off, not an end.
* ``cut=True`` (the segment was cut without a sentence-ending pause): the
  last sentence is held too, even with a full stop - Whisper punctuates the
  end of every segment it gets.
"""

from __future__ import annotations

_CLOSERS = "\"')]}»”’"


def ends_sentence(word: str) -> bool:
    w = word.rstrip(_CLOSERS)
    if not w or w.endswith("..") or w.endswith("…"):
        return False
    return w[-1] in ".!?"


def split_complete(text: str, cut: bool = False) -> tuple[str, str]:
    """``(complete sentences, incomplete tail)`` of ``text``; either may be
    empty. The two parts joined by a space give the normalized text back."""
    words = text.split()
    ends = [i + 1 for i, w in enumerate(words) if ends_sentence(w)]
    complete = ends[-1] if ends else 0
    if cut and complete == len(words):
        complete = ends[-2] if len(ends) > 1 else 0
    return " ".join(words[:complete]), " ".join(words[complete:])
