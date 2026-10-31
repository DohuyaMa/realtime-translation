"""Whisper + Vosk cross-check (TASK-SYS-12): the gate and word alignment.

The same segment audio is recognized by whisper (FLUENT: correct spelling,
may hallucinate a whole phrase) and Vosk (EXACT: never invents words). Two
pieces of logic live here, both pure:

- ``has_real_word``: the gate. Vosk heard no word of >= 2 letters -> the
  segment is silence/noise whatever whisper wrote; the pipeline drops it
  before the LLM (the LLM cannot reliably answer "nothing").
- ``Aligned``: both transcripts placed on the stream timeline, so the
  incomplete-sentence hold (``split_complete`` on whisper's punctuation) can
  cut the Vosk text at the same moment: Vosk words carry their own times,
  whisper words get times interpolated inside whisper's segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

GATE_MIN_LETTERS = 2


def has_real_word(text: str) -> bool:
    """A token with at least GATE_MIN_LETTERS letters (Vosk fillers like
    «е» or «і» alone are not speech)."""
    return any(
        sum(ch.isalpha() for ch in token) >= GATE_MIN_LETTERS for token in text.split()
    )


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
