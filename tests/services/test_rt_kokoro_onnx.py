# tests/services/test_rt_kokoro_onnx.py
import json
import types

import numpy as np
import pytest

from src.services.common.device import DeviceError
from src.services.tts import kokoro_onnx as ko
from src.services.tts.g2p import G2PChunk, KokoroG2P, UnknownVoice, lang_code_for_voice

VOCAB = {"$": 0, "h": 1, "ə": 2, "l": 3, "O": 4, " ": 5}


@pytest.fixture
def model_dir(tmp_path):
    (tmp_path / "tokenizer.json").write_text(json.dumps({"model": {"vocab": VOCAB}}))
    voices = tmp_path / "voices"
    voices.mkdir()
    np.arange(510 * 256, dtype=np.float32).tofile(voices / "af_heart.bin")
    np.zeros(510 * 256, dtype=np.float32).tofile(voices / "bf_emma.bin")
    return tmp_path


class FakeSession:
    def __init__(self, path, device, threads):
        self.path, self.device, self.threads, self.feeds = path, device, threads, []

    def run(self, outputs, feeds):
        self.feeds.append(feeds)
        return [np.ones((1, 2400), dtype=np.float32)]


class FakeG2P:
    created = []

    def __init__(self, lang_code):
        FakeG2P.created.append(lang_code)

    def __call__(self, text):
        for word in text.split("|"):
            yield G2PChunk(word, word)


def make(model_dir, device="cpu", compute_type="float32"):
    FakeG2P.created = []
    return ko.KokoroOnnxSynthesizer(
        model_dir,
        device,
        compute_type,
        2,
        session_factory=FakeSession,
        g2p_factory=FakeG2P,
    )


def test_session_gets_file_device_threads(model_dir):
    synth = make(model_dir)
    assert synth._session.path == str(model_dir / "onnx/model.onnx")
    assert (synth._session.device, synth._session.threads) == ("cpu", 2)
    assert make(model_dir, "cpu", "int8")._session.path.endswith(
        "onnx/model_quantized.onnx"
    )
    assert make(model_dir, "cuda", "float16")._session.path.endswith(
        "onnx/model_fp16.onnx"
    )


@pytest.mark.parametrize(
    "device,compute", [("cpu", "float16"), ("cuda", "int8"), ("cpu", "bf16")]
)
def test_invalid_device_compute(model_dir, device, compute):
    with pytest.raises(ValueError):
        make(model_dir, device, compute)


def test_rocm_rejected(model_dir):
    with pytest.raises(DeviceError):
        make(model_dir, "rocm", "float32")


def test_feeds_and_style_index(model_dir):
    synth = make(model_dir)
    chunks = list(synth.synthesize("həl?O", voice="af_heart", speed=1.2))
    assert (
        len(chunks) == 1
        and chunks[0].shape == (2400,)
        and chunks[0].dtype == np.float32
    )
    feeds = synth._session.feeds[0]
    assert feeds["input_ids"].dtype == np.int64
    assert feeds["input_ids"].tolist() == [
        [0, 1, 2, 3, 4, 0]
    ]  # "?" not in vocab -> dropped
    pack = np.arange(510 * 256, dtype=np.float32).reshape(-1, 1, 256)
    assert np.array_equal(feeds["style"], pack[3])  # len(ids) - 1
    assert feeds["speed"].tolist() == pytest.approx([1.2])


def test_chunks_stream_per_g2p_chunk(model_dir):
    synth = make(model_dir)
    assert len(list(synth.synthesize("hə|lO", voice="af_heart"))) == 2


def test_ids_truncated(model_dir):
    synth = make(model_dir)
    list(synth.synthesize("h" * 600, voice="af_heart"))
    assert len(synth._session.feeds[0]["input_ids"][0]) == ko.MAX_PHONEMES + 2


def test_british_voice_uses_lang_b(model_dir):
    synth = make(model_dir)
    list(synth.synthesize("hə", voice="bf_emma"))
    assert FakeG2P.created == ["b"]


def test_empty_text_yields_nothing(model_dir):
    synth = make(model_dir)
    assert list(synth.synthesize("   ", voice="af_heart")) == []
    assert synth._session.feeds == []


def test_unknown_voice(model_dir):
    with pytest.raises(UnknownVoice):
        list(make(model_dir).synthesize("hə", voice="am_adam"))


def test_voice_path_traversal_rejected(model_dir):
    with pytest.raises(UnknownVoice):
        list(make(model_dir).synthesize("hə", voice="../../etc/passwd"))


def test_voices_listed(model_dir):
    assert make(model_dir).voices() == ["af_heart", "bf_emma"]


def test_lang_code_for_voice():
    assert lang_code_for_voice("af_heart") == "a"
    assert lang_code_for_voice("bm_george") == "b"
    for bad in ["jf_alpha", "af", "../x", "AF_heart"]:
        with pytest.raises(UnknownVoice):
            lang_code_for_voice(bad)


def test_kokoro_g2p_skips_empty_phonemes():
    results = [
        types.SimpleNamespace(graphemes="Hi", phonemes="hˈI"),
        types.SimpleNamespace(graphemes="", phonemes=""),
    ]
    g2p = KokoroG2P("a", pipeline_factory=lambda lc: (lambda text: iter(results)))
    assert list(g2p("Hi")) == [G2PChunk("Hi", "hˈI")]
