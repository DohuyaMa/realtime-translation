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
* A full stop at the end of a segment cut without a pause (``soft`` /
  ``hard``) is trusted like any other: holding it cost +9 s per cut (§11)
  and in the live logs (§11/§12) whisper's period at a cut was a real
  sentence end in 10 of 11 cuts; the one wrong period («натисніть. |
  Зберегти.») came out the same with the hold («press. Save.»).
"""

from __future__ import annotations

_CLOSERS = "\"')]}»”’"


def ends_sentence(word: str) -> bool:
    w = word.rstrip(_CLOSERS)
    if not w or w.endswith("..") or w.endswith("…"):
        return False
    return w[-1] in ".!?"


def split_complete(text: str) -> tuple[str, str]:
    """``(complete sentences, incomplete tail)`` of ``text``; either may be
    empty. The two parts joined by a space give the normalized text back."""
    words = text.split()
    ends = [i + 1 for i, w in enumerate(words) if ends_sentence(w)]
    complete = ends[-1] if ends else 0
    return " ".join(words[:complete]), " ".join(words[complete:])
