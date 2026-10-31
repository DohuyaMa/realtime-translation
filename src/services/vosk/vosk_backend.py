"""Vosk (Kaldi) backend: one model per language, CPU only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        self._prepare = prepare
        self._paths: Dict[str, Path] = {}
        self._models: Dict[str, Any] = {}

    def fetch(self, progress: Optional[Progress] = None) -> None:
        # one model after another; the download progress restarts per model
        model_settings = ModelSettings.from_env()
        self._paths = {
            lang: self._prepare(alias, model_settings, progress)
            for lang, alias in self._settings.languages.items()
        }

    def load(self) -> None:
        if not self._paths:
            raise RuntimeError("load() before fetch()")
        from vosk import Model, SetLogLevel

        SetLogLevel(0 if self._settings.log_level in ("trace", "debug") else -1)
        self._models = {lang: Model(str(path)) for lang, path in self._paths.items()}

    def unload(self) -> None:
        # vosk.Model frees the Kaldi model when the last reference goes
        self._models = {}

    def languages(self) -> Tuple[str, ...]:
        return tuple(self._models)

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(self._models[language], TARGET_RATE)
        rec.SetWords(True)
        pcm = float_to_pcm16(audio)
        results: List[Dict[str, Any]] = []
        for i in range(0, len(pcm), FEED_BYTES):
            if rec.AcceptWaveform(pcm[i : i + FEED_BYTES]):
                results.append(json.loads(rec.Result()))
        results.append(json.loads(rec.FinalResult()))
        words = [w for r in results for w in _words(r)]
        text = " ".join(t for t in (r.get("text", "").strip() for r in results) if t)
        return Transcript(text, tuple(words))
