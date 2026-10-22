import pytest

from src.services.common.device import DeviceError
from src.services.tts import engines


def test_create_synthesizer_onnx(monkeypatch, tmp_path):
    created = {}
    monkeypatch.setattr(
        engines, "KokoroOnnxSynthesizer", lambda *a: created.setdefault("onnx", a)
    )
    engines.create_synthesizer("onnx", tmp_path, "cpu", None, 2)
    assert created["onnx"] == (tmp_path, "cpu", "float32", 2)
    engines.create_synthesizer("onnx", tmp_path, "cuda", "float16", 2)
    assert engines.DEFAULT_TTS_ENGINE == "onnx"


def test_create_synthesizer_torch_deferred(tmp_path):
    with pytest.raises(ValueError, match="engine 'torch' is deferred"):
        engines.create_synthesizer("torch", tmp_path, "cpu", None, 2)


def test_create_synthesizer_rocm_rejected(tmp_path):
    with pytest.raises(DeviceError):
        engines.create_synthesizer("onnx", tmp_path, "rocm", None, 2)
