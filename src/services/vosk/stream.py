"""Vosk streaming session (spec 2026-10-31 §2): one KaldiRecognizer per WS
session. Kaldi's own endpointing ends an utterance: ``final`` (words with
conf/start/end, ``eos``) + ``endpoint``; between finals ``partial`` (the
current utterance, with partial words) at most every ``PARTIAL_INTERVAL_S``
of audio and only when it changed; with no word in the current utterance,
``idle{at, silent_from}`` at the same pace instead (``at`` = processed audio
time): Kaldi has no word AND the audio in ``[silent_from, at]`` had no voice
energy (``VoiceTracker``), so the edge can tell a whisper hallucination in a
pause (no Vosk word, silence past it) from Vosk being late (live 2026-11-01
§14). An empty partial alone is no proof of silence: the small uk model
decodes speech it cannot match as silence («повільніше» after «буде
замість» - Kaldi endpointed, then idle, and the real word was dropped as
``no_word``, live §16); ``silent_from`` starts after the last voiced
audio, so such a span is never claimed. Times are ``t0`` + samples / 16 kHz,
the same scale as rt-asr, so the edge can match words of both.

Pure class (no asyncio): ``feed`` only buffers; ``step``/``finish`` run the
recognizer on the inference thread (``common/stream.py`` drives it).
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from ..common.stream import SAMPLE_RATE
from .backend import VoskBackend

PARTIAL_INTERVAL_S = 0.3
TICK_S = 0.1  # vosk steps every 100 ms: the recognizer is cheap, finals timely
FEED_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms per AcceptWaveform
# voice energy of a 100 ms chunk: above VOICE_GATE_DBFS and VOICE_MARGIN_DB
# above the noise floor = the quietest chunk of the last NOISE_WINDOW_S
# (minimum statistics: speech has quieter gaps between words, steady noise
# sets the floor to itself; the edge normalizes speech to ~-20 dBFS and adds
# <= 24 dB of gain, so amplified room noise sits >= 10 dB below speech)
VOICE_GATE_DBFS = -50.0
VOICE_MARGIN_DB = 10.0
NOISE_WINDOW_S = 5.0
_FLOOR_DB = -100.0


def chunk_dbfs(chunk: bytes) -> float:
    """RMS of 16-bit PCM in dBFS."""
    x = np.frombuffer(chunk, dtype="<i2").astype(np.float64) / 32768.0
    r = float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
    return max(_FLOOR_DB, 20.0 * math.log10(r)) if r > 0 else _FLOOR_DB


class VoiceTracker:
    """Energy voice detector over the session's chunks: ``voiced_to`` = end
    of the latest chunk with voice energy (the edge must not take audio
    before it as heard silence, whatever Kaldi decoded). Until the first
    NOISE_WINDOW_S of audio has passed, the floor is at most
    VOICE_GATE_DBFS - VOICE_MARGIN_DB: speech from the first chunk on counts
    as voice (steady louder noise counts too, until the window learns it -
    a missed silence only lets a unit pass)."""

    def __init__(self, t0: float) -> None:
        self.voiced_to = t0
        self._levels: Deque[Tuple[float, float]] = deque(
            [(t0, VOICE_GATE_DBFS - VOICE_MARGIN_DB)]
        )  # (chunk end, dBFS)

    def add(self, chunk: bytes, end: float) -> bool:
        level = chunk_dbfs(chunk)
        self._levels.append((end, level))
        while self._levels[0][0] < end - NOISE_WINDOW_S:
            self._levels.popleft()
        floor = min(lv for _, lv in self._levels)
        voiced = level > max(VOICE_GATE_DBFS, floor + VOICE_MARGIN_DB)
        if voiced:
            self.voiced_to = end
        return voiced


class VoskStreamSession:
    def __init__(self, backend: VoskBackend, *, language: str, t0: float = 0.0) -> None:
        self._backend = backend
        self.language = language
        self._t0 = t0
        self._rec = backend.recognizer()
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._received = 0  # bytes fed
        self._processed = 0  # bytes given to the recognizer
        self._seq = 0
        self._partial = ""
        self._partial_at: Optional[float] = None
        self._last_end = t0
        self._partials = 0
        self._voice = VoiceTracker(t0)

    @property
    def audio_end(self) -> float:
        return self._t0 + (self._received // 2) / SAMPLE_RATE

    def _t(self, nbytes: int) -> float:
        return self._t0 + (nbytes // 2) / SAMPLE_RATE

    def feed(self, pcm: bytes) -> bool:
        with self._lock:
            self._pending += pcm
            self._received += len(pcm)
        return False

    def pending(self) -> bool:
        with self._lock:
            return len(self._pending) >= 2

    def _take(self) -> bytes:
        with self._lock:
            usable = len(self._pending) - len(self._pending) % 2
            data = bytes(self._pending[:usable])
            del self._pending[:usable]
        return data

    # --- inference thread ---------------------------------------------------

    def warm_up(self) -> None:
        rec = self._backend.recognizer()
        rec.AcceptWaveform(bytes(SAMPLE_RATE * 2))
        rec.FinalResult()

    def step(self) -> List[dict]:
        data = self._take()
        out: List[dict] = []
        for i in range(0, len(data), FEED_BYTES):
            chunk = data[i : i + FEED_BYTES]
            ended = self._rec.AcceptWaveform(chunk)
            self._processed += len(chunk)
            self._voice.add(chunk, self._t(self._processed))
            if ended:
                out += self._final(json.loads(self._rec.Result()), endpoint=True)
        out += self._partial_msg()
        return out

    def finish(self) -> List[dict]:
        out = self.step()
        out = [m for m in out if m["type"] not in ("partial", "idle")]
        return out + self._final(json.loads(self._rec.FinalResult()), endpoint=False)

    # --- messages -------------------------------------------------------------

    def _words(self, items: Any) -> List[dict]:
        return [
            {
                "word": str(w["word"]),
                "start": round(self._t0 + float(w["start"]), 3),
                "end": round(self._t0 + float(w["end"]), 3),
                "conf": round(float(w["conf"]), 4),
            }
            for w in items or ()
        ]

    def _final(self, result: Dict[str, Any], *, endpoint: bool) -> List[dict]:
        self._partial, self._partial_at = "", None
        words = self._words(result.get("result"))
        text = (result.get("text") or "").strip()
        if not text or not words:
            return []  # Kaldi ended an utterance of silence/noise
        self._seq += 1
        self._last_end = words[-1]["end"]
        out = [
            {
                "type": "final",
                "seq": self._seq,
                "text": text,
                "words": words,
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "eos": True,
                "language": self.language,
            }
        ]
        if endpoint:
            out.append({"type": "endpoint", "at": words[-1]["end"]})
        return out

    def _partial_msg(self) -> List[dict]:
        now = self._t(self._processed)
        if (
            self._partial_at is not None
            and now - self._partial_at < PARTIAL_INTERVAL_S - 1e-9
        ):
            return []
        result = json.loads(self._rec.PartialResult())
        text = (result.get("partial") or "").strip()
        if text == self._partial:
            if text:
                return []
            self._partial_at = now
            silent_from = max(self._voice.voiced_to, self._last_end)
            return [
                {
                    "type": "idle",
                    "at": round(now, 3),
                    "silent_from": round(min(silent_from, now), 3),
                }
            ]
        words = self._words(result.get("partial_result"))
        self._partial, self._partial_at = text, now
        self._partials += 1
        start = words[0]["start"] if words else self._last_end
        return [{"type": "partial", "text": text, "words": words, "start": start}]

    def stats(self) -> dict:
        return {
            "language": self.language,
            "finals": self._seq,
            "partials": self._partials,
            "buffered_s": round(len(self._pending) / 2 / SAMPLE_RATE, 2),
        }
