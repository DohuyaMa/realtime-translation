"""ONNX copy of the pinned wav2vec2 CTC weights for the cpu path.

No ONNX of the model is published, so the node exports it once from the
verified ``model.safetensors`` (torch, TorchScript exporter) and, for
``int8``, quantizes the MatMul weights dynamically, per channel
(onnxruntime; the conv feature encoder stays float32). Owner's session
2026-11-01 (39 units): float32 ONNX == torch on all, int8 == float32 text on
32 (the rest differ by a letter or a space), the same gate on all 39.
The result is cached next to the weights
(``<model dir>/onnx/model.<compute_type>.onnx``) and goes away with them
when the pinned revision changes. Runs from ``fetch()`` (no load timeout):
the export takes about a minute and ~3 GiB RAM, later loads only open the
cached file. One writer at a time (``flock``), atomic rename.
"""

from __future__ import annotations

import fcntl
import gc
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

ONNX_DIR = "onnx"
OPSET = 17
ONNX_COMPUTE_TYPES = ("int8", "float32")


def onnx_path(model_dir: Path, compute_type: str) -> Path:
    if compute_type not in ONNX_COMPUTE_TYPES:
        raise ValueError(
            f"onnx: compute type {compute_type!r} not in {ONNX_COMPUTE_TYPES}"
        )
    return Path(model_dir) / ONNX_DIR / f"model.{compute_type}.onnx"


def ensure_onnx(model_dir: Path, compute_type: str) -> Path:
    """Path of the cached ONNX model; exports (and quantizes) when absent."""
    target = onnx_path(model_dir, compute_type)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.parent / ".lock", "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if target.is_file():  # another process exported it meanwhile
                return target
            _build(Path(model_dir), compute_type, target)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return target


def _build(model_dir: Path, compute_type: str, target: Path) -> None:
    tmp_fp32 = target.with_name(f".export-{os.getpid()}.float32.onnx")
    tmp_out = target.with_name(f".export-{os.getpid()}.{compute_type}.onnx")
    try:
        log.info("rt-phonetic: exporting %s to ONNX (once)", model_dir.name)
        export_float32(model_dir, tmp_fp32)
        if compute_type == "int8":
            log.info("rt-phonetic: quantizing to int8 (dynamic, MatMul)")
            quantize_int8(tmp_fp32, tmp_out)
        else:
            os.replace(tmp_fp32, tmp_out)
        os.replace(tmp_out, target)
        log.info("rt-phonetic: ONNX model ready: %s", target)
    finally:
        for p in (tmp_fp32, tmp_out):
            p.unlink(missing_ok=True)


def export_float32(model_dir: Path, out: Path) -> None:
    import torch
    from transformers import Wav2Vec2ForCTC

    model = Wav2Vec2ForCTC.from_pretrained(str(model_dir), dtype=torch.float32)
    model.eval()
    with torch.inference_mode():
        torch.onnx.export(
            model,
            (torch.zeros(1, 16000),),
            str(out),
            input_names=["input_values"],
            output_names=["logits"],
            dynamic_axes={"input_values": {1: "samples"}, "logits": {1: "frames"}},
            opset_version=OPSET,
            dynamo=False,
        )
    del model
    gc.collect()


def quantize_int8(src: Path, out: Path) -> None:
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(
        str(src),
        str(out),
        op_types_to_quantize=["MatMul"],
        per_channel=True,  # owner's session: 32/39 texts == fp32 (per-tensor 27/39)
        weight_type=QuantType.QInt8,
    )
