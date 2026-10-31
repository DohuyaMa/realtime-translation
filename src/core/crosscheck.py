"""Whisper + Vosk cross-check (TASK-SYS-12): the gate and word alignment.

The same segment audio is recognized by whisper (correct spelling, may
hallucinate a whole phrase) and Vosk (never invents words, but on a small
model is sparse and garbled). Two pieces of logic live here, both pure:

- ``gate_reason``: the segment-level gate (code, never the LLM - it cannot
  reliably answer "nothing"). A segment is dropped before the LLM only when
  whisper clearly hallucinated it: Vosk heard no word of >= 2 letters
  (silence/noise), or not one whisper word of >= 3 letters agrees
  (fuzzy) with a Vosk word. Any agreement keeps the WHOLE whisper text:
  Vosk is sparse and garbled (live test 2026-10-31 §10), so it may veto a
  phrase but never delete words from it.
- ``Aligned``: both transcripts placed on the stream timeline, so the
  incomplete-sentence hold (``split_complete`` on whisper's punctuation) can
  cut the Vosk text at the same moment: Vosk words carry their own times,
  whisper words get times interpolated inside whisper's segments.
"""

from __future__ import annotations

import re
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
# gate_reason values
GATE_NO_WORD = "no_word"
GATE_DISAGREE = "disagree"

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


def agreement(whisper: str, vosk: str) -> Optional[float]:
    """Letter-weighted share of whisper words (>= AGREE_MIN_LETTERS) that
    fuzzily match a Vosk word or two adjacent Vosk words joined (Vosk splits
    words: «інформа ції»). None = whisper has no such word (no evidence)."""
    ww = [w for w in words(whisper) if len(w) >= AGREE_MIN_LETTERS]
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


def gate_reason(whisper: str, vosk: str) -> Optional[str]:
    """Why the segment is dropped before the LLM, or None (translated whole).

    Only clear hallucination is dropped: no real Vosk word (GATE_NO_WORD) or
    zero agreement (GATE_DISAGREE). A whisper text without long words
    (agreement None) is kept when Vosk heard a word."""
    if not has_real_word(vosk):
        return GATE_NO_WORD
    if agreement(whisper, vosk) == 0.0:
        return GATE_DISAGREE
    return None


def whisper_word_times(
    text: str, segments: Sequence[dict], duration_s: float, offset_s: float
) -> tuple[float, ...]:
    """Estimated stream time of every word of ``text`` (whisper output).

    Words are spread evenly inside the whisper segment that holds them.
    When the segments do not cover the text word for word (no segments, or
    whisper's repetition filter changed the joined text) the words are
    spread over the whole audio instead - same interpolation, one segment.
    """
    words = text.split()
    spans: list[tuple[float, float, int]] = []
    for seg in segments or ():
        try:
            n = len(str(seg.get("text") or "").split())
            spans.append((float(seg["start"]), float(seg["end"]), n))
        except (KeyError, TypeError, ValueError, AttributeError):
            spans = []
            break
    if sum(n for _, _, n in spans) != len(words):
        spans = [(0.0, max(duration_s, 0.0), len(words))]
    times: list[float] = []
    for start, end, n in spans:
        for i in range(n):
            times.append(offset_s + start + (end - start) * (i + 0.5) / n)
    return tuple(times)


@dataclass(frozen=True)
class Aligned:
    """Whisper word times + Vosk words with times, on the stream timeline.

    ``times[i]`` belongs to the i-th whisper word of the phrase text; the
    phrase text itself stays in ``_Phrase.text`` (split by whitespace).
    """

    times: tuple[float, ...]
    check: tuple[tuple[str, float], ...]  # (Vosk word, stream time)

    @classmethod
    def build(
        cls,
        text: str,
        segments: Sequence[dict],
        vosk_words: Sequence,
        vosk_text: str,
        duration_s: float,
        offset_s: float,
    ) -> "Aligned":
        times = whisper_word_times(text, segments, duration_s, offset_s)
        if vosk_words:
            check = tuple(
                (w.word, offset_s + (w.start + w.end) / 2.0) for w in vosk_words
            )
        else:  # a backend without word timings: spread the text evenly
            tokens = vosk_text.split()
            check = tuple(
                (t, offset_s + duration_s * (i + 0.5) / len(tokens))
                for i, t in enumerate(tokens)
            )
        return cls(times, check)

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
