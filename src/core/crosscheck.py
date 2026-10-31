"""Whisper + Vosk cross-check (TASK-SYS-12): the gate and word alignment.

The same audio is streamed to whisper (correct spelling, may hallucinate a
whole phrase) and Vosk (never invents words, but on a small model is sparse
and garbled). Two pieces of logic live here, both pure:

- ``gate_reason``: the per-unit gate (code, never the LLM - it cannot
  reliably answer "nothing"). A unit is dropped before the LLM only when
  whisper clearly hallucinated it: Vosk heard no word of >= 2 letters
  (silence/noise), or not one whisper word of >= 3 letters agrees
  (fuzzy) with a Vosk word. Any agreement keeps the WHOLE whisper text:
  Vosk is sparse and garbled (live test 2026-10-31 §10), so it may veto a
  phrase but never delete words from it. Whisper words written in another
  script than the Vosk model's language (Latin «TypeScript, Docker» for a
  uk model, Cyrillic for a pl model) are not evidence either way: the
  model cannot spell them, so they never count against agreement (§12 #19
  was dropped as ``disagree``); a unit of only such words passes
  (``foreign_terms``).
- ``Aligned``: both transcripts of a unit on the stream timeline (both
  services send word times on one scale), so a unit split into several LLM
  requests (``words_per_request``) cuts the Vosk text at the same moment.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Sequence

GATE_MIN_LETTERS = 2
# whisper words shorter than this are not evidence (« в », «і», «за» match
# by chance)
AGREE_MIN_LETTERS = 3
# difflib ratio for "the same word, misrecognized" (Vosk «перемешують» ~
# whisper «переміщують»); 0.67 separated real speech (>= 0.26 agreement,
# Vosk nano/small on espeak audio) from hallucinations (0.0) in the
# 2026-10-31 test (context/live-test-2026-10-31.md §10)
AGREE_WORD_SIMILARITY = 0.67
# gate_reason values: the unit is dropped ...
GATE_NO_WORD = "no_word"
GATE_DISAGREE = "disagree"
GATE_DROPS = (GATE_NO_WORD, GATE_DISAGREE)
# ... or passes: some whisper word agrees with Vosk / every long whisper
# word is in a script the Vosk model cannot write / no whisper word of
# AGREE_MIN_LETTERS letters (no evidence, kept)
GATE_AGREE = "agree"
GATE_FOREIGN_TERMS = "foreign_terms"
GATE_SHORT_WORDS = "short_words"

# alphabet of a Vosk model per language; a language missing here has no
# script rule (every whisper word counts)
LANGUAGE_SCRIPTS = {"uk": "CYRILLIC", "ru": "CYRILLIC", "pl": "LATIN", "en": "LATIN"}

_WORD = re.compile(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)*")


def has_real_word(text: str) -> bool:
    """A token with at least GATE_MIN_LETTERS letters (Vosk fillers like
    «е» or «і» alone are not speech)."""
    return any(
        sum(ch.isalpha() for ch in token) >= GATE_MIN_LETTERS for token in text.split()
    )


def words(text: str) -> list[str]:
    """Lower-case letter words (apostrophes kept, punctuation/digits gone)."""
    return [w.replace("’", "'").replace("ʼ", "'") for w in _WORD.findall(text.lower())]


def script(word: str) -> Optional[str]:
    """Unicode script of a word's letters (CYRILLIC, LATIN, ...); None when
    it mixes scripts."""
    found = {unicodedata.name(ch, "?").split(" ")[0] for ch in word if ch.isalpha()}
    return found.pop() if len(found) == 1 else None


def long_words(whisper: str) -> list[str]:
    return [w for w in words(whisper) if len(w) >= AGREE_MIN_LETTERS]


def countable_words(whisper: str, language: Optional[str]) -> list[str]:
    """Long whisper words the Vosk model of ``language`` could have heard:
    words in another script than the model's are left out."""
    model = LANGUAGE_SCRIPTS.get(language or "")
    return [
        w for w in long_words(whisper) if model is None or script(w) in (None, model)
    ]


def agreement(whisper: str, vosk: str, language: Optional[str]) -> Optional[float]:
    """Letter-weighted share of countable whisper words (>= AGREE_MIN_LETTERS,
    in the Vosk model's script) that fuzzily match a Vosk word or two
    adjacent Vosk words joined (Vosk splits words: «інформа ції»). None =
    whisper has no such word (no evidence)."""
    ww = countable_words(whisper, language)
    if not ww:
        return None
    vv = words(vosk)
    cands = set(vv) | {a + b for a, b in zip(vv, vv[1:])}
    total = hit = 0
    for w in ww:
        total += len(w)
        if any(
            SequenceMatcher(None, w, c).ratio() >= AGREE_WORD_SIMILARITY for c in cands
        ):
            hit += len(w)
    return hit / total


def mean_conf(vosk_words: Sequence) -> Optional[float]:
    """Mean Vosk word confidence (None = no words / no confidences)."""
    confs = [float(w.conf) for w in vosk_words]
    return sum(confs) / len(confs) if confs else None


def gate_reason(whisper: str, vosk: str, language: Optional[str]) -> str:
    """The gate decision for one unit; dropped iff in GATE_DROPS.

    Only clear hallucination is dropped: no real Vosk word (GATE_NO_WORD) or
    zero agreement (GATE_DISAGREE). Passed: GATE_AGREE, GATE_FOREIGN_TERMS
    (only words the ``language`` Vosk model cannot write), GATE_SHORT_WORDS
    (no long whisper word; kept when Vosk heard a word)."""
    if not has_real_word(vosk):
        return GATE_NO_WORD
    share = agreement(whisper, vosk, language)
    if share == 0.0:
        return GATE_DISAGREE
    if share is not None:
        return GATE_AGREE
    return GATE_FOREIGN_TERMS if long_words(whisper) else GATE_SHORT_WORDS


@dataclass(frozen=True)
class Aligned:
    """Whisper word times + Vosk words with times, on the stream timeline.

    ``times[i]`` belongs to the i-th whisper word of the phrase text; the
    phrase text itself stays in ``_Phrase.text`` (split by whitespace).
    """

    times: tuple[float, ...]
    check: tuple[tuple[str, float], ...]  # (Vosk word, stream time)

    @classmethod
    def from_words(cls, words: Sequence, vosk_words: Sequence) -> "Aligned":
        """Whisper words of a unit and the Vosk words of its span, both with
        stream times (``.text``/``.start``/``.end``); a word sits at its
        middle."""
        return cls(
            tuple((w.start + w.end) / 2.0 for w in words),
            tuple((v.text, (v.start + v.end) / 2.0) for v in vosk_words),
        )

    @property
    def check_text(self) -> str:
        return " ".join(w for w, _ in self.check)

    def __add__(self, other: "Aligned") -> "Aligned":
        return Aligned(self.times + other.times, self.check + other.check)

    def cut(self, k: int) -> tuple["Aligned", "Aligned"]:
        """(first ``k`` whisper words, the rest); Vosk words go to the side
        of the cut time (midway between whisper words k-1 and k)."""
        if k <= 0:
            return Aligned((), ()), self
        if k >= len(self.times):
            return self, Aligned((), ())
        at = (self.times[k - 1] + self.times[k]) / 2.0
        head = tuple(c for c in self.check if c[1] < at)
        tail = tuple(c for c in self.check if c[1] >= at)
        return Aligned(self.times[:k], head), Aligned(self.times[k:], tail)
