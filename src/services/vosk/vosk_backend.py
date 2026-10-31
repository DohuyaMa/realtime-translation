"""Vosk (Kaldi) backend: one single-language model, CPU only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from ..common.audio import TARGET_RATE, float_to_pcm16
from ..common.model_store import ModelSettings, Progress, prepare_model
from .backend import Transcript, Word
from .settings import VoskSettings

# Audio fed to the recognizer per call (0.5 s of PCM16): a finished utterance
# inside a long upload is collected via Result() as soon as Kaldi ends it.
FEED_BYTES = TARGET_RATE  # 8000 samples * 2 bytes


def _words(result: Dict[str, Any]) -> List[Word]:
    return [
        Word(
            str(w["word"]),
            round(float(w["conf"]), 4),
            round(float(w["start"]), 3),
            round(float(w["end"]), 3),
        )
        for w in result.get("result", ())
    ]


class KaldiVoskBackend:
    name = "vosk"

    def __init__(
        self, settings: VoskSettings, *, prepare: Callable = prepare_model
    ) -> None:
        self._settings = settings
        self.model_id = settings.model
        self.language = settings.language
        self._prepare = prepare
        self._path: Optional[Path] = None
        self._model: Optional[Any] = None

    def fetch(self, progress: Optional[Progress] = None) -> None:
        self._path = self._prepare(
            self._settings.model, ModelSettings.from_env(), progress
        )

    def load(self) -> None:
        if self._path is None:
            raise RuntimeError("load() before fetch()")
        from vosk import Model, SetLogLevel

        SetLogLevel(0 if self._settings.log_level in ("trace", "debug") else -1)
        self._model = Model(str(self._path))

    def unload(self) -> None:
        # vosk.Model frees the Kaldi model when the last reference goes
        self._model = None

    def recognizer(self) -> Any:
        """A fresh KaldiRecognizer with per-word results (final and partial)."""
        from vosk import KaldiRecognizer

        if self._model is None:
            raise RuntimeError("recognizer() before load()")
        rec = KaldiRecognizer(self._model, TARGET_RATE)
        rec.SetWords(True)
        rec.SetPartialWords(True)
        return rec

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        if self._model is None:
            raise RuntimeError("transcribe() before load()")
        rec = self.recognizer()
        pcm = float_to_pcm16(audio)
        results: List[Dict[str, Any]] = []
        for i in range(0, len(pcm), FEED_BYTES):
            if rec.AcceptWaveform(pcm[i : i + FEED_BYTES]):
                results.append(json.loads(rec.Result()))
        results.append(json.loads(rec.FinalResult()))
        words = [w for r in results for w in _words(r)]
        text = " ".join(t for t in (r.get("text", "").strip() for r in results) if t)
        return Transcript(text, tuple(words))
