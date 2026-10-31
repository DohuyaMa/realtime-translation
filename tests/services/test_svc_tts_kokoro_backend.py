import dataclasses
import struct

import numpy as np
import pytest

from src.services.common.env import SettingsError
from src.services.common.settings import render_env_value
from src.services.tts.backend import (
    FakeTtsBackend,
    UnsupportedVoiceLanguage,
    make_backend,
)
from src.services.tts.encoding import pcm_bytes, wav_bytes, wav_header
from src.services.tts.kokoro_backend import KokoroBackend
from src.services.tts.settings import TtsSettings
from src.services.tts.voices import KOKORO_VOICES, LANGUAGE_BY_PREFIX, catalog


class FakeSynth:
    sample_rate = 24000

    def __init__(self, voices=("af_heart", "bf_emma", "jf_alpha", "zz_unknown")):
        self._voices = list(voices)
        self.calls = []

    def voices(self):
        return self._voices

    def synthesize(self, text, voice="af_heart", speed=1.0):
        self.calls.append((text, voice, speed))
        yield np.ones(10, np.float32)


def _backend(settings=None, synth=None):
    synth = synth or FakeSynth()
    seen = {}

    def factory(engine, path, device, compute, threads):
        seen["args"] = (engine, path, device, compute, threads)
        return synth

    def prepare(model, model_settings, progress):
        seen["model"] = model
        return "/models/x"

    b = KokoroBackend(
        settings or TtsSettings.from_env({}), synth_factory=factory, prepare=prepare
    )
    return b, synth, seen


def test_catalog_is_english_only():
    assert len(KOKORO_VOICES) == 28 and all(v[0] in "ab" for v in KOKORO_VOICES)
    assert LANGUAGE_BY_PREFIX == {"a": "en-us", "b": "en-gb"}
    assert len(catalog()) == 28


def test_tts_rocm_rejected():
    with pytest.raises(SettingsError):
        TtsSettings.from_env({"RT_DEVICE": "rocm"})


def test_tts_engine_selection():
    s = TtsSettings.from_env({})
    assert s.engine == "onnx" and s.model == "kokoro-82m-v1.0-onnx"
    assert s.voice == "af_heart" and s.port == 8093
    with pytest.raises(SettingsError):
        TtsSettings.from_env({"RT_TTS_ENGINE": "torch"})


def test_unknown_or_non_english_voice_rejected():
    with pytest.raises(SettingsError):
        TtsSettings.from_env({"RT_TTS_VOICE": "jf_alpha"})


def test_env_keys_cover_fields():
    keys = TtsSettings.env_keys()
    assert keys["backend"] == "RT_TTS_BACKEND" and keys["engine"] == "RT_TTS_ENGINE"
    assert TtsSettings.TUNABLE_FIELDS == {
        "log_level",
        "voice",
        "speed",
        "sample_rate",
        "max_chars",
    }
    assert "engine" in TtsSettings.RELOAD_FIELDS
    assert {"listen_addresses", "port"} <= TtsSettings.bootstrap_fields()
    assert not TtsSettings.bootstrap_fields() & TtsSettings.TUNABLE_FIELDS


def test_tunables_roundtrip_through_rendered_env():
    s = dataclasses.replace(
        TtsSettings.from_env({}),
        voice="bm_george",
        speed=1.5,
        sample_rate=16000,
        max_chars=100,
        log_level="debug",
    )
    keys = TtsSettings.env_keys()
    env = {keys[f]: render_env_value(getattr(s, f)) for f in TtsSettings.TUNABLE_FIELDS}
    back = TtsSettings.from_env(env)
    for f in TtsSettings.TUNABLE_FIELDS:
        assert getattr(back, f) == getattr(s, f), f


def test_make_backend():
    fake = make_backend(TtsSettings.from_env({"RT_TTS_BACKEND": "fake"}))
    assert isinstance(fake, FakeTtsBackend)
    assert isinstance(make_backend(TtsSettings.from_env({})), KokoroBackend)


def test_kokoro_backend_delegates_to_synthesizer():
    b, synth, seen = _backend()
    b.fetch(None)
    b.load()
    assert seen["model"] == "kokoro-82m-v1.0-onnx"
    assert seen["args"][:4] == ("onnx", "/models/x", "cpu", "float32")
    assert b.sample_rate == 24000
    chunks = list(b.synthesize("Hi.", "af_heart", 1.2))
    assert len(chunks) == 1 and synth.calls == [("Hi.", "af_heart", 1.2)]


def test_kokoro_backend_voices_intersect_catalog():
    b, _, _ = _backend()
    b.fetch(None)
    b.load()
    assert [v.id for v in b.voices()] == ["af_heart", "bf_emma"]


def test_kokoro_backend_unsupported_language():
    b, _, _ = _backend()
    b.fetch(None)
    b.load()
    b.prepare_voice("bf_emma")
    with pytest.raises(UnsupportedVoiceLanguage):
        b.prepare_voice("zf_xiaobei")


def test_kokoro_backend_unload_drops_synthesizer():
    b, _, _ = _backend()
    b.fetch(None)
    b.load()
    b.unload()
    assert b._synth is None


def test_fake_backend_synthesizes_and_rejects_prefix():
    b = FakeTtsBackend(unsupported_prefixes=("b",))
    b.load()
    chunks = list(b.synthesize("One. Two.", "af_heart", 1.0))
    assert len(chunks) == 2 and chunks[0].dtype == np.float32
    assert b.calls == [("One. Two.", "af_heart", 1.0)]
    with pytest.raises(UnsupportedVoiceLanguage):
        b.prepare_voice("bf_emma")


def test_wav_encoding():
    pcm = pcm_bytes(np.array([0.0, 1.0, -1.0, 2.0], np.float32))
    assert struct.unpack("<4h", pcm) == (0, 32767, -32767, 32767)
    wav = wav_bytes(pcm, 24000)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE" and len(wav) == 44 + 8
    assert struct.unpack("<I", wav[4:8])[0] == 36 + 8
    assert wav[44:] == pcm
    stream = wav_header(24000, None)
    assert struct.unpack("<I", stream[4:8])[0] == 0xFFFFFFFF
    assert struct.unpack("<I", stream[40:44])[0] == 0xFFFFFFFF
