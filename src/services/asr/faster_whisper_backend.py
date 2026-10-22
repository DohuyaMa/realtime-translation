"""faster-whisper backend on top of WhisperEngine."""

from __future__ import annotations

from typing import Any, Callable, Iterator, Optional, Sequence

import numpy as np

from ..common.device import default_threads
from ..common.model_store import ModelSettings, prepare_model
from .backend import DecodeOptions, Segment, Transcription
from .settings import AsrSettings
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
        self._engine: Optional[Any] = None

    def load(self) -> None:
        path = self._prepare(self._settings.model, ModelSettings.from_env())
        cls = self._engine_cls or WhisperEngine
        self._engine = cls.from_settings(
            path, self._settings.device, self._settings.compute_type, default_threads()
        )

    def transcribe(self, audio: np.ndarray, opts: DecodeOptions) -> Transcription:
        partial = (
            opts.without_timestamps and opts.beam_size == 1 and not opts.vad_filter
        )
        r = self._engine.transcribe(
            audio,
            language=opts.language,
            beam_size=opts.beam_size,
            vad=FILE_VAD if opts.vad_filter else None,
            initial_prompt=opts.prompt,
            partial=partial,
        )
        return Transcription(
            text=r.text,
            language=r.language or opts.language,
            duration=r.duration,
            segments=[Segment(s.start, s.end, s.text) for s in r.segments],
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
        return (Segment(s.start, s.end, s.text) for s in segments)

    def detect_language(self, audio: np.ndarray, allowed: Sequence[str]) -> str:
        return self._engine.detect_language(audio, allowed)[0]
