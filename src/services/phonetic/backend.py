"""rt-phonetic backend interface, CTC greedy decoding and the fake backend.

The model emits one token per audio frame (wav2vec2: 20 ms): a letter, the
word delimiter ``|``, the CTC blank (pad) or a non-letter mark (the stress
mark ``+`` of the uk model). ``ctc_words`` collapses repeats, drops blanks
and marks and splits at the delimiter; each word keeps the time of its
first and last letter frame, so the edge can cut a unit's transcript at the
same moment as whisper's words (``core.crosscheck.Aligned``).
"""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from typing import List, Mapping, Optional, Protocol, Sequence, Tuple

import numpy as np

from ..common.model_store import Progress
from .settings import PhoneticSettings

__all__ = [
    "FakePhoneticBackend",
    "PhoneticBackend",
    "Transcript",
    "Word",
    "ctc_words",
    "make_backend",
]

WORD_DELIMITER = "|"
# letters kept inside a word besides alphabetic ones (Ukrainian apostrophe)
APOSTROPHES = "'’ʼ"


@dataclass(frozen=True)
class Word:
    word: str
    start: float  # seconds from the start of the audio
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    words: Tuple[Word, ...]


def clean_token(token: str) -> str:
    """The letters of one vocabulary token: alphabetic characters and the
    apostrophe; stress marks (``+``, combining U+0301), ``[UNK]`` and other
    marks are dropped."""
    if token.startswith("[") and token.endswith("]"):
        return ""
    return "".join(
        ch
        for ch in unicodedata.normalize("NFC", token)
        if (ch.isalpha() and not unicodedata.combining(ch)) or ch in APOSTROPHES
    )


def ctc_words(
    ids: Sequence[int],
    vocab: Mapping[int, str],
    blank: int,
    frame_s: float,
) -> List[Word]:
    """Greedy CTC: per-frame argmax ids -> words with times (seconds)."""
    words: List[Word] = []
    letters: List[str] = []
    first = last = 0
    prev: Optional[int] = None

    def close() -> None:
        text = "".join(letters).strip("".join(APOSTROPHES))
        if any(ch.isalpha() for ch in text):
            words.append(
                Word(text, round(first * frame_s, 3), round((last + 1) * frame_s, 3))
            )
        letters.clear()

    for i, tid in enumerate(ids):
        if tid == prev:
            if letters and tid != blank:
                last = i
            continue
        prev = tid
        if tid == blank:
            continue
        token = vocab.get(tid, "")
        if token == WORD_DELIMITER:
            close()
            continue
        piece = clean_token(token)
        if not piece:
            continue
        if not letters:
            first = i
        letters.append(piece)
        last = i
    close()
    return words


class PhoneticBackend(Protocol):
    name: str
    model_id: str
    language: str  # of the (single) model

    def fetch(self, progress: Optional[Progress] = None) -> None:
        """Provision the model files (may download; no timeout)."""
        ...

    def load(self) -> None:
        """Load the fetched model (bounded by the load timeout)."""
        ...

    def unload(self) -> None:
        """Free the model now."""
        ...

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        """float32 mono 16 kHz -> letter-level words with times."""
        ...


class FakePhoneticBackend:
    """Deterministic: one word ``звук<i>`` per started 0.5 s of audio that
    is not digital silence; silence -> empty transcript."""

    name = "fake"
    WORD_S = 0.5

    def __init__(
        self,
        language: str,
        model_id: str = "fake",
        load_delay: float = 0.0,
        fail_on_load: Optional[str] = None,
    ) -> None:
        self.language = language
        self.model_id = model_id
        self._load_delay = load_delay
        self._fail_on_load = fail_on_load
        self.calls: List[Tuple[int, str]] = []

    def fetch(self, progress: Optional[Progress] = None) -> None:
        pass  # nothing to provision

    def load(self) -> None:
        time.sleep(self._load_delay)
        if self._fail_on_load:
            raise RuntimeError(self._fail_on_load)

    def unload(self) -> None:
        pass  # holds no model

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        self.calls.append((len(audio), language))
        if not np.any(audio):
            return Transcript("", ())
        dur = len(audio) / 16000
        n = max(1, int(np.ceil(dur / self.WORD_S - 1e-9)))
        words = tuple(
            Word(
                f"звук{i}",
                round(i * self.WORD_S, 3),
                round(min(dur, (i + 1) * self.WORD_S), 3),
            )
            for i in range(n)
        )
        return Transcript(" ".join(w.word for w in words), words)


def make_backend(settings: PhoneticSettings) -> PhoneticBackend:
    if settings.backend == "fake":
        return FakePhoneticBackend(settings.language, settings.model)
    from .wav2vec2_backend import Wav2Vec2Backend

    return Wav2Vec2Backend(settings)
