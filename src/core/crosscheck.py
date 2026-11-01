"""Whisper + phonetic cross-check: the letter gate and word alignment.

Every translate-mode unit's audio (``stream.audio(t0, t1)``) is recognized
again by rt-phonetic, a character-level CTC model (wav2vec2, no dictionary,
no language model): it writes the letters it heard, so it never invents a
fluent phrase, and names/terms come out spelled by sound («сайборг»,
«віспер»). Whisper writes correct words but may hallucinate a whole phrase
or confidently put a wrong word in. Two pieces of logic live here, both pure:

- ``gate_reason``: the per-unit gate (code, never the LLM - it cannot
  reliably answer "nothing"). Both texts are reduced to their letters
  (lowercased; spaces, punctuation, digits, apostrophes gone) and compared
  with a difflib ratio. An empty transcript is heard silence: the whisper
  unit is a hallucination (``no_word``). A ratio below
  ``GATE_MIN_SIMILARITY`` is a different phrase (``disagree``). Whisper words
  in another script than the phonetic model's (Latin «TypeScript, Docker»
  for the uk model) are left out of whisper's letters - the model spells
  them by sound in its own alphabet, so they are not evidence; a unit of
  only such words passes (``foreign_terms``), and so does a unit with fewer
  than ``GATE_MIN_LETTERS`` countable whisper letters (``short_words``: «це»,
  «так» are too short for a ratio). Evidence (context/phonetic-bench-
  2026-11-01.md): on the owner's 39 recorded units the gate wrongly dropped
  0, it was empty on 5/5 silence/noise clips and dropped 66/66 swapped
  phrases (FLEURS).
- ``Aligned``: whisper word times + the phonetic words of the unit on the
  stream timeline, so a unit split into several LLM requests
  (``words_per_request``) cuts the transcript at the same moment.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, Sequence

# drop a unit when its letters are less similar than this (difflib ratio);
# owner decision 2026-11-01 (re-derive by scripts/replay_session.py)
GATE_MIN_SIMILARITY = 0.4
# fewer countable whisper letters than this: no ratio, the unit passes
GATE_MIN_LETTERS = 4
# gate_reason values: the unit is dropped ...
GATE_NO_WORD = "no_word"
GATE_DISAGREE = "disagree"
GATE_DROPS = (GATE_NO_WORD, GATE_DISAGREE)
# ... or passes: the letters agree / every whisper word is in a script the
# phonetic model cannot write / too few whisper letters to compare
GATE_AGREE = "agree"
GATE_FOREIGN_TERMS = "foreign_terms"
GATE_SHORT_WORDS = "short_words"

# alphabet of a phonetic model per language; a language missing here has no
# script rule (every whisper word counts)
LANGUAGE_SCRIPTS = {"uk": "CYRILLIC", "ru": "CYRILLIC", "pl": "LATIN", "en": "LATIN"}

_WORD = re.compile(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)*")


def words(text: str) -> list[str]:
    """Lower-case letter words (apostrophes kept, punctuation/digits gone)."""
    return [w.replace("’", "'").replace("ʼ", "'") for w in _WORD.findall(text.lower())]


def script(word: str) -> Optional[str]:
    """Unicode script of a word's letters (CYRILLIC, LATIN, ...); None when
    it mixes scripts."""
    found = {unicodedata.name(ch, "?").split(" ")[0] for ch in word if ch.isalpha()}
    return found.pop() if len(found) == 1 else None


def letters(text: str) -> str:
    """Only the letters of ``text``, lowercased (no spaces/punctuation)."""
    return "".join(ch for ch in text.lower() if ch.isalpha())


def countable_words(whisper: str, language: Optional[str]) -> list[str]:
    """Whisper words the phonetic model of ``language`` could have spelled:
    words in another script than the model's are left out."""
    model = LANGUAGE_SCRIPTS.get(language or "")
    return [w for w in words(whisper) if model is None or script(w) in (None, model)]


def similarity(whisper: str, sound: str, language: Optional[str]) -> Optional[float]:
    """difflib ratio of whisper's countable letters and the transcript's
    letters; None = whisper has fewer than GATE_MIN_LETTERS such letters
    (no evidence either way)."""
    w = letters(" ".join(countable_words(whisper, language)))
    if len(w) < GATE_MIN_LETTERS:
        return None
    return SequenceMatcher(None, w, letters(sound), autojunk=False).ratio()


def gate_reason(whisper: str, sound: str, language: Optional[str]) -> str:
    """The gate decision for one unit; dropped iff in GATE_DROPS.

    GATE_NO_WORD: the transcript has no letter (heard silence/noise).
    GATE_DISAGREE: similarity < GATE_MIN_SIMILARITY. Passed: GATE_AGREE,
    GATE_FOREIGN_TERMS (only words the ``language`` model cannot write),
    GATE_SHORT_WORDS (too few whisper letters to compare)."""
    if not letters(sound):
        return GATE_NO_WORD
    share = similarity(whisper, sound, language)
    if share is not None:
        return GATE_AGREE if share >= GATE_MIN_SIMILARITY else GATE_DISAGREE
    if words(whisper) and not countable_words(whisper, language):
        return GATE_FOREIGN_TERMS
    return GATE_SHORT_WORDS


@dataclass(frozen=True)
class Aligned:
    """Whisper word times + phonetic words with times, on the stream timeline.

    ``times[i]`` belongs to the i-th whisper word of the unit text; the
    text itself stays in ``_Unit.text`` (split by whitespace).
    """

    times: tuple[float, ...]
    sound: tuple[tuple[str, float], ...]  # (phonetic word, stream time)

    @classmethod
    def from_words(cls, words: Sequence, sound_words: Sequence) -> "Aligned":
        """Whisper words of a unit and its phonetic words, both with stream
        times (``.text``/``.start``/``.end``); a word sits at its middle."""
        return cls(
            tuple((w.start + w.end) / 2.0 for w in words),
            tuple((v.text, (v.start + v.end) / 2.0) for v in sound_words),
        )

    @property
    def sound_text(self) -> str:
        return " ".join(w for w, _ in self.sound)

    def __add__(self, other: "Aligned") -> "Aligned":
        return Aligned(self.times + other.times, self.sound + other.sound)

    def cut(self, k: int) -> tuple["Aligned", "Aligned"]:
        """(first ``k`` whisper words, the rest); phonetic words go to the
        side of the cut time (midway between whisper words k-1 and k)."""
        if k <= 0:
            return Aligned((), ()), self
        if k >= len(self.times):
            return self, Aligned((), ())
        at = (self.times[k - 1] + self.times[k]) / 2.0
        head = tuple(c for c in self.sound if c[1] < at)
        tail = tuple(c for c in self.sound if c[1] >= at)
        return Aligned(self.times[:k], head), Aligned(self.times[k:], tail)
