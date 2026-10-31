"""faster-whisper backend on top of WhisperEngine."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

import numpy as np

from ..common.device import default_threads
from ..common.model_store import ModelSettings, Progress, prepare_model
from .backend import DecodeOptions, Segment, Transcription
from .settings import AsrSettings
from .vad import SileroVad, Vad
from .whisper_engine import FILE_VAD, WhisperEngine


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(
        self,
        settings: AsrSettings,
        *,
        engine_cls: Optional[Any] = None,
        prepare: Callable = prepare_model,
    ) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._engine_cls = engine_cls
        self._prepare = prepare
        self._path: Optional[Path] = None
        self._engine: Optional[Any] = None

    def fetch(self, progress: Optional[Progress] = None) -> None:
        self._path = self._prepare(
            self._settings.model, ModelSettings.from_env(), progress
        )

    def load(self) -> None:
        if self._path is None:
            raise RuntimeError("load() before fetch()")
        cls = self._engine_cls or WhisperEngine
        self._engine = cls.from_settings(
            self._path,
            self._settings.device,
            self._settings.compute_type,
            default_threads(),
        )

    def unload(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.close()

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription:
        r = self._engine.transcribe(
            audio,
            language=opts.language,
            beam_size=opts.beam_size,
            vad=FILE_VAD if opts.vad_filter else None,
            initial_prompt=opts.prompt,
        )
        return Transcription(
            text=r.text,
            language=r.language or opts.language,
            duration=r.duration,
            segments=[Segment(s.start, s.end, s.text, s.stats) for s in r.segments],
        )

    def transcribe_iter(
        self, audio: np.ndarray, opts: DecodeOptions
    ) -> Iterator[Segment]:
        if opts.language is None:
            raise ValueError("transcribe_iter requires opts.language")
        segments = self._engine.iter_transcribe(
            audio,
            language=opts.language,
            beam_size=opts.beam_size,
            vad=FILE_VAD if opts.vad_filter else None,
            initial_prompt=opts.prompt,
        )
        return (Segment(s.start, s.end, s.text, s.stats) for s in segments)

    def detect_language(self, audio: np.ndarray, allowed: Sequence[str]) -> str:
        return self._engine.detect_language(audio, allowed)[0]

    def stream_decode(
        self, audio: np.ndarray, language: str, prompt: Optional[str]
    ) -> list[Segment]:
        return [
            Segment(s.start, s.end, s.text, s.stats, s.words)
            for s in self._engine.stream_decode(
                audio, language=language, initial_prompt=prompt
            )
        ]

    def new_vad(self) -> Vad:
        return SileroVad()
