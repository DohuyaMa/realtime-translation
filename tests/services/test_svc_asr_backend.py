import dataclasses
import subprocess
import sys

import numpy as np
import pytest

from src.services.asr import whisper_engine as we
from src.services.asr.backend import (
    DecodeOptions,
    FakeAsrBackend,
    make_backend,
    normalize_language,
)
from src.services.asr.faster_whisper_backend import FasterWhisperBackend
from src.services.asr.settings import AsrSettings
from src.services.common.env import SettingsError
from src.services.common.settings import render_env_value

SR = 16000


def test_asr_settings_defaults():
    s = AsrSettings.from_env({})
    assert s.languages == ("pl", "uk", "ru", "en")
    assert s.chunk_seconds == 600 and s.max_upload_mb == 100
    assert s.model == "whisper-small" and s.beam_size == 1 and s.language is None


def test_cuda_defaults():
    s = AsrSettings.from_env({"RT_DEVICE": "cuda"})
    assert s.model == "whisper-large-v3-turbo" and s.beam_size == 3
    assert s.compute_type == "int8_float16"


@pytest.mark.parametrize(
    "key,value",
    [
        ("RT_ASR_CHUNK_SECONDS", "10"),
        ("RT_ASR_CHUNK_SECONDS", "901"),
        ("RT_ASR_CHUNK_LOOKBACK_SECONDS", "4"),
        ("RT_ASR_REALTIME_MAX_SECONDS", "121"),
        ("RT_ASR_MAX_UPLOAD_MB", "0"),
        ("RT_ASR_MAX_UPLOAD_MB", "1001"),
        ("RT_ASR_LANGUAGES", "pl,ua"),
        ("RT_ASR_LANGUAGES", ","),
        ("RT_ASR_LANGUAGE", "xx"),
    ],
)
def test_invalid_values_rejected(key, value):
    with pytest.raises(SettingsError):
        AsrSettings.from_env({key: value})


def test_languages_parsed_and_normalized():
    s = AsrSettings.from_env({"RT_ASR_LANGUAGES": " PL, en ,pl"})
    assert s.languages == ("pl", "en")


def test_normalize_language():
    assert normalize_language("auto") is None and normalize_language("") is None
    assert normalize_language(None) is None and normalize_language(" UK ") == "uk"
    with pytest.raises(SettingsError):
        normalize_language("ua")


def test_env_keys_cover_tunables_and_roundtrip():
    keys = AsrSettings.env_keys()
    assert AsrSettings.TUNABLE_FIELDS <= set(keys)
    assert keys["backend"] == "RT_ASR_BACKEND"
    assert "listen_addresses" in AsrSettings.bootstrap_fields()
    assert not AsrSettings.bootstrap_fields() & AsrSettings.TUNABLE_FIELDS


@pytest.mark.parametrize("language", ["pl", None])
def test_tunables_roundtrip_through_rendered_env(language):
    s = dataclasses.replace(
        AsrSettings.from_env({}),
        language=language,
        languages=("en", "uk"),
        beam_size=4,
        vad_filter=False,
        max_upload_mb=50,
        chunk_seconds=120.5,
        chunk_lookback_seconds=12.5,
        realtime_max_seconds=45.0,
        log_level="debug",
    )
    keys = AsrSettings.env_keys()
    env = {keys[f]: render_env_value(getattr(s, f)) for f in AsrSettings.TUNABLE_FIELDS}
    back = AsrSettings.from_env(env)
    for f in AsrSettings.TUNABLE_FIELDS:
        assert getattr(back, f) == getattr(s, f), f


def test_make_backend_selects_fake_and_faster_whisper():
    fake = make_backend(AsrSettings.from_env({"RT_ASR_BACKEND": "fake"}))
    assert isinstance(fake, FakeAsrBackend)
    assert isinstance(make_backend(AsrSettings.from_env({})), FasterWhisperBackend)


def test_fake_transcribe_iter_segments():
    b = FakeAsrBackend(segment_seconds=10.0)
    segs = list(
        b.transcribe_iter(np.zeros(25 * SR, np.float32), DecodeOptions("en", None))
    )
    assert [(s.start, s.end) for s in segs] == [(0, 10), (10, 20), (20, 25)]
    assert segs[2].text == "speech 5.00s"


def test_fake_transcribe_iter_requires_language():
    with pytest.raises(ValueError):
        FakeAsrBackend().transcribe_iter(
            np.zeros(SR, np.float32), DecodeOptions(None, None)
        )


def test_fake_detect_language_respects_allowed():
    a = np.zeros(SR, np.float32)
    assert FakeAsrBackend(detected="ru").detect_language(a, ["pl", "uk"]) == "pl"
    assert FakeAsrBackend().detect_language(a, ["pl", "uk"]) == "uk"


def test_fake_transcribe_defaults():
    r = FakeAsrBackend().transcribe(np.zeros(SR, np.float32), DecodeOptions(None, None))
    assert r.text == "speech 1.00s" and r.language == "uk" and r.duration == 1.0


class _Engine:
    created = None
    last = {}

    @classmethod
    def from_settings(cls, path, device, compute_type, threads):
        cls.created = (path, device, compute_type, threads)
        return cls()

    def close(self):
        self.closed = True

    def transcribe(self, audio, **kw):
        self.last = kw
        return we.TranscriptionResult(
            "hi there", "", [we.Segment(0.0, 1.0, "hi there")], 1.0
        )

    def iter_transcribe(self, audio, **kw):
        self.last = kw
        yield we.Segment(0.0, 1.0, "a")
        yield we.Segment(1.0, 2.0, "b")

    def detect_language(self, audio, allowed):
        return allowed[-1], 0.9


def _backend(env=None):
    prepared = []

    def prepare(value, _settings, progress):
        prepared.append(value)
        return "/models/x"

    b = FasterWhisperBackend(
        AsrSettings.from_env(env or {}), engine_cls=_Engine, prepare=prepare
    )
    b.fetch(None)
    b.load()
    return b, prepared


def test_faster_whisper_load_prepares_model_and_builds_engine():
    _, prepared = _backend()
    assert prepared == ["whisper-small"]
    assert _Engine.created[:3] == ("/models/x", "cpu", "int8")


def test_faster_whisper_transcribe_maps_options():
    b, _ = _backend()
    r = b.transcribe(
        np.zeros(SR, np.float32),
        DecodeOptions(language="en", prompt="ctx", beam_size=2, vad_filter=True),
    )
    kw = b._engine.last
    assert kw["vad"] is we.FILE_VAD and kw["initial_prompt"] == "ctx"
    assert kw["beam_size"] == 2
    assert r.text == "hi there" and r.language == "en" and r.duration == 1.0
    assert [(s.start, s.end, s.text) for s in r.segments] == [(0.0, 1.0, "hi there")]


def test_faster_whisper_iter_and_detect_delegate():
    b, _ = _backend()
    audio = np.zeros(SR, np.float32)
    it = b.transcribe_iter(audio, DecodeOptions("uk", "p", beam_size=3))
    segs = list(it)
    assert [(s.start, s.end, s.text) for s in segs] == [(0, 1, "a"), (1, 2, "b")]
    assert all(type(s).__module__.endswith("asr.backend") for s in segs)
    assert b._engine.last["language"] == "uk" and b._engine.last["vad"] is None
    assert b.detect_language(audio, ["pl", "uk"]) == "uk"
    with pytest.raises(ValueError):
        b.transcribe_iter(audio, DecodeOptions(None, None))


def test_faster_whisper_load_requires_fetch_and_unload_closes_engine():
    b = FasterWhisperBackend(
        AsrSettings.from_env({}), engine_cls=_Engine, prepare=lambda *a: "/m"
    )
    with pytest.raises(RuntimeError, match="before fetch"):
        b.load()
    b.fetch(None)
    b.load()
    engine = b._engine
    b.unload()
    assert engine.closed and b._engine is None
    b.unload()  # idempotent


def test_fake_does_not_import_faster_whisper():
    code = (
        "import sys\n"
        "from src.services.asr.backend import make_backend\n"
        "from src.services.asr.settings import AsrSettings\n"
        "make_backend(AsrSettings.from_env({'RT_ASR_BACKEND': 'fake'}))\n"
        "assert 'faster_whisper' not in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
