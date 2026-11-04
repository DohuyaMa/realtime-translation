"""wav2vec2 CTC backend: greedy decoding, no LM; the engine follows the device.

cpu: onnxruntime on the ONNX copy of the pinned weights (``compute_type``
int8 - default, dynamic MatMul quantization - or float32), exported once by
``fetch()`` (``onnx_export``); ``RT_NUM_THREADS`` intra-op threads (default
min(4, cpus)); no VRAM.
cuda: torch with the model in ``compute_type`` (float16 default, float32) on
the GPU; unload frees its VRAM (``torch.cuda.empty_cache``) before another
load. The device is switched at runtime via ``POST /config`` (the
«Фонетика» tab of the edge): unload, fetch (export on the first cpu load),
load.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

from ..common.audio import TARGET_RATE
from ..common.device import default_threads
from ..common.model_store import ModelSettings, Progress, prepare_model
from .backend import Transcript, ctc_words
from .onnx_export import ensure_onnx
from .settings import PhoneticSettings

# shorter audio has no frame for the conv feature encoder (400 samples)
MIN_SAMPLES = 400


def normalize(audio: np.ndarray) -> np.ndarray:
    """Wav2Vec2FeatureExtractor(do_normalize=True): zero mean, unit variance."""
    x = audio.astype(np.float32, copy=False)
    return ((x - x.mean()) / np.sqrt(x.var() + 1e-7)).astype(np.float32)


def frame_seconds(config: Dict[str, Any]) -> float:
    """Audio seconds per logits frame (product of the conv strides)."""
    ratio = 1
    for stride in config["conv_stride"]:
        ratio *= int(stride)
    return ratio / TARGET_RATE


class Wav2Vec2Backend:
    name = "wav2vec2"

    def __init__(
        self,
        settings: PhoneticSettings,
        *,
        prepare: Callable = prepare_model,
        export: Callable[[Path, str], Path] = ensure_onnx,
    ) -> None:
        self._settings = settings
        self.model_id = settings.model
        self.language = settings.language
        self._prepare = prepare
        self._export = export
        self._path: Optional[Path] = None
        self._onnx: Optional[Path] = None
        self._run: Optional[Callable[[np.ndarray], np.ndarray]] = None
        self._release: Callable[[], None] = lambda: None
        self._vocab: Dict[int, str] = {}
        self._blank = 0
        self._frame_s = 0.02

    @property
    def on_cpu(self) -> bool:
        return self._settings.torch_device == "cpu"

    def fetch(self, progress: Optional[Progress] = None) -> None:
        self._path = self._prepare(
            self._settings.model, ModelSettings.from_env(), progress
        )
        if self.on_cpu:
            self._onnx = self._export(self._path, self._settings.compute_type)

    def load(self) -> None:
        if self._path is None or (self.on_cpu and self._onnx is None):
            raise RuntimeError("load() before fetch()")
        config = json.loads((self._path / "config.json").read_text(encoding="utf-8"))
        vocab = json.loads((self._path / "vocab.json").read_text(encoding="utf-8"))
        blank = config.get("pad_token_id")
        if blank is None:
            raise RuntimeError(f"{self.model_id}: config.json has no pad_token_id")
        self._vocab = {int(i): str(t) for t, i in vocab.items()}
        self._blank = int(blank)  # the CTC blank
        self._frame_s = frame_seconds(config)
        if self.on_cpu:
            self._load_onnx()
        else:
            self._load_torch()

    def _load_onnx(self) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = default_threads()
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.log_severity_level = 3  # no device-discovery warnings in the journal
        session = ort.InferenceSession(
            str(self._onnx), opts, providers=["CPUExecutionProvider"]
        )

        def run(x: np.ndarray) -> np.ndarray:
            return session.run(["logits"], {"input_values": x[None]})[0][0]

        self._run = run

    def _load_torch(self) -> None:
        import torch
        from transformers import Wav2Vec2ForCTC
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()  # no tqdm bars in the journal
        if not torch.cuda.is_available():
            raise RuntimeError("device cuda: torch sees no CUDA GPU")
        dtype = getattr(torch, self._settings.compute_type)
        model = Wav2Vec2ForCTC.from_pretrained(str(self._path), dtype=dtype)
        model.to("cuda")  # type: ignore[arg-type]  # transformers' typed wrapper
        model.eval()

        def run(x: np.ndarray) -> np.ndarray:
            inp = torch.from_numpy(x[None]).to("cuda", dtype)
            with torch.inference_mode():
                return model(inp).logits[0].float().cpu().numpy()

        def release() -> None:
            gc.collect()
            torch.cuda.empty_cache()

        self._run, self._release = run, release

    def unload(self) -> None:
        self._run = None
        release, self._release = self._release, lambda: None
        release()

    def transcribe(self, audio: np.ndarray, language: str) -> Transcript:
        if self._run is None:
            raise RuntimeError("transcribe() before load()")
        if len(audio) < MIN_SAMPLES:
            return Transcript("", ())
        ids = self._run(normalize(audio)).argmax(axis=-1).tolist()
        words = ctc_words(ids, self._vocab, self._blank, self._frame_s)
        return Transcript(" ".join(w.word for w in words), tuple(words))
