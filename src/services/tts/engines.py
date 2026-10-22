# src/services/tts/engines.py
"""RT_TTS_ENGINE dispatch (spec §4.4); only the onnx engine is supported."""

from __future__ import annotations

from pathlib import Path

from ..common.device import default_compute_type
from .kokoro_onnx import KokoroOnnxSynthesizer

DEFAULT_TTS_ENGINE = "onnx"


def create_synthesizer(
    engine: str, model_dir: Path, device: str, compute_type: str | None, threads: int
):
    if engine != "onnx":
        raise ValueError(f"engine {engine!r} is deferred")
    return KokoroOnnxSynthesizer(
        model_dir, device, compute_type or default_compute_type("tts", device), threads
    )
