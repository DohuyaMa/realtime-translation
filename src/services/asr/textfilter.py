"""Whisper output cleanup: repetition loops and hallucinated segments."""

from __future__ import annotations

import re
import threading
from difflib import SequenceMatcher
from typing import List, Optional, Sequence

from loguru import logger

from .whisper_engine import SegmentStats


def normalize_word(w: str) -> str:
    """Strip leading/trailing punctuation for comparison."""
    return re.sub(r"^[^\w]+|[^\w]+$", "", w, flags=re.UNICODE).lower()


def repetition_keep(normed: Sequence[str]) -> List[int]:
    """Indices of the words kept after collapsing consecutive repeated words
    or short phrases (window up to 5 words); ``normed`` = normalized words."""
    keep: List[int] = []
    i = 0
    n = len(normed)
    while i < n:
        keep.append(i)
        moved = False
        for win in range(1, min(6, n - i + 1)):
            seq_n = normed[i : i + win]
            j = i + win
            count = 0
            while j + win <= n and normed[j : j + win] == seq_n:
                j += win
                count += 1
            if count > 0:
                if win > 1:
                    keep.extend(range(i + 1, i + win))
                i = j
                moved = True
                break
        if not moved:
            i += 1
    return keep


def filter_repetitions(text: str) -> str:
    """Collapse consecutive repeated words or short phrases (window up to 5 words).

    Examples:
        "hello hello hello"      -> "hello"
        "I think think think"    -> "I think"
        "я не знаю, я не знаю"  -> "я не знаю,"
        "Добре. Добре"           -> "Добре."
    """
    if not text:
        return text
    words = text.split()
    if len(words) < 2:
        return text
    return " ".join(
        words[i] for i in repetition_keep([normalize_word(w) for w in words])
    )


# --- prompt echo -------------------------------------------------------------
#
# With initial_prompt = the committed text, whisper sometimes "transcribes"
# the prompt again instead of the new audio. A hypothesis of >= ECHO_MIN_WORDS
# words of which >= ECHO_MIN_SHARE appear, in order, in the prompt, with one
# run of >= ECHO_MIN_WORDS consecutive words, is an echo (scattered common
# words are not).
ECHO_MIN_WORDS = 3
ECHO_MIN_SHARE = 0.8


def is_prompt_echo(words: Sequence[str], prompt: Optional[str]) -> bool:
    """``words`` (normalized hypothesis words) repeat the prompt."""
    if len(words) < ECHO_MIN_WORDS or not prompt:
        return False
    pw = [w for w in (normalize_word(t) for t in prompt.split()) if w]
    blocks = SequenceMatcher(
        None, list(words), pw, autojunk=False
    ).get_matching_blocks()
    longest = max((b.size for b in blocks), default=0)
    return longest >= ECHO_MIN_WORDS and sum(
        b.size for b in blocks
    ) >= ECHO_MIN_SHARE * len(words)


# --- hallucinated segments -------------------------------------------------
#
# Whisper invents text on silence, noise and sometimes on real speech: most
# often subtitle credits / video outros from its training data. A segment is
# dropped when its decoding confidence is low or its text is a known
# hallucination; every drop is logged (INFO) and counted in /status.

# faster-whisper's own thresholds and rule: a segment is silence only when BOTH
# no_speech and logprob are bad (either alone drops real fast/accented speech);
# a repetitive (highly compressible) segment is a decoding loop on its own.
NO_SPEECH_PROB_MAX = 0.6
AVG_LOGPROB_MIN = -1.0
COMPRESSION_RATIO_MAX = 2.4

# Whole-segment phrases (normalized: lowercase words joined by one space).
# Only video outros/credits - not phrases people really say live ("дякую за
# увагу" ends a talk).
KNOWN_HALLUCINATIONS = frozenset(
    {
        # uk
        "дякую за перегляд",
        "дякую що дивилися",
        "підписуйтесь на канал",
        "субтитри зроблені спільнотою amara org",
        # ru
        "спасибо за просмотр",
        "продолжение следует",
        "подписывайтесь на канал",
        "субтитры сделал dimatorzok",
        "субтитры создавал dimatorzok",
        # pl
        "dzięki za obejrzenie",
        "dziękuję za obejrzenie",
        "napisy stworzone przez społeczność amara org",
        # en
        "thanks for watching",
        "thank you for watching",
        "please subscribe",
        "subtitles by the amara org community",
    }
)
# Substrings that mark a subtitle credit anywhere in a segment (normalized).
HALLUCINATION_MARKERS = ("amara org", "dimatorzok", "редактор субтитров")

LOW_CONFIDENCE = "low_confidence"
HALLUCINATION = "hallucination"


def normalize_phrase(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower(), flags=re.UNICODE))


def drop_reason(text: str, stats: Optional[SegmentStats]) -> Optional[str]:
    """``HALLUCINATION`` / ``LOW_CONFIDENCE`` if the segment must go, else None."""
    if stats is not None and (
        (
            stats.no_speech_prob > NO_SPEECH_PROB_MAX
            and stats.avg_logprob < AVG_LOGPROB_MIN
        )
        or stats.compression_ratio > COMPRESSION_RATIO_MAX
    ):
        return LOW_CONFIDENCE
    phrase = normalize_phrase(text)
    if phrase in KNOWN_HALLUCINATIONS or any(
        m in phrase for m in HALLUCINATION_MARKERS
    ):
        return HALLUCINATION
    return None


class SegmentFilter:
    """Drops hallucinated segments; counts drops per reason for /status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = {HALLUCINATION: 0, LOW_CONFIDENCE: 0}

    def keep(self, text: str, stats: Optional[SegmentStats]) -> bool:
        reason = drop_reason(text, stats)
        if reason is None:
            return True
        with self._lock:
            self._counts[reason] += 1
        logger.info("dropped ASR segment ({}): {!r} {}", reason, text, stats)
        return False

    def counts(self) -> dict:
        with self._lock:
            return dict(self._counts)
