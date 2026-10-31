"""rt-vosk: settings, HTTP API and the shared lifecycle with a fake backend."""

import time

import numpy as np
import pytest

from src.services.common.env import SettingsError
from src.services.vosk.app import create_app
from src.services.vosk.backend import FakeVoskBackend
from src.services.vosk.settings import DEFAULT_VOSK_MODELS, VoskSettings
from svc_testutil import running, tone, wav_file

FAKE = {"RT_VOSK_BACKEND": "fake"}


def make(backend=None, state_path=None, **env):
    env = {**FAKE, **env}
    settings = VoskSettings.from_env(env)
    backend = backend or FakeVoskBackend(settings.languages)
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


def test_settings_defaults_port_and_languages():
    s = VoskSettings.from_env(FAKE)
    assert (s.service, s.port, s.device) == ("rt-vosk", 8092, "cpu")
    assert s.models == DEFAULT_VOSK_MODELS
    assert s.languages == {"uk": "vosk-small-uk-v3-nano", "pl": "vosk-small-pl-0.22"}


@pytest.mark.parametrize(
    "env,match",
    [
        ({"RT_MODEL": "whisper-small"}, "unknown Vosk model"),
        (
            {"RT_MODEL": "vosk-small-uk-v3-nano,vosk-small-uk-v3-small"},
            "one model per language",
        ),
        ({"RT_MODEL": " , "}, "at least one"),
        ({"RT_DEVICE": "cuda"}, "cpu only"),
        ({"RT_DEVICES": "cpu,cuda"}, "cpu only"),
        ({"RT_COMPUTE_TYPE": "int8"}, "float32 only"),
    ],
)
def test_settings_reject(env, match):
    with pytest.raises(SettingsError, match=match):
        VoskSettings.from_env({**FAKE, **env})


def test_transcription_returns_words_with_confidence():
    app, backend = make()
    with running(app) as client:
        r = transcribe(client, language="pl")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "pl" and body["text"] == "fake-pl words"
    assert body["words"][0] == {
        "word": "fake-pl",
        "conf": 0.9,
        "start": 0.0,
        "end": 0.5,
    }
    assert set(body["words"][1]) == {"word", "conf", "start", "end"}
    assert backend.calls == [(16000, "pl")]


def test_language_rules():
    app, _ = make()
    with running(app) as client:
        r = transcribe(client)
        assert r.status_code == 400 and "language is required" in r.text
        r = transcribe(client, language="auto")
        assert r.status_code == 400
        r = transcribe(client, language="en")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "unsupported_language"
        assert transcribe(client, language="UK").json()["language"] == "uk"
    single, _ = make(RT_MODEL="vosk-small-pl-0.22")
    with running(single) as client:
        assert transcribe(client).json()["language"] == "pl"  # the only model


def test_silence_gives_empty_text_and_bad_audio_is_400():
    app, _ = make()
    with running(app) as client:
        body = transcribe(client, audio=np.zeros(1600, np.float32), language="uk")
        assert body.json() == {"text": "", "language": "uk", "words": []}
        r = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("a.wav", b"not audio", "audio/wav")},
            data={"language": "uk"},
        )
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_audio"


def test_upload_limit():
    app, _ = make(RT_VOSK_MAX_UPLOAD_MB="1")
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


def test_models_list_vosk_registry_with_languages(tmp_path, monkeypatch):
    monkeypatch.setenv("RT_MODEL_DIR", str(tmp_path))
    app, _ = make()
    with running(app) as client:
        body = client.get("/v1/models").json()
    assert body["current"] == ",".join(DEFAULT_VOSK_MODELS)
    langs = {m["id"]: m["language"] for m in body["models"]}
    assert langs["vosk-small-pl-0.22"] == "pl"
    assert langs["vosk-small-uk-v3-small"] == "uk"
    assert not any(m["cached"] for m in body["models"])


def test_config_schema_has_no_device_switch():
    app, _ = make()
    with running(app) as client:
        schema = client.get("/config").json()["schema"]
    assert set(schema) == {"backend", "log_level", "max_upload_mb", "model"}
    assert schema["model"]["reload"] is True


def test_vosk_shares_the_model_lifecycle():
    """Explicit unload/load, 503 model_unloaded, model change via /config."""
    env = {**FAKE}
    app = create_app(VoskSettings.from_env(env), bootstrap_env=env, state_path=None)
    runtime = app.state.runtime
    with running(app) as client:
        status = client.get("/status").json()
        assert status["service"] == "rt-vosk"
        assert set(status["model"]) == {"id", "state", "error", "download", "device"}
        assert status["model"]["device"] == "cpu" and "rss_mb" in status["memory"]
        r = client.post("/model/unload")
        assert r.status_code == 200 and r.json()["model"]["state"] == "unloaded"
        r = transcribe(client, language="uk")
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_unloaded"
        assert client.post("/model/load").json()["model"]["state"] == "loading"
        assert runtime.wait_ready(5)
        r = client.post(
            "/config",
            json={"version": 1, "values": {"model": "vosk-small-uk-v3-small"}},
        )
        assert r.status_code == 200 and r.json()["reloaded"] is True
        deadline = time.monotonic() + 5
        while runtime.state.value != "ready":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert runtime.backend.languages() == ("uk",)
        assert client.get("/status").json()["model"]["id"] == "vosk-small-uk-v3-small"
        assert transcribe(client).json()["language"] == "uk"


def test_failed_load_is_reported_in_status():
    app, _ = make(
        backend=FakeVoskBackend({"uk": "x"}, fail_on_load="no model"),
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
