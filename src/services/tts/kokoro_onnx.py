# src/services/tts/kokoro_onnx.py
"""Kokoro-82M on onnxruntime (onnx-community/Kokoro-82M-v1.0-ONNX), spec §4.4."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from ..common.device import DeviceError
from .g2p import KokoroG2P, UnknownVoice, lang_code_for_voice

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
ONNX_FILES = {
    "float32": "onnx/model.onnx",
    "float16": "onnx/model_fp16.onnx",
    "int8": "onnx/model_quantized.onnx",
}
_ALLOWED = {
    ("cpu", "float32"),
    ("cpu", "int8"),
    ("cuda", "float32"),
    ("cuda", "float16"),
}


def load_vocab(model_dir: Path) -> dict[str, int]:
    data = json.loads((Path(model_dir) / "tokenizer.json").read_text(encoding="utf-8"))
    return dict(data["model"]["vocab"])


def phonemes_to_ids(phonemes: str, vocab: dict[str, int]) -> list[int]:
    return [vocab[ch] for ch in phonemes if ch in vocab]


def list_voices(model_dir: Path, suffix: str) -> list[str]:
    voices_dir = Path(model_dir) / "voices"
    return sorted(
        p.name[: -len(suffix)] for p in voices_dir.glob(f"*{suffix}") if p.is_file()
    )


def _default_session_factory(path: str, device: str, threads: int):
    import onnxruntime as ort

    if device == "cuda":
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise DeviceError(
                "RT_DEVICE=cuda but onnxruntime has no CUDAExecutionProvider"
            )
        providers = [
            ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
            "CPUExecutionProvider",
        ]
    else:
        providers = ["CPUExecutionProvider"]
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    return ort.InferenceSession(path, sess_options=options, providers=providers)


class KokoroOnnxSynthesizer:
    sample_rate = SAMPLE_RATE

    def __init__(
        self,
        model_dir: Path,
        device: str = "cpu",
        compute_type: str = "float32",
        threads: int = 4,
        *,
        session_factory: Callable | None = None,
        g2p_factory: Callable | None = None,
    ):
        if device not in ("cpu", "cuda"):
            raise DeviceError(
                f"rt-tts supports cpu|cuda only (spec D2), got {device!r}"
            )
        if (device, compute_type) not in _ALLOWED:
            raise ValueError(
                f"compute_type {compute_type!r} is not allowed on {device}"
            )
        self.model_dir = Path(model_dir)
        self._vocab = load_vocab(self.model_dir)
        self._voice_names = set(list_voices(self.model_dir, ".bin"))
        self._packs: dict[str, np.ndarray] = {}
        self._g2p: dict[str, Callable] = {}
        self._g2p_factory = g2p_factory or KokoroG2P
        factory = session_factory or _default_session_factory
        self._session = factory(
            str(self.model_dir / ONNX_FILES[compute_type]), device, threads
        )

    def voices(self) -> list[str]:
        return sorted(self._voice_names)

    def _pack(self, voice: str) -> np.ndarray:
        if voice not in self._voice_names:
            raise UnknownVoice(f"unknown voice {voice!r}; available: {self.voices()}")
        lang_code_for_voice(voice)
        if voice not in self._packs:
            path = self.model_dir / "voices" / f"{voice}.bin"
            self._packs[voice] = np.fromfile(path, dtype=np.float32).reshape(-1, 1, 256)
        return self._packs[voice]

    def synthesize(
        self, text: str, voice: str = "af_heart", speed: float = 1.0
    ) -> Iterator[np.ndarray]:
        pack = self._pack(voice)
        if not text.strip():
            return
        lang = lang_code_for_voice(voice)
        if lang not in self._g2p:
            self._g2p[lang] = self._g2p_factory(lang)
        for chunk in self._g2p[lang](text):
            ids = phonemes_to_ids(chunk.phonemes, self._vocab)[:MAX_PHONEMES]
            if not ids:
                continue
            style = pack[min(len(ids) - 1, len(pack) - 1)]
            feeds = {
                "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
                "style": np.asarray(style, dtype=np.float32),
                "speed": np.array([speed], dtype=np.float32),
            }
            audio = self._session.run(None, feeds)[0]
            yield np.asarray(audio, dtype=np.float32).reshape(-1)
