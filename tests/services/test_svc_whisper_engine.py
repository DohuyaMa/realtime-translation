# tests/services/test_rt_whisper_engine.py
import sys
import types

import numpy as np
import pytest

from src.services.asr import whisper_engine as we
from src.services.common.device import DeviceError


class FakeSeg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class FakeModel:
    def __init__(self, path, **kw):
        self.path, self.kw, self.calls = path, kw, []

    def transcribe(self, audio, **kw):
        self.calls.append((audio, kw))
        return iter(
            [FakeSeg(0.0, 1.0, " Привіт "), FakeSeg(1.0, 2.0, "світ")]
        ), types.SimpleNamespace(language="uk")


@pytest.fixture
def engine(tmp_path):
    return we.WhisperEngine(
        tmp_path, "cuda", "int8_float16", 4, model_factory=FakeModel
    )


def test_constructor_args(engine, tmp_path):
    assert engine._model.path == str(tmp_path)
    assert engine._model.kw == {
        "device": "cuda",
        "compute_type": "int8_float16",
        "cpu_threads": 4,
        "num_workers": 1,
    }


def test_final_transcribe_kwargs(engine):
    result = engine.transcribe(
        np.zeros(32000, dtype=np.float32), language="uk", beam_size=3
    )
    _, kw = engine._model.calls[0]
    assert kw["language"] == "uk" and kw["beam_size"] == 3
    assert kw["temperature"] == [0.0, 0.2, 0.4]
    assert kw["condition_on_previous_text"] is False
    assert kw["no_speech_threshold"] == 0.6
    assert kw["compression_ratio_threshold"] == 2.4
    assert kw["log_prob_threshold"] == -1.0
    assert kw["without_timestamps"] is False
    assert kw["vad_filter"] is True
    assert kw["vad_parameters"] == we.FILE_VAD.as_dict()
    assert "initial_prompt" not in kw
    assert result.text == "Привіт світ"
    assert result.language == "uk"
    assert result.duration == 2.0
    assert [s.text for s in result.segments] == ["Привіт", "світ"]


def test_partial_transcribe_kwargs(engine):
    engine.transcribe(
        np.zeros(16000, dtype=np.float32),
        language="pl",
        beam_size=5,
        vad=we.STREAMING_VAD,
        initial_prompt="kontekst",
        partial=True,
    )
    _, kw = engine._model.calls[0]
    assert (
        kw["beam_size"] == 1
        and kw["temperature"] == 0.0
        and kw["without_timestamps"] is True
    )
    assert kw["vad_parameters"] == we.STREAMING_VAD.as_dict()
    assert kw["initial_prompt"] == "kontekst"


def test_no_vad(engine):
    engine.transcribe(np.zeros(16000, dtype=np.float32), language="uk", vad=None)
    _, kw = engine._model.calls[0]
    assert kw["vad_filter"] is False and "vad_parameters" not in kw


def test_empty_audio_skips_model(engine):
    result = engine.transcribe(np.zeros(0, dtype=np.float32), language="uk")
    assert result.text == "" and engine._model.calls == []


def test_int16_audio_is_scaled(engine):
    engine.transcribe(np.full(1600, 16384, dtype=np.int16), language="uk")
    audio, _ = engine._model.calls[0]
    assert audio.dtype == np.float32
    assert np.allclose(audio, 0.5)


def test_stereo_rejected(engine):
    with pytest.raises(ValueError, match="mono"):
        engine.transcribe(np.zeros((1600, 2), dtype=np.float32), language="uk")


def test_vad_profiles():
    assert we.STREAMING_VAD.as_dict() == {
        "threshold": 0.5,
        "neg_threshold": 0.35,
        "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 400,
        "speech_pad_ms": 200,
        "max_speech_duration_s": 12.0,
    }
    assert we.FILE_VAD.as_dict() == {
        "threshold": 0.5,
        "min_speech_duration_ms": 0,
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 300,
        "max_speech_duration_s": 30.0,
    }


def _fake_modules(monkeypatch, gpus, supported):
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        types.SimpleNamespace(
            get_cuda_device_count=lambda: gpus,
            get_supported_compute_types=lambda device: set(supported),
        ),
    )
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel)
    )


def test_from_settings_default_compute(monkeypatch, tmp_path):
    _fake_modules(monkeypatch, gpus=0, supported={"int8", "int8_float32", "float32"})
    engine = we.WhisperEngine.from_settings(tmp_path, "cpu", None, 2)
    assert engine.compute_type == "int8"
    assert engine._model.kw["device"] == "cpu"


def test_from_settings_requires_gpu(monkeypatch, tmp_path):
    _fake_modules(monkeypatch, gpus=0, supported={"int8"})
    with pytest.raises(DeviceError):
        we.WhisperEngine.from_settings(tmp_path, "cuda", None, 2)


class LazyModel(FakeModel):
    def __init__(self, path, **kw):
        super().__init__(path, **kw)
        self.consumed = 0
        self.started = False

    def transcribe(self, audio, **kw):
        self.started = True
        self.calls.append((audio, kw))

        def gen():
            for i in range(3):
                self.consumed += 1
                yield FakeSeg(i, i + 1, f"s{i}")

        return gen(), types.SimpleNamespace(language="uk")


def test_iter_transcribe_is_lazy(tmp_path):
    engine = we.WhisperEngine(tmp_path, "cpu", "int8", 1, model_factory=LazyModel)
    it = engine.iter_transcribe(np.zeros(16000, dtype=np.float32), language="uk")
    assert engine._model.started is False
    first = next(it)
    assert first.text == "s0" and engine._model.consumed == 1
    _, kw = engine._model.calls[0]
    assert kw["temperature"] == [0.0, 0.2, 0.4] and kw["language"] == "uk"
    assert [s.text for s in it] == ["s1", "s2"]


class DetectModel(FakeModel):
    def detect_language(self, audio=None, **kw):
        self.detect_audio = audio
        return "ru", 0.6, [("ru", 0.6), ("uk", 0.3), ("pl", 0.1)]


def test_detect_language_restricted_to_allowed(tmp_path):
    engine = we.WhisperEngine(tmp_path, "cpu", "int8", 1, model_factory=DetectModel)
    audio = np.zeros(16000 * 40, dtype=np.float32)
    assert engine.detect_language(audio, ["pl", "uk"]) == ("uk", 0.3)
    assert engine._model.detect_audio.size == 16000 * 30


def test_detect_language_tie_prefers_allowed_order(tmp_path):
    engine = we.WhisperEngine(tmp_path, "cpu", "int8", 1, model_factory=DetectModel)
    engine._model.detect_language = lambda audio=None: (
        "a",
        0.5,
        [("a", 0.5), ("b", 0.5)],
    )
    assert engine.detect_language(np.zeros(100, dtype=np.float32), ["b", "a"]) == (
        "b",
        0.5,
    )


def test_detect_language_empty_allowed_raises(tmp_path):
    engine = we.WhisperEngine(tmp_path, "cpu", "int8", 1, model_factory=DetectModel)
    with pytest.raises(ValueError):
        engine.detect_language(np.zeros(100, dtype=np.float32), [])
