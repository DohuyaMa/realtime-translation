"""rt-phonetic: settings, HTTP API, CTC decoding and the shared lifecycle
with a fake backend."""

import time

import numpy as np
import pytest

from src.services.common.env import SettingsError
from src.services.phonetic.app import create_app
from src.services.phonetic.backend import FakePhoneticBackend, Word, ctc_words
from src.services.phonetic.settings import DEFAULT_PHONETIC_MODEL, PhoneticSettings
from svc_testutil import running, tone, wav_file

FAKE = {"RT_PHONETIC_BACKEND": "fake"}


def make(backend=None, state_path=None, **env):
    env = {**FAKE, **env}
    settings = PhoneticSettings.from_env(env)
    backend = backend or FakePhoneticBackend(settings.language, settings.model)
    app = create_app(
        settings,
        backend_factory=lambda: backend,
        state_path=state_path,
        bootstrap_env=env,
    )
    return app, backend


def transcribe(client, audio=None, **form):
    audio = tone(16000) if audio is None else audio
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", wav_file(audio), "audio/wav")},
        data=form,
    )


def test_settings_defaults_port_and_language():
    s = PhoneticSettings.from_env(FAKE)
    assert (s.service, s.port, s.device) == ("rt-phonetic", 8092, "cpu")
    assert (s.model, s.language) == (DEFAULT_PHONETIC_MODEL, "uk")
    assert PhoneticSettings.from_env({}).backend == "wav2vec2"


def test_settings_cuda_defaults_to_float16_and_switches_per_device():
    cuda = PhoneticSettings.from_env({**FAKE, "RT_DEVICE": "cuda"})
    assert (cuda.device, cuda.compute_type, cuda.torch_device) == (
        "cuda",
        "float16",
        "cuda",
    )
    f32 = PhoneticSettings.from_env(
        {**FAKE, "RT_DEVICE": "cuda", "RT_COMPUTE_TYPE": "float32"}
    )
    assert f32.compute_type == "float32"
    both = PhoneticSettings.from_env({**FAKE, "RT_DEVICES": "cpu,cuda"})
    assert (both.device, both.devices, both.compute_type) == (
        "cpu",
        ("cpu", "cuda"),
        "float32",
    )


@pytest.mark.parametrize(
    "env,match",
    [
        ({"RT_MODEL": "whisper-small"}, "unknown phonetic model"),
        ({"RT_DEVICE": "rocm"}, "cpu/cuda, not rocm"),
        ({"RT_DEVICES": "cpu,rocm"}, "cpu/cuda, not rocm"),
        ({"RT_COMPUTE_TYPE": "float16"}, "on cpu computes in float32"),
        ({"RT_DEVICE": "cuda", "RT_COMPUTE_TYPE": "int8"}, "float16/float32"),
    ],
)
def test_settings_reject(env, match):
    with pytest.raises(SettingsError, match=match):
        PhoneticSettings.from_env({**FAKE, **env})


# vocab of the shape of the uk model: letters, "|" delimiter, "+" stress,
# [UNK], [PAD] = blank
VOCAB = {0: "[PAD]", 1: "|", 2: "т", 3: "а", 4: "к", 5: "+", 6: "[UNK]", 7: "’"}
CHARS = {"_": 0, " ": 1, "т": 2, "а": 3, "к": 4, "+": 5, "?": 6, "'": 7}


def ids(frames: str) -> list:
    return [CHARS[c] for c in frames]


def test_ctc_collapses_repeats_drops_blanks_and_stress_marks():
    words = ctc_words(ids("__тт_а+а_к__ _т_а___"), VOCAB, 0, 0.02)
    assert [w.word for w in words] == ["таак", "та"]
    # times: first letter frame .. last letter frame + 1, 20 ms frames
    assert words[0] == Word("таак", 0.04, 0.2)
    assert words[1] == Word("та", 0.28, 0.34)


def test_ctc_blank_separates_repeated_letters_and_marks_alone_are_no_word():
    assert [w.word for w in ctc_words(ids("та_а"), VOCAB, 0, 0.02)] == ["таа"]
    assert [w.word for w in ctc_words(ids("таа"), VOCAB, 0, 0.02)] == ["та"]
    # stress marks / unknown tokens / apostrophes alone: heard nothing
    assert ctc_words(ids("__+_?_' _"), VOCAB, 0, 0.02) == []
    assert ctc_words([], VOCAB, 0, 0.02) == []
    # the apostrophe stays inside a word, never at its edges
    assert [w.word for w in ctc_words(ids("'та'к'"), VOCAB, 0, 0.02)] == ["та’к"]


def test_transcription_returns_letters_words_and_duration():
    app, backend = make()
    with running(app) as client:
        r = transcribe(client, language="uk")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "uk" and body["text"] == "звук0 звук1"
    assert body["duration"] == 1.0
    assert body["words"][0] == {"word": "звук0", "start": 0.0, "end": 0.5}
    assert set(body["words"][1]) == {"word", "start", "end"}
    assert backend.calls == [(16000, "uk")]


def test_language_rules():
    """The single loaded model decides; another language is 409, the model
    is never switched by a request."""
    app, backend = make()
    with running(app) as client:
        assert transcribe(client).json()["language"] == "uk"
        assert transcribe(client, language="auto").json()["language"] == "uk"
        assert transcribe(client, language="UK").json()["language"] == "uk"
        r = transcribe(client, language="pl")
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "model_language_mismatch"
        assert DEFAULT_PHONETIC_MODEL in err["message"] and "'pl'" in err["message"]
    assert [lang for _, lang in backend.calls] == ["uk", "uk", "uk"]


def test_silence_gives_empty_text_and_bad_audio_is_400():
    app, _ = make()
    with running(app) as client:
        body = transcribe(client, audio=np.zeros(1600, np.float32), language="uk")
        assert body.json() == {
            "text": "",
            "language": "uk",
            "words": [],
            "duration": 0.1,
        }
        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", b"not audio", "audio/wav")},
            data={"language": "uk"},
        )
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_audio"


def test_upload_limit():
    app, _ = make(RT_PHONETIC_MAX_UPLOAD_MB="1")
    with running(app) as client:
        r = transcribe(client, audio=tone(16000 * 40), language="uk")
    assert r.status_code == 413


def test_auth_token(tmp_path):
    token = tmp_path / "token"
    token.write_text("secret\n")
    app, _ = make(RT_API_TOKEN_FILE=str(token))
    with running(app) as client:
        assert transcribe(client, language="uk").status_code == 401
        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", wav_file(tone(1600)), "audio/wav")},
            data={"language": "uk"},
            headers={"Authorization": "Bearer secret"},
        )
        assert r.status_code == 200
        assert client.get("/health").status_code == 200


def test_models_list_with_language_and_no_cpu_heavy_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("RT_MODEL_DIR", str(tmp_path))
    app, _ = make()
    with running(app) as client:
        body = client.get("/v1/models").json()
    assert body["current"] == DEFAULT_PHONETIC_MODEL
    (model,) = body["models"]
    assert model["id"] == DEFAULT_PHONETIC_MODEL and model["language"] == "uk"
    assert not model["cached"] and "warning" not in model


def test_config_schema_offers_the_nodes_devices():
    app, _ = make()
    with running(app) as client:
        schema = client.get("/config").json()["schema"]
    assert set(schema) == {
        "backend",
        "compute_type",
        "device",
        "log_level",
        "max_upload_mb",
        "model",
    }
    assert schema["device"]["choices"] == ["cpu"]  # this node's RT_DEVICES
    assert schema["device"]["reload"] and schema["compute_type"]["reload"]
    assert schema["model"]["reload"] is True
    assert schema["model"]["type"] == "model"
    assert schema["model"]["choices"] == [DEFAULT_PHONETIC_MODEL]


def test_phonetic_shares_the_model_lifecycle():
    """Explicit unload/load, 503 model_unloaded, reload via /config."""
    env = {**FAKE}
    app = create_app(PhoneticSettings.from_env(env), bootstrap_env=env, state_path=None)
    runtime = app.state.runtime
    with running(app) as client:
        status = client.get("/status").json()
        assert status["service"] == "rt-phonetic"
        assert set(status["model"]) == {"id", "state", "error", "download", "device"}
        assert status["model"]["device"] == "cpu" and "rss_mb" in status["memory"]
        r = client.post("/model/unload")
        assert r.status_code == 200 and r.json()["model"]["state"] == "unloaded"
        r = transcribe(client, language="uk")
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_unloaded"
        assert client.post("/model/load").json()["model"]["state"] == "loading"
        assert runtime.wait_ready(5)
        r = client.post("/config", json={"version": 1, "values": {"backend": "fake"}})
        assert r.status_code == 200
        deadline = time.monotonic() + 5
        while runtime.state.value != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert transcribe(client).json()["language"] == "uk"


def test_device_switch_reloads_and_status_reports_it():
    """cpu -> cuda via POST /config: the model is unloaded, then loaded on
    the new device (fake backend; the real one moves torch to the GPU)."""
    env = {**FAKE, "RT_DEVICES": "cpu,cuda"}
    loaded = []

    def factory():
        loaded.append(app.state.config.current().device)
        return FakePhoneticBackend("uk", DEFAULT_PHONETIC_MODEL)

    app = create_app(
        PhoneticSettings.from_env(env),
        backend_factory=factory,
        bootstrap_env=env,
        state_path=None,
    )
    runtime = app.state.runtime
    with running(app) as client:
        assert transcribe(client).status_code == 200
        assert client.get("/status").json()["model"]["device"] == "cpu"
        r = client.post("/config", json={"version": 1, "values": {"device": "cuda"}})
        assert r.status_code == 200 and r.json()["reloaded"] is True
        deadline = time.monotonic() + 5
        while runtime.state.value != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        status = client.get("/status").json()
        assert status["model"]["device"] == "cuda"
        values = client.get("/config").json()["values"]
        assert (values["device"], values["compute_type"]) == ("cuda", "float16")
    assert loaded == ["cpu", "cuda"]


def test_failed_load_is_reported_in_status():
    app, _ = make(
        backend=FakePhoneticBackend("uk", "x", fail_on_load="no model"),
        RT_IDLE_UNLOAD_MINUTES="0",  # load at start, not on the first request
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while app.state.runtime.state.value != "error":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        model = client.get("/status").json()["model"]
    assert model["state"] == "error" and "no model" in model["error"]
