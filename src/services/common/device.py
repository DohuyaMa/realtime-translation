# src/services/common/device.py
"""RT_DEVICE / RT_COMPUTE_TYPE / RT_NUM_THREADS resolution (spec §4.2-§4.4, §4.8)."""

from __future__ import annotations

import logging
import os
from typing import Mapping

log = logging.getLogger(__name__)

DEVICES = ("cpu", "cuda", "rocm")


class DeviceError(RuntimeError):
    """Invalid or unavailable device / compute configuration."""


_DEFAULT_COMPUTE = {
    ("asr", "cpu"): "int8",
    ("asr", "cuda"): "int8_float16",
    ("asr", "rocm"): "int8_float16",
    ("tts", "cpu"): "float32",
    ("tts", "cuda"): "float32",
}

_DEFAULT_MODEL = {
    ("asr", "cpu"): "whisper-small",
    ("asr", "cuda"): "whisper-large-v3-turbo",
    ("asr", "rocm"): "whisper-small",
    ("tts", "cpu"): "kokoro-82m-v1.0-onnx",
    ("tts", "cuda"): "kokoro-82m-v1.0-onnx",
}

_DEFAULT_BEAMS = {
    ("asr", "cpu"): 1,
    ("asr", "cuda"): 3,
    ("asr", "rocm"): 3,
}

# Preference chains when a CT2 build/device lacks the requested type.
_CT2_FALLBACK = {
    "int8_float16": ("int8_float16", "int8", "int8_float32", "float32"),
    "int8_bfloat16": ("int8_bfloat16", "int8_float16", "int8", "float32"),
    "int8_float32": ("int8_float32", "int8", "float32"),
    "int8": ("int8", "int8_float32", "float32"),
    "float16": ("float16", "float32"),
    "bfloat16": ("bfloat16", "float16", "float32"),
    "float32": ("float32",),
}


def normalize_device(value: str | None) -> str:
    device = (value or "cpu").strip().lower()
    if device not in DEVICES:
        raise DeviceError(f"RT_DEVICE must be one of {DEVICES}, got {value!r}")
    return device


def backend_device(device: str) -> str:
    # CTranslate2 HIP builds and ORT keep the "cuda" device string (spec A5).
    return "cpu" if device == "cpu" else "cuda"


def _lookup(table: dict, service: str, device: str, what: str):
    try:
        return table[(service, device)]
    except KeyError:
        raise DeviceError(
            f"no {what} for service={service!r} device={device!r} "
            f"(rt-tts has no rocm variant, spec D2)"
        ) from None


def default_compute_type(service: str, device: str) -> str:
    return _lookup(_DEFAULT_COMPUTE, service, device, "compute type")


def default_model(service: str, device: str) -> str:
    return _lookup(_DEFAULT_MODEL, service, device, "default model")


def default_beams(service: str, device: str) -> int:
    return _lookup(_DEFAULT_BEAMS, service, device, "default beam size")


def default_threads(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    raw = env.get("RT_NUM_THREADS")
    if not raw:
        return min(4, os.cpu_count() or 1)
    try:
        threads = int(raw)
    except ValueError:
        raise DeviceError(f"RT_NUM_THREADS must be an integer, got {raw!r}") from None
    if threads < 1:
        raise DeviceError(f"RT_NUM_THREADS must be >= 1, got {threads}")
    return threads


def resolve_ct2_compute_type(requested: str, supported: set[str]) -> str:
    for candidate in _CT2_FALLBACK.get(requested, (requested,)):
        if candidate in supported:
            if candidate != requested:
                log.warning(
                    "compute_type %s not supported here; falling back to %s",
                    requested,
                    candidate,
                )
            return candidate
    raise DeviceError(
        f"compute_type {requested!r} unsupported; supported={sorted(supported)}"
    )


def require_gpu(device: str, gpu_count: int) -> None:
    if device != "cpu" and gpu_count < 1:
        raise DeviceError(f"RT_DEVICE={device} but no GPU is visible to the backend")
