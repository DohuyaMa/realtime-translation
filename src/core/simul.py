"""Simultaneous translation units (spec 2026-10-31 §4, owner decision 1).

rt-asr streams whisper's committed words (LocalAgreement-2 finals: they are
only ever appended, never revised). ``SourceLedger`` keeps them in order with
a cursor - everything before it is already cut into units - plus whisper's
current uncommitted hypothesis (``partial``, shown grey in the UI, never
translated). ``next_unit`` cuts the next unit after the cursor at the first
of, scanning word by word:

* a sentence end (``ends_sentence``: ``.``/``!``/``?``, not an ellipsis)
  that is final (``_sentence_cut``, live 2026-11-01 §14: whisper puts a
  period at a short pause inside a sentence - «оновлюється кожній екрані.»
  + «ноль ,5 секунды.»): a lowercase word right after it with no audio gap
  (<= ``CONTINUE_GAP_S``) continues the sentence; a one-word piece shorter
  than ``TINY_S`` («так.», 0.1 s) is never a unit of its own - it joins the
  previous piece or, at the head of a unit, the next one; a sentence end
  that is the last committed word waits <= ``SENTENCE_HOLD_S`` for the next
  word while whisper's partial goes on in lowercase (no wait after an
  endpoint);
* >= ``CLAUSE_WORDS`` untranslated words ending at a clause mark (``,;:``);
* ``HARD_WORDS`` untranslated words;
* the end of the committed words when an endpoint (VAD silence; ``final``
  with ``eos``) closed them.

Units never overlap, so nothing is translated or spoken twice (owner
decisions 2/3). In translate mode each unit's audio is then recognized by
rt-phonetic (``core.crosscheck``, translation_system ``_gate``).

Also used by transcribe mode: each unit is one recognized line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

CLAUSE_WORDS = 12
HARD_WORDS = 20
CLAUSE_MARKS = ",;:"
# sentence-end finality (``SourceLedger._sentence_cut``)
CONTINUE_GAP_S = 0.05
TINY_S = 0.25
SENTENCE_HOLD_S = 0.5
_CLOSERS = "\"')]}»”’"

# Unit.reason values
SENTENCE = "sentence"
CLAUSE = "clause"
HARD = "hard"
ENDPOINT = "endpoint"
STOP = "stop"


def ends_sentence(word: str) -> bool:
    """``.``/``!``/``?`` at the end (closing quotes/brackets ignored); an
    ellipsis (``..`` / ``…``) is speech trailing off, not an end."""
    w = word.rstrip(_CLOSERS)
    if not w or w.endswith("..") or w.endswith("…"):
        return False
    return w[-1] in ".!?"


def starts_lower(word: str) -> bool:
    """The word goes on a sentence: its first letter is lowercase or it
    starts with a digit («0 ,5»)."""
    for ch in word:
        if ch.isalpha():
            return ch.islower()
        if ch.isdigit():
            return True
    return False


def ends_clause(word: str) -> bool:
    w = word.rstrip(_CLOSERS)
    return bool(w) and w[-1] in CLAUSE_MARKS


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # stream seconds
    end: float
    conf: float = 1.0

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0

    @classmethod
    def parse(cls, d: dict) -> "Word":
        return cls(
            str(d.get("word") or ""),
            float(d.get("start") or 0.0),
            float(d.get("end") or 0.0),
            float(d.get("conf") if d.get("conf") is not None else 1.0),
        )


def parse_words(items: Optional[Iterable[dict]]) -> List[Word]:
    return [Word.parse(d) for d in items or () if isinstance(d, dict)]


@dataclass(frozen=True)
class Unit:
    """Committed words [first, last] of the ledger, cut as one LLM request."""

    words: tuple[Word, ...]
    reason: str
    end_index: int  # ledger index after the unit's last word

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def t0(self) -> float:
        return self.words[0].start

    @property
    def t1(self) -> float:
        return self.words[-1].end


class SourceLedger:
    """Whisper's committed words (append-only) + the unit cursor."""

    def __init__(self) -> None:
        self.words: List[Word] = []
        self.committed_at: List[Optional[float]] = []  # clock of each commit
        self.cursor = 0  # words before it are in units
        self.closed = 0  # words before it are closed by an endpoint
        self.partial = ""  # uncommitted hypothesis after the last word

    def commit(
        self, words: Sequence[Word], eos: bool = False, now: Optional[float] = None
    ) -> None:
        new = [w for w in words if w.text.strip()]
        self.words.extend(new)
        self.committed_at.extend(now for _ in new)
        if eos:
            self.endpoint()

    def endpoint(self) -> None:
        """Silence: the committed words so far form a complete utterance."""
        self.closed = len(self.words)
        self.partial = ""

    def _cut(self, end: int, reason: str) -> Unit:
        unit = Unit(tuple(self.words[self.cursor : end]), reason, end)
        self.cursor = end
        return unit

    def _tiny(self, i: int) -> bool:
        """Word i is a whole sentence piece of one word shorter than TINY_S."""
        w = self.words[i]
        alone = i == self.cursor or ends_sentence(self.words[i - 1].text)
        return alone and ends_sentence(w.text) and w.end - w.start < TINY_S

    def _sentence_cut(self, i: int, now: Optional[float]) -> Optional[bool]:
        """Sentence end at word i: True = cut after it, False = the sentence
        goes on (merge), None = wait (``now`` = clock; None = never wait)."""
        if i + 1 < len(self.words):
            nxt, w = self.words[i + 1], self.words[i]
            if i == self.cursor and self._tiny(i):
                return False  # a head fragment joins the next piece
            if self._tiny(i + 1):
                return False  # a trailing fragment joins this piece
            if starts_lower(nxt.text) and nxt.start - w.end <= CONTINUE_GAP_S:
                return False
            return True
        if self.closed > i or now is None:
            return True
        at = self.committed_at[i]
        if (
            at is not None
            and now - at < SENTENCE_HOLD_S
            and (i == self.cursor or starts_lower(self.partial))
        ):
            return None
        return True

    def next_unit(self, now: Optional[float] = None) -> Optional[Unit]:
        """The next unit after the cursor, or None (wait for more words)."""
        for i in range(self.cursor, len(self.words)):
            n = i - self.cursor + 1
            text = self.words[i].text
            if ends_sentence(text):
                cut = True if n >= HARD_WORDS else self._sentence_cut(i, now)
                if cut is None:
                    return None
                if cut:
                    return self._cut(i + 1, SENTENCE)
            if n >= CLAUSE_WORDS and ends_clause(text):
                return self._cut(i + 1, CLAUSE)
            if n >= HARD_WORDS:
                return self._cut(i + 1, HARD)
        if self.closed > self.cursor:
            return self._cut(self.closed, ENDPOINT)
        return None

    def units(self, now: Optional[float] = None) -> List[Unit]:
        out = []
        while True:
            unit = self.next_unit(now)
            if unit is None:
                return out
            out.append(unit)

    def flush(self) -> Optional[Unit]:
        """Stop: everything left after the cursor as one unit."""
        if self.cursor >= len(self.words):
            return None
        return self._cut(len(self.words), STOP)

    def after(self, index: int) -> str:
        """Committed words from ``index`` on plus the partial (NEXT)."""
        parts = [w.text for w in self.words[index:]]
        if self.partial:
            parts.append(self.partial)
        return " ".join(parts)

    def tail(self) -> str:
        """Not yet in a unit: committed words after the cursor + partial
        (the grey text of the UI)."""
        return self.after(self.cursor)


def words_text(words: Sequence[Any]) -> str:
    return " ".join(w.text for w in words)
