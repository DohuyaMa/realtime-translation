"""rt-phonetic cpu path: the cached ONNX export and the backend's engine
choice per device (fakes; the real export is in test_svc_phonetic_real)."""

import json

import numpy as np
import pytest

from src.services.phonetic import onnx_export
from src.services.phonetic.settings import PhoneticSettings
from src.services.phonetic.wav2vec2_backend import (
    Wav2Vec2Backend,
    frame_seconds,
    normalize,
)

FAKE_MODEL_ENV = {"RT_PHONETIC_BACKEND": "wav2vec2"}


def fake_build(calls):
    def export(model_dir, out):
        calls.append(("export", out.name))
        out.write_bytes(b"fp32")

    def quantize(src, out):
        calls.append(("quantize", src.name, out.name))
        assert src.read_bytes() == b"fp32"
        out.write_bytes(b"int8")

    return export, quantize


@pytest.mark.parametrize("compute_type,steps", [("int8", 2), ("float32", 1)])
def test_ensure_onnx_exports_once_and_caches(
    tmp_path, monkeypatch, compute_type, steps
):
    calls = []
    export, quantize = fake_build(calls)
    monkeypatch.setattr(onnx_export, "export_float32", export)
    monkeypatch.setattr(onnx_export, "quantize_int8", quantize)
    path = onnx_export.ensure_onnx(tmp_path, compute_type)
    assert path == tmp_path / "onnx" / f"model.{compute_type}.onnx"
    assert path.read_bytes() == (b"int8" if compute_type == "int8" else b"fp32")
    assert len(calls) == steps
    assert onnx_export.ensure_onnx(tmp_path, compute_type) == path
    assert len(calls) == steps  # cached: no second export
    leftovers = [p.name for p in (tmp_path / "onnx").iterdir()]
    assert sorted(leftovers) == [".lock", path.name]  # temp files removed


def test_failed_export_leaves_no_model(tmp_path, monkeypatch):
    def broken(model_dir, out):
        out.write_bytes(b"partial")
        raise RuntimeError("export failed")

    monkeypatch.setattr(onnx_export, "export_float32", broken)
    with pytest.raises(RuntimeError, match="export failed"):
        onnx_export.ensure_onnx(tmp_path, "int8")
    assert [p.name for p in (tmp_path / "onnx").iterdir()] == [".lock"]


def test_onnx_path_rejects_torch_only_precision(tmp_path):
    with pytest.raises(ValueError, match="float16"):
        onnx_export.onnx_path(tmp_path, "float16")


def test_normalize_is_zero_mean_unit_variance():
    x = normalize(np.array([0.1, 0.3, -0.2, 0.6], np.float32))
    assert x.dtype == np.float32
    assert abs(float(x.mean())) < 1e-6 and abs(float(x.std()) - 1) < 1e-3


def test_frame_seconds_is_the_conv_stride_product():
    assert frame_seconds({"conv_stride": [5, 2, 2, 2, 2, 2, 2]}) == 0.02


def model_dir(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"pad_token_id": 0, "conv_stride": [5, 2, 2, 2, 2, 2, 2]})
    )
    (tmp_path / "vocab.json").write_text(json.dumps({"<pad>": 0, "|": 1}))
    return tmp_path


@pytest.mark.parametrize(
    "env,exported",
    [
        ({}, [("int8",)]),
        ({"RT_COMPUTE_TYPE": "float32"}, [("float32",)]),
        ({"RT_DEVICE": "cuda"}, []),  # torch on the GPU: no ONNX
    ],
)
def test_fetch_exports_onnx_only_on_cpu(tmp_path, env, exported):
    calls = []
    backend = Wav2Vec2Backend(
        PhoneticSettings.from_env({**FAKE_MODEL_ENV, **env}),
        prepare=lambda alias, settings, progress: model_dir(tmp_path),
        export=lambda path, ct: calls.append((ct,)) or path / "m.onnx",
    )
    backend.fetch()
    assert calls == exported


def test_load_before_fetch_fails(tmp_path):
    backend = Wav2Vec2Backend(PhoneticSettings.from_env(FAKE_MODEL_ENV))
    with pytest.raises(RuntimeError, match="before fetch"):
        backend.load()
