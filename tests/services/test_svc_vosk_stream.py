"""Vosk streaming: VoskStreamSession (pure) and WS /v1/audio/stream."""

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from src.services.vosk.app import create_app
from src.services.vosk.backend import FakeVoskBackend
from src.services.vosk.settings import VoskSettings
from src.services.vosk.stream import VoskStreamSession
from svc_testutil import SR, pcm, running, tone

URL = "/v1/audio/stream"
FEED = SR * 2 // 10  # 100 ms of PCM


def speech(seconds):
    return pcm(tone(int(seconds * SR)))


def silence(seconds):
    return bytes(int(seconds * SR) * 2)


def test_session_partials_throttled_finals_with_words_and_endpoint():
    s = VoskStreamSession(FakeVoskBackend("uk"), language="uk", t0=2.0)
    out = []
    for _ in range(10):  # 1.5 s of speech in 150 ms frames
        s.feed(speech(0.15))
        out += s.step()
    partials = [m for m in out if m["type"] == "partial"]
    assert [p["text"] for p in partials] == ["w0", "w0 w1", "w0 w1 w2"]
    assert partials[0]["words"][0]["start"] == pytest.approx(2.0)
    s.feed(silence(0.6))
    out = s.step()
    final, endpoint, idle = out
    assert final["type"] == "final" and final["eos"] is True
    assert final["text"] == "w0 w1 w2" and final["language"] == "uk"
    assert final["words"][-1] == {"word": "w2", "start": 3.0, "end": 3.5, "conf": 0.9}
    assert (final["start"], final["end"]) == (2.0, 3.5)
    assert endpoint == {"type": "endpoint", "at": 3.5}
    assert idle == {"type": "idle", "at": pytest.approx(4.1), "silent_from": 3.5}
    assert s.audio_end == pytest.approx(2.0 + 1.5 + 0.6)


def test_partial_throttle_is_300ms_of_audio():
    class Rec:
        def __init__(self):
            self.n = 0

        def AcceptWaveform(self, data):
            return False

        def PartialResult(self):
            self.n += 1
            return json.dumps({"partial": f"p{self.n}", "partial_result": []})

    backend = FakeVoskBackend("uk")
    backend.recognizer = Rec
    s = VoskStreamSession(backend, language="uk")
    sent = []
    for _ in range(10):
        s.feed(speech(0.1))
        sent += [m for m in s.step() if m["type"] == "partial"]
    # 1.0 s of audio: partials at 0.1, 0.4, 0.7, 1.0
    assert len(sent) == 4


def test_idle_while_kaldi_has_no_word_at_the_partial_pace():
    s = VoskStreamSession(FakeVoskBackend("uk"), language="uk", t0=1.0)
    out = []
    for _ in range(10):  # 1.0 s of silence in 100 ms frames
        s.feed(silence(0.1))
        out += s.step()
    assert [m["type"] for m in out] == ["idle"] * 4  # 0.1, 0.4, 0.7, 1.0 s
    assert [m["at"] for m in out] == pytest.approx([1.1, 1.4, 1.7, 2.0])
    assert {m["silent_from"] for m in out} == {1.0}


class DeafRec:
    """Kaldi that decodes everything as silence (the small uk model on
    «повільніше», live §16): never a word, never a partial."""

    def AcceptWaveform(self, data):
        return False

    def PartialResult(self):
        return json.dumps({"partial": ""})

    def FinalResult(self):
        return json.dumps({"text": ""})


def feed_steps(s, audio):
    out = []
    for i in range(0, len(audio), FEED):
        s.feed(audio[i : i + FEED])
        out += s.step()
    return [m for m in out if m["type"] == "idle"]


def test_idle_never_claims_voiced_audio_kaldi_heard_no_word_in():
    """§16: «повільніше» (voiced 166.5-167.3, Kaldi empty), then a pause -
    the idle's silence starts after the voiced audio, never before it."""
    backend = FakeVoskBackend("uk")
    backend.recognizer = DeafRec
    s = VoskStreamSession(backend, language="uk", t0=166.0)
    before = feed_steps(s, silence(0.5))
    voiced = feed_steps(s, speech(0.8))
    pause = feed_steps(s, silence(1.5))
    assert {m["silent_from"] for m in before} == {166.0}
    assert all(m["silent_from"] == pytest.approx(m["at"]) for m in voiced)
    assert pause and {m["silent_from"] for m in pause} == {167.3}


def test_steady_room_noise_is_learned_as_the_floor_not_voice():
    """Amplified room noise (edge gain, -43 dBFS) counts as voice only until
    the NOISE_WINDOW_S window knows it; speech above it is voice again."""
    backend = FakeVoskBackend("uk")
    backend.recognizer = DeafRec
    s = VoskStreamSession(backend, language="uk")
    noise = pcm(tone(int(7.0 * SR), amp=0.01))
    idles = feed_steps(s, noise)
    assert idles[-1]["silent_from"] == pytest.approx(5.0, abs=0.11)
    feed_steps(s, speech(0.5))  # 7.0-7.5 s
    idles = feed_steps(s, noise[: 5 * FEED])
    assert {m["silent_from"] for m in idles} == {7.5}


def test_finish_flushes_the_open_utterance():
    s = VoskStreamSession(FakeVoskBackend("uk"), language="uk")
    s.feed(speech(1.0))
    (final,) = s.finish()
    assert final["text"] == "w0 w1" and final["eos"] is True
    assert s.finish() == []


def make(**env):
    env = {"RT_VOSK_BACKEND": "fake", **env}
    settings = VoskSettings.from_env(env)
    backend = FakeVoskBackend(settings.language, settings.model)
    app = create_app(
        settings, backend_factory=lambda: backend, state_path=None, bootstrap_env=env
    )
    return app, backend


def test_ws_stream_same_protocol_as_asr():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ws.send_json(
                {"type": "start", "language": "uk", "sample_rate": SR, "t0": 1.0}
            )
            ready = ws.receive_json()
            assert (
                ready["type"] == "ready" and ready["model"] == "vosk-small-uk-v3-nano"
            )
            ws.send_bytes(speech(1.0))
            ws.send_bytes(silence(0.6))
            msgs = []
            while not msgs or msgs[-1]["type"] != "endpoint":
                msgs.append(ws.receive_json())
            final = [m for m in msgs if m["type"] == "final"][-1]
            assert final["text"] == "w0 w1" and final["start"] == 1.0
            assert all("audio_end" in m for m in msgs)
            assert client.get("/status").json()["sessions"]["active"] == 1
            ws.send_json({"type": "stop"})
            while ws.receive_json()["type"] != "done":
                pass
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1000


def test_ws_other_language_is_refused():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ws.send_json({"type": "start", "language": "pl"})
            err = ws.receive_json()
            assert err["code"] == "model_language_mismatch"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1003


def test_ws_unload_closes_1012():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ws.send_json({"type": "start"})
            assert ws.receive_json()["type"] == "ready"
            client.post("/model/unload")
            assert ws.receive_json()["code"] == "model_unloaded"
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == 1012
