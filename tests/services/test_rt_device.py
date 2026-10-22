# tests/services/test_rt_device.py
import logging
import os

import pytest

from src.services.common import device as dv


def test_normalize_device():
    assert dv.normalize_device(None) == "cpu"
    assert dv.normalize_device("") == "cpu"
    assert dv.normalize_device(" CUDA ") == "cuda"
    assert dv.normalize_device("rocm") == "rocm"
    with pytest.raises(dv.DeviceError, match="tpu"):
        dv.normalize_device("tpu")


def test_backend_device():
    assert dv.backend_device("cpu") == "cpu"
    assert dv.backend_device("cuda") == "cuda"
    assert dv.backend_device("rocm") == "cuda"


@pytest.mark.parametrize(
    "service,device,expected",
    [
        ("asr", "cpu", "int8"),
        ("asr", "cuda", "int8_float16"),
        ("asr", "rocm", "int8_float16"),
        ("tts", "cpu", "float32"),
        ("tts", "cuda", "float32"),
    ],
)
def test_default_compute_type(service, device, expected):
    assert dv.default_compute_type(service, device) == expected


def test_tts_has_no_rocm():
    with pytest.raises(dv.DeviceError, match="rocm"):
        dv.default_compute_type("tts", "rocm")


@pytest.mark.parametrize(
    "service,device,expected",
    [
        ("asr", "cpu", "whisper-small"),
        ("asr", "rocm", "whisper-small"),
        ("asr", "cuda", "whisper-large-v3-turbo"),
        ("tts", "cpu", "kokoro-82m-v1.0-onnx"),
        ("tts", "cuda", "kokoro-82m-v1.0-onnx"),
    ],
)
def test_default_model(service, device, expected):
    assert dv.default_model(service, device) == expected


def test_default_beams():
    assert dv.default_beams("asr", "cpu") == 1
    assert dv.default_beams("asr", "cuda") == 3


def test_default_threads():
    assert dv.default_threads({"RT_NUM_THREADS": "3"}) == 3
    assert dv.default_threads({}) == min(4, os.cpu_count() or 1)
    with pytest.raises(dv.DeviceError):
        dv.default_threads({"RT_NUM_THREADS": "0"})
    with pytest.raises(dv.DeviceError):
        dv.default_threads({"RT_NUM_THREADS": "many"})


def test_resolve_ct2_compute_type(caplog):
    assert (
        dv.resolve_ct2_compute_type("int8_float16", {"int8_float16", "int8"})
        == "int8_float16"
    )
    with caplog.at_level(logging.WARNING):
        assert (
            dv.resolve_ct2_compute_type("int8_float16", {"int8", "float32"}) == "int8"
        )
    assert "falling back" in caplog.text
    assert dv.resolve_ct2_compute_type("float16", {"float32"}) == "float32"
    with pytest.raises(dv.DeviceError):
        dv.resolve_ct2_compute_type("int8", set())


def test_require_gpu():
    dv.require_gpu("cpu", 0)
    dv.require_gpu("cuda", 1)
    with pytest.raises(dv.DeviceError, match="no GPU"):
        dv.require_gpu("cuda", 0)
    with pytest.raises(dv.DeviceError):
        dv.require_gpu("rocm", 0)
