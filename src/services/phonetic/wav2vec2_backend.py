"""wav2vec2 CTC backend (transformers + CPU torch): greedy decoding, no LM.

``torch.set_num_threads(RT_NUM_THREADS)`` (default min(4, cpus)) at load.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from ..common.audio import TARGET_RATE
from ..common.device import default_threads
from ..common.model_store import ModelSettings, Progress, prepare_model
from .backend import Transcript, ctc_words
from .settings import PhoneticSettings

# shorter audio has no frame for the conv feature encoder (400 samples)
MIN_SAMPLES = 400


class Wav2Vec2Backend:
    name = "wav2vec2"

    def __init__(
        self, settings: PhoneticSettings, *, prepare: Callable = prepare_model
    ) -> None:
        self._settings = settings
        self.model_id = settings.model
        self.language = settings.language
        self._prepare = prepare
        self._path: Optional[Path] = None
        self._model: Any = None
        self._features: Any = None
        self._vocab: Dict[int, str] = {}
        self._blank = 0
        self._frame_s = 0.02

    def fetch(self, progress: Optional[Progress] = None) -> None:
        self._path = self._prepare(
            self._settings.model, ModelSettings.from_env(), progress
        )

    def load(self) -> None:
        if self._path is None:
            raise RuntimeError("load() before fetch()")
        import torch
        from transformers import Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()  # no tqdm bars in the journal
        torch.set_num_threads(default_threads())
        path = str(self._path)
        vocab = json.loads((self._path / "vocab.json").read_text(encoding="utf-8"))
        self._vocab = {int(i): str(t) for t, i in vocab.items()}
        self._features = Wav2Vec2FeatureExtractor.from_pretrained(path)
        model = Wav2Vec2ForCTC.from_pretrained(path)
        model.eval()
        blank = model.config.pad_token_id
        if blank is None:
            raise RuntimeError(f"{self.model_id}: config.json has no pad_token_id")
        self._blank = int(blank)  # the CTC blank
        self._frame_s = float(model.config.inputs_to_logits_ratio) / TARGET_RATE
        self._model = model

    def unload(self) -> None:
        self._model = None
        self._features = None

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        if self._model is None:
            raise RuntimeError("transcribe() before load()")
        if len(audio) < MIN_SAMPLES:
            return Transcript("", ())
        import torch

        x = self._features(
            audio, sampling_rate=TARGET_RATE, return_tensors="pt"
        ).input_values
        with torch.inference_mode():
            logits = self._model(x).logits[0]
        ids = logits.argmax(dim=-1).tolist()
        words = ctc_words(ids, self._vocab, self._blank, self._frame_s)
        return Transcript(" ".join(w.word for w in words), tuple(words))
