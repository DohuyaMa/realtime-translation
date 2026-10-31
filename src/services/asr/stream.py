"""Whisper streaming session: LocalAgreement-2 (spec 2026-10-31 §1).

Pure class (no asyncio): ``feed`` runs on the event loop (VAD only), ``step``
/ ``finish`` / ``warm_up`` on the inference thread (``common/stream.py``
drives it). Every tick re-decodes the growing speech buffer (greedy, word
timestamps, ``initial_prompt`` = committed text before the buffer, <= 200
chars) and commits the longest common prefix of the last two hypotheses
(normalized words; text and times from the newer one). A VAD endpoint
(``STREAMING_VAD``: >= 500 ms silence) commits the whole hypothesis up to the
end of speech (``eos``) and sends ``endpoint``.

The buffer is trimmed at the end of the last committed sentence; above
``TRIM_SECONDS`` at the end of the last fully committed whisper segment;
above ``MAX_BUFFER_SECONDS`` (whisper sees <= 30 s) the hypothesis is
committed as is (``forced_commits``, logged).

Per hypothesis: segments ``drop_reason`` would drop (low confidence /
known hallucination) are left out, repeated words collapsed
(``repetition_keep``), and an echo of the prompt (``is_prompt_echo``) is
dropped. All times are ``t0`` + samples / 16 kHz.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence

import numpy as np
from loguru import logger

from ..common.audio import pcm16_to_float
from ..common.stream import SAMPLE_RATE
from .backend import AsrBackend
from .textfilter import (
    drop_reason,
    is_prompt_echo,
    normalize_word,
    repetition_keep,
)
from .vad import WINDOW, Vad
from .whisper_engine import STREAMING_VAD, VadProfile

PROMPT_CHARS = 200
TRIM_SECONDS = 15.0
MAX_BUFFER_SECONDS = 28.0
DETECT_SECONDS = 1.0  # auto language: detected once this much speech is buffered
OVERLAP_NGRAM = 5  # committed tail words a new hypothesis may repeat
SENTENCE_END = (".", "?", "!", "…")


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # absolute seconds (t0-based)
    end: float
    conf: float

    @property
    def norm(self) -> str:
        return normalize_word(self.text)

    def as_dict(self) -> dict:
        return {
            "word": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "conf": round(self.conf, 4),
        }


def _pct(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


class AsrStreamSession:
    def __init__(
        self,
        backend: AsrBackend,
        *,
        language: Optional[str],
        languages: Sequence[str],
        prompt: Optional[str] = None,
        t0: float = 0.0,
        vad: Optional[Vad] = None,
        profile: VadProfile = STREAMING_VAD,
    ) -> None:
        self._backend = backend
        self.language = language
        self._languages = tuple(languages)
        self._client_prompt = prompt or ""
        self._t0 = t0
        self._vad = vad if vad is not None else backend.new_vad()
        self._speech_thr = profile.threshold
        self._silence_thr = (
            profile.neg_threshold
            if profile.neg_threshold is not None
            else profile.threshold
        )
        self._min_speech = int(profile.min_speech_duration_ms * SAMPLE_RATE / 1000)
        self._endpoint_silence = int(
            profile.min_silence_duration_ms * SAMPLE_RATE / 1000
        )
        self._preroll = int(profile.speech_pad_ms * SAMPLE_RATE / 1000)

        # --- audio + VAD (event loop writes, inference thread reads) ------
        self._lock = threading.Lock()
        self._odd = b""  # a split PCM16 sample
        self._received = 0  # samples fed
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start = 0  # absolute sample of _buf[0]
        self._vad_tail = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._utt_speech = 0
        self._silence = 0
        self._speech_end = 0  # absolute sample after the last speech window
        self._speech_in_buf = False
        self._endpoints: Deque[int] = deque()  # speech-end samples to commit
        self._stepped = 0  # _received at the last step

        # --- hypotheses (inference thread only) ---------------------------
        self._committed: List[Word] = []
        self._committed_end = t0
        self._prev: List[Word] = []
        self._partial = ""
        self._seq = 0
        # stats
        self._lags: List[float] = []
        self._dropped: Dict[str, int] = {}
        self._echo = 0
        self._forced = 0

    # --- event loop ---------------------------------------------------------

    def _t(self, sample: int) -> float:
        return self._t0 + sample / SAMPLE_RATE

    def _sample(self, t: float) -> int:
        return int(round((t - self._t0) * SAMPLE_RATE))

    @property
    def audio_end(self) -> float:
        return self._t(self._received)

    def feed(self, pcm: bytes) -> bool:
        data = self._odd + pcm
        usable = len(data) - len(data) % 2
        self._odd = data[usable:]
        audio = pcm16_to_float(data[:usable])
        with self._lock:
            pos = self._received - len(self._vad_tail)  # sample of _vad_tail[0]
            self._received += len(audio)
            self._buf = np.concatenate([self._buf, audio])
            tail = np.concatenate([self._vad_tail, audio])
            n = len(tail) // WINDOW
            self._vad_tail = tail[n * WINDOW :]
            endpoint = False
            if n:
                probs = self._vad.probs(tail[: n * WINDOW].reshape(n, WINDOW))
                for k, prob in enumerate(probs):
                    endpoint |= self._vad_window(float(prob), pos + (k + 1) * WINDOW)
            if not self._speech_in_buf:  # idle: keep only the pre-roll
                self._trim_to(self._received - len(self._vad_tail) - self._preroll)
            return endpoint

    def _vad_window(self, prob: float, end: int) -> bool:
        """One 32 ms window ending at sample ``end``; True on an endpoint."""
        speech = prob >= (self._silence_thr if self._in_speech else self._speech_thr)
        if speech:
            if not self._in_speech:
                self._in_speech, self._utt_speech = True, 0
            self._utt_speech += WINDOW
            self._silence = 0
            self._speech_end = end
            if self._utt_speech >= self._min_speech:
                self._speech_in_buf = True
            return False
        if not self._in_speech:
            return False
        self._silence += WINDOW
        if self._silence < self._endpoint_silence:
            return False
        self._in_speech = False
        if self._utt_speech < self._min_speech:
            return False  # a click, not speech
        self._endpoints.append(self._speech_end)
        return True

    def _trim_to(self, sample: int) -> None:
        """Caller holds the lock. Drop buffered audio before ``sample``."""
        if sample > self._buf_start:
            cut = min(sample - self._buf_start, len(self._buf))
            self._buf = self._buf[cut:]
            self._buf_start += cut

    def pending(self) -> bool:
        with self._lock:
            return self._speech_in_buf and (
                self._received > self._stepped or bool(self._endpoints)
            )

    # --- inference thread ---------------------------------------------------

    def warm_up(self) -> None:
        lang = self.language or self._languages[0]
        self._backend.stream_decode(np.zeros(SAMPLE_RATE, np.float32), lang, None)

    def step(self) -> List[dict]:
        with self._lock:
            if not self._speech_in_buf:
                return []
            endpoint = self._endpoints.popleft() if self._endpoints else None
            start = self._buf_start
            stop = endpoint if endpoint is not None else self._received
            audio = self._buf[: max(0, stop - start)].copy()
            self._stepped = self._received
        if endpoint is None and len(audio) < DETECT_SECONDS * SAMPLE_RATE:
            if self.language is None:
                return []  # too little speech to tell the language yet
        out: List[dict] = []
        hyp, seg_ends = self._hypothesis(audio, start)
        if endpoint is not None:
            out += self._commit(hyp, eos=True, now=self._t(stop))
            out.append({"type": "endpoint", "at": round(self._t(endpoint), 3)})
            self._prev, self._partial = [], ""
            with self._lock:
                self._trim_to(endpoint)
                self._speech_in_buf = self._in_speech or bool(self._endpoints)
            return out
        agreed = 0
        while (
            agreed < min(len(hyp), len(self._prev))
            and hyp[agreed].norm == self._prev[agreed].norm
        ):
            agreed += 1
        out += self._commit(hyp[:agreed], eos=False, now=self._t(stop))
        self._prev = hyp[agreed:]
        if (stop - start) / SAMPLE_RATE > MAX_BUFFER_SECONDS and self._prev:
            self._forced += 1
            logger.warning(
                "asr stream: {:.1f}s without agreement; committing the hypothesis",
                (stop - start) / SAMPLE_RATE,
            )
            out += self._commit(self._prev, eos=False, now=self._t(stop))
            self._prev = []
            with self._lock:
                self._trim_to(self._sample(self._committed_end))
        else:
            self._trim(start, stop, seg_ends)
        out += self._partial_msg()
        return out

    def finish(self) -> List[dict]:
        """``stop``: everything buffered becomes final (eos)."""
        with self._lock:
            has_speech = self._speech_in_buf
            start, stop = self._buf_start, self._received
            audio = self._buf.copy()
            self._endpoints.clear()
            self._speech_in_buf = False
            self._trim_to(stop)
        if not has_speech:
            return []
        hyp, _ = self._hypothesis(audio, start)
        self._prev, self._partial = [], ""
        return self._commit(hyp, eos=True, now=self._t(stop))

    # --- hypothesis ---------------------------------------------------------

    def _prompt(self, buf_start: float) -> Optional[str]:
        before = " ".join(w.text for w in self._committed if w.end <= buf_start + 1e-3)
        text = f"{self._client_prompt} {before}".strip()
        if len(text) > PROMPT_CHARS:
            text = text[-PROMPT_CHARS:]
            text = text.split(" ", 1)[1] if " " in text else text
        return text or None

    def _hypothesis(
        self, audio: np.ndarray, start: int
    ) -> tuple[List[Word], List[float]]:
        """Filtered words of one decode after the committed ones, and the
        ends of the kept whisper segments (absolute seconds)."""
        if len(audio) == 0:
            return [], []
        if self.language is None:
            self.language = self._backend.detect_language(audio, self._languages)
            logger.info(
                "asr stream: language {} (fixed for the session)", self.language
            )
        offset = self._t(start)
        prompt = self._prompt(offset)
        words: List[Word] = []
        seg_ends: List[float] = []
        for seg in self._backend.stream_decode(audio, self.language, prompt):
            reason = drop_reason(seg.text, seg.stats)
            if reason is not None:
                self._dropped[reason] = self._dropped.get(reason, 0) + 1
                logger.debug("asr stream: dropped segment ({}): {!r}", reason, seg.text)
                continue
            seg_ends.append(offset + seg.end)
            words += [
                Word(w.word, offset + w.start, offset + w.end, w.prob)
                for w in seg.words
                if normalize_word(w.word)
            ]
        fresh = self._fresh(words)
        fresh = [fresh[i] for i in repetition_keep([w.norm for w in fresh])]
        if is_prompt_echo([w.norm for w in fresh], prompt):
            self._echo += 1
            logger.info(
                "asr stream: prompt echo dropped: {!r}", " ".join(w.text for w in fresh)
            )
            return [], seg_ends
        return fresh, seg_ends

    def _fresh(self, words: List[Word]) -> List[Word]:
        """Words after the committed ones: by time, then by an n-gram the
        new hypothesis repeats from the committed tail."""
        fresh = [w for w in words if w.start > self._committed_end - 0.1]
        if (
            fresh
            and self._committed
            and abs(fresh[0].start - self._committed_end) < 1.0
        ):
            tail = [w.norm for w in self._committed[-OVERLAP_NGRAM:]]
            for n in range(min(len(tail), len(fresh), OVERLAP_NGRAM), 0, -1):
                if tail[-n:] == [w.norm for w in fresh[:n]]:
                    return fresh[n:]
        return fresh

    # --- commit / trim -------------------------------------------------------

    def _commit(self, words: List[Word], *, eos: bool, now: float) -> List[dict]:
        if not words:
            return []
        self._seq += 1
        self._committed += words
        del self._committed[:-200]  # the prompt needs <= 200 chars
        self._committed_end = words[-1].end
        self._lags += [now - w.end for w in words]
        return [
            {
                "type": "final",
                "seq": self._seq,
                "text": " ".join(w.text for w in words),
                "words": [w.as_dict() for w in words],
                "start": round(words[0].start, 3),
                "end": round(words[-1].end, 3),
                "eos": eos,
                "language": self.language,
            }
        ]

    def _partial_msg(self) -> List[dict]:
        text = " ".join(w.text for w in self._prev)
        if text == self._partial:
            return []
        self._partial = text
        start = self._prev[0].start if self._prev else self._committed_end
        return [{"type": "partial", "text": text, "start": round(start, 3)}]

    def _trim(self, start: int, stop: int, seg_ends: List[float]) -> None:
        """Cut the buffer at the last committed sentence end; above
        TRIM_SECONDS at the end of the last fully committed segment."""
        buf_t = self._t(start)
        cut: Optional[float] = None
        for w in reversed(self._committed):
            if w.end <= buf_t:
                break
            if w.text.endswith(SENTENCE_END):
                cut = w.end
                break
        if cut is None and (stop - start) / SAMPLE_RATE > TRIM_SECONDS:
            done = [e for e in seg_ends if e <= self._committed_end + 1e-3]
            cut = done[-1] if done else None
        if cut is not None:
            with self._lock:
                self._trim_to(self._sample(cut))

    # --- stats ----------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "language": self.language,
            "finals": self._seq,
            "committed_words": len(self._lags),
            "commit_lag_p50": _pct(self._lags, 0.5),
            "commit_lag_p90": _pct(self._lags, 0.9),
            "buffer_s": round(len(self._buf) / SAMPLE_RATE, 2),
            "dropped_segments": dict(self._dropped),
            "prompt_echo": self._echo,
            "forced_commits": self._forced,
        }
