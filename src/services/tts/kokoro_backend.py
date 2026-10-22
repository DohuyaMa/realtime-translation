"""Kokoro-82M backend delegating to an engine synthesizer (ONNX)."""

from __future__ import annotations

from typing import Any, Callable, Iterator, List, Optional

import numpy as np

from ..common.device import default_threads
from ..common.model_store import ModelSettings, prepare_model
from .backend import UnsupportedVoiceLanguage
from .engines import create_synthesizer
from .g2p import UnknownVoice, lang_code_for_voice
from .settings import TtsSettings
from .voices import Voice, catalog


class KokoroBackend:
    name = "kokoro"
    sample_rate = 24000

    def __init__(
        self,
        settings: TtsSettings,
        *,
        synth_factory: Callable = create_synthesizer,
        prepare: Callable = prepare_model,
    ) -> None:
        self._settings = settings
        self.model_id = settings.model
        self._synth_factory = synth_factory
        self._prepare = prepare
        self._synth: Optional[Any] = None

    def load(self) -> None:
        path = self._prepare(self._settings.model, ModelSettings.from_env())
        self._synth = self._synth_factory(
            self._settings.engine,
            path,
            self._settings.device,
            self._settings.compute_type,
            default_threads(),
        )
        self.prepare_voice(self._settings.voice)

    def voices(self) -> List[Voice]:
        available = set(self._synth.voices())
        return [v for v in catalog() if v.id in available]

    def prepare_voice(self, voice: str) -> None:
        try:
            lang_code_for_voice(voice)
        except UnknownVoice as exc:
            raise UnsupportedVoiceLanguage(str(exc)) from exc

    def synthesize(self, text: str, voice: str, speed: float) -> Iterator[np.ndarray]:
        self.prepare_voice(voice)
        yield from self._synth.synthesize(text, voice=voice, speed=speed)
