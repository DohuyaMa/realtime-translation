import struct
import subprocess
import sys
from pathlib import Path

import pytest

from src.services.common.env import SettingsError
from src.services.tts.app import create_app
from src.services.tts.backend import FakeTtsBackend
from src.services.tts.encoding import wav_bytes, wav_header
from src.services.tts.settings import TtsSettings
from src.services.tts.voices import KOKORO_VOICES, catalog
from svc_testutil import running

ROOT = Path(__file__).resolve().parents[2]


def make(backend=None, state_path=None, **env):
    settings = TtsSettings.from_env({"RT_TTS_BACKEND": "fake", **env})
    backend = backend or FakeTtsBackend()
    app = create_app(
        settings,
        backend_factory=lambda: backend,
        state_path=state_path,
        bootstrap_env={"RT_TTS_BACKEND": "fake", **env},
    )
    return app, backend


def parse_wav(data):
    fields = struct.unpack("<4sI4s4sIHHIIHH4sI", data[:44])
    riff, riff_size, wave, fmt, _, pcm_fmt, channels, rate, byte_rate = fields[:9]
    align, bits, data_id, size = fields[9:]
    assert (riff, wave, fmt, data_id) == (b"RIFF", b"WAVE", b"fmt ", b"data")
    assert (pcm_fmt, channels, bits, align, byte_rate) == (1, 1, 16, 2, rate * 2)
    return rate, riff_size, size, data[44:]


def speech(client, **body):
    return client.post("/v1/audio/speech", json=body)


def test_wav_header_complete_and_streaming():
    rate, riff_size, size, pcm = parse_wav(wav_bytes(b"\x01\x00" * 10, 24000))
    assert (rate, riff_size, size, len(pcm)) == (24000, 56, 20, 20)
    _, riff_size, size, _ = parse_wav(wav_header(16000, None))
    assert riff_size == size == 0xFFFFFFFF


def test_voice_catalog():
    assert len(KOKORO_VOICES) == 28 and len(set(KOKORO_VOICES)) == 28
    heart = next(v for v in catalog() if v.id == "af_heart")
    assert (heart.language, heart.gender) == ("en-us", "female")
    with pytest.raises(SettingsError):
        TtsSettings.from_env({"RT_TTS_VOICE": "nobody"})


def test_wav_response_with_defaults():
    app, backend = make()
    with running(app) as client:
        r = speech(client, input="Hello there. Bye!")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.headers["x-sample-rate"] == "24000"
    rate, _, size, pcm = parse_wav(r.content)
    assert rate == 24000 and size == len(pcm) > 0
    assert backend.calls == [("Hello there. Bye!", "af_heart", 1.0)]


def test_pcm_response_resampled():
    app, _ = make(RT_TTS_SAMPLE_RATE="16000")
    with running(app) as client:
        r = speech(client, input="abcd", response_format="pcm", speed=2.0)
    assert r.headers["content-type"] == "audio/pcm"
    assert r.headers["x-sample-rate"] == "16000"
    # fake: 0.05 s * 4 chars / speed 2 = 0.1 s -> 1600 samples at 16 kHz
    assert len(r.content) == 1600 * 2


def test_streaming_wav():
    app, _ = make()
    with running(app) as client:
        with client.stream(
            "POST",
            "/v1/audio/speech",
            json={"input": "One. Two. Three.", "stream": True},
        ) as r:
            body = b"".join(r.iter_bytes())
    _, riff_size, _, pcm = parse_wav(body)
    assert riff_size == 0xFFFFFFFF and len(pcm) > 0


def test_streaming_pcm_equals_non_streaming():
    app, _ = make()
    with running(app) as client:
        full = speech(client, input="One. Two.", response_format="pcm").content
        streamed = speech(
            client, input="One. Two.", response_format="pcm", stream=True
        ).content
    assert streamed == full


def test_voices_are_per_request():
    app, backend = make()
    with running(app) as client:
        a = speech(client, input="same", voice="af_heart", response_format="pcm")
        b = speech(client, input="same", voice="am_adam", response_format="pcm")
        c = speech(client, input="same", response_format="pcm")
    assert a.content != b.content and a.content == c.content
    assert [v for _, v, _ in backend.calls] == ["af_heart", "am_adam", "af_heart"]


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({"input": "   "}, 400, "invalid_request"),
        ({"input": "x" * 11}, 413, "payload_too_large"),
        ({"input": "x", "voice": "nobody"}, 400, "unknown_voice"),
        ({"input": "x", "voice": "jf_alpha"}, 400, "unknown_voice"),
        ({"input": "x", "response_format": "mp3"}, 400, "unsupported_format"),
        ({"input": "x", "voice": "bf_emma"}, 400, "unsupported_voice_language"),
    ],
)
def test_validation_errors(body, status, code):
    app, _ = make(FakeTtsBackend(unsupported_prefixes=("b",)), RT_TTS_MAX_CHARS="10")
    with running(app) as client:
        r = speech(client, **body)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_unsupported_voice_language_also_for_stream():
    app, _ = make(FakeTtsBackend(unsupported_prefixes=("b",)))
    with running(app) as client:
        r = speech(client, input="x", voice="bf_emma", stream=True)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_voice_language"


def test_speed_bounds():
    app, _ = make()
    with running(app) as client:
        assert speech(client, input="x", speed=5).status_code == 422


def test_voices_endpoint():
    app, _ = make(RT_TTS_VOICE="bf_emma")
    with running(app) as client:
        body = client.get("/v1/audio/voices").json()
    assert body["default"] == "bf_emma" and len(body["voices"]) == 28
    assert {"id": "am_adam", "language": "en-us", "gender": "male"} in body["voices"]


def test_config_voice_default_applies_without_restart(tmp_path):
    app, backend = make(state_path=tmp_path / "state.json")
    with running(app) as client:
        r = client.post("/config", json={"version": 1, "values": {"voice": "am_adam"}})
        assert r.status_code == 200, r.text
        assert speech(client, input="hi").status_code == 200
        assert client.get("/v1/audio/voices").json()["default"] == "am_adam"
    assert [v for _, v, _ in backend.calls] == ["am_adam"]


def test_tts_service_never_imports_sounddevice():
    code = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from src.services.tts.app import create_app\n"
        "from src.services.tts.settings import TtsSettings\n"
        "env = {'RT_TTS_BACKEND': 'fake'}\n"
        "app = create_app(TtsSettings.from_env(env), bootstrap_env=env)\n"
        "with TestClient(app) as c:\n"
        "    app.state.runtime.wait_ready(5)\n"
        "    r = c.post('/v1/audio/speech', json={'input': 'hi'})\n"
        "    assert r.status_code == 200\n"
        "print('sounddevice' in sys.modules)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_empty_synthesis_returns_valid_empty_wav():
    class Silent(FakeTtsBackend):
        def synthesize(self, text, voice, speed):
            return iter(())

    app, _ = make(Silent())
    with running(app) as client:
        r = speech(client, input="Hello")
    assert r.status_code == 200
    _, riff_size, size, pcm = parse_wav(r.content)
    assert (riff_size, size, pcm) == (36, 0, b"")


def test_sample_rate_differs_from_backend_native_rate_is_resampled():
    backend = FakeTtsBackend()
    backend.sample_rate = 48000
    app, _ = make(backend, RT_TTS_SAMPLE_RATE="16000")
    with running(app) as client:
        r = speech(client, input="abcd", response_format="pcm", speed=2.0)
    assert r.headers["x-sample-rate"] == "16000"
    # 0.1 s of audio at the native 48 kHz -> 1600 samples at 16 kHz
    assert len(r.content) == 1600 * 2


def test_config_bootstrap_reflects_only_given_env(tmp_path):
    app, _ = make(state_path=tmp_path / "state.json", RT_TTS_SPEED="1.5")
    with running(app) as client:
        client.post("/config", json={"version": 1, "values": {"voice": "am_adam"}})
        body = client.get("/config").json()
    assert body["values"]["voice"] == "am_adam"
    assert body["bootstrap"]["speed"] == 1.5
    assert body["bootstrap"]["voice"] == "af_heart"  # not in env -> default
    assert body["source"]["voice"] == "override"
