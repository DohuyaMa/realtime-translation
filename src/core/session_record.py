"""«Записувати сесію для аналізу»: opt-in per-session recording.

Explicit toggle (``services.record_session``, env ``RT_EDGE_RECORD_SESSION``,
default off). While on, every translate-mode unit is written as
``seg-<seq>.wav`` (the exact 16 kHz audio sent to rt-phonetic, cut from the
rt-asr stream) plus one JSON line in ``phrases.jsonl``; every LLM pass adds
a ``translation`` line. One directory per pipeline start:
``$XDG_STATE_HOME/real-time-translator/sessions/<YYYYmmdd-HHMMSS>/``
(default ``~/.local/state/...``), so real audio can be replayed offline
(``scripts/replay_session.py``) against other models and prompts.

Records (``type``):
- ``segment``: seq, wav, start_s, end_s, reason, language, whisper,
  whisper_words [{word, start, end, conf}], phonetic (transcript or null),
  phonetic_words [{word, start, end}] (stream times), phonetic_ms,
  similarity, gate (``agree`` / ``foreign_terms`` / ``short_words`` pass,
  ``no_word`` / ``disagree`` dropped, ``no_phonetic`` = no transcript),
  mode (``sound`` | ``whisper-only``);
- ``translation``: seq, mode (``sound`` | ``whisper-only`` | ``retry``),
  gate, text, sound, next, translation, rejected (reason or null). A
  rejected sound-mode pass followed by its whisper-only retry gives two
  lines of one seq.

Recordings made before 2026-11-01 carry the removed Vosk fields (``vosk``,
``vosk_words``, ``agreement``, mode ``two-text``); the replay script reads
only whisper, the wav and the translation.

A write error stops the recording - logged as an error, never silently.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from loguru import logger

from ..clients.asr import encode_wav


def default_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "real-time-translator" / "sessions"


class SessionRecorder:
    def __init__(
        self,
        root: Optional[Path] = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._root = root or default_root()
        self._now = now
        self._lock = threading.Lock()
        self._file: Any = None
        self.path: Optional[Path] = None
        self.error: Optional[str] = None

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self) -> Optional[Path]:
        """Open a new session directory; None (and ``error``) on failure."""
        with self._lock:
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._now()))
            path = self._root / stamp
            n = 1
            while path.exists():  # two starts within one second
                n += 1
                path = self._root / f"{stamp}-{n}"
            try:
                path.mkdir(parents=True)
                self._file = open(path / "phrases.jsonl", "a", encoding="utf-8")
            except OSError as e:
                self.error = f"{type(e).__name__}: {e}"
                logger.error("session recording not started ({}): {}", path, e)
                return None
            self.path, self.error = path, None
        logger.info("session recording on: {}", path)
        return path

    def close(self) -> None:
        with self._lock:
            f, self._file = self._file, None
        if f is not None:
            f.close()
            logger.info("session recording off: {}", self.path)

    def segment(
        self, seq: int, audio: np.ndarray, sample_rate: int, record: dict
    ) -> None:
        name = f"seg-{seq:05d}.wav"

        def write() -> None:
            assert self.path is not None
            (self.path / name).write_bytes(encode_wav(audio, sample_rate))
            self._line({"type": "segment", "seq": seq, "wav": name, **record})

        self._guard(write)

    def translation(self, seq: int, record: dict) -> None:
        self._guard(lambda: self._line({"type": "translation", "seq": seq, **record}))

    def _line(self, record: dict) -> None:
        record = {"t": round(self._now(), 3), **record}
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def _guard(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                fn()
            except OSError as e:
                self.error = f"{type(e).__name__}: {e}"
                logger.error(
                    "session recording stopped - write failed in {}: {}", self.path, e
                )
                f, self._file = self._file, None
                try:
                    f.close()
                except OSError:
                    pass
