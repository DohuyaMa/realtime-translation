"""WS /v1/audio/stream on rt-asr (fake backend, EnergyVad)."""

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.services.asr.app import create_app
from src.services.asr.backend import FakeAsrBackend
from src.services.asr.settings import AsrSettings
from svc_testutil import SR, pcm, running, tone

URL = "/v1/audio/stream"


def make(backend=None, token_file=None, **env):
    env = {"RT_ASR_BACKEND": "fake", "RT_ASR_STREAM_TICK_MS": "100", **env}
    if token_file is not None:
        env["RT_API_TOKEN_FILE"] = str(token_file)
    settings = AsrSettings.from_env(env)
    backend = backend or FakeAsrBackend()
    app = create_app(
        settings, backend_factory=lambda: backend, state_path=None, bootstrap_env=env
    )
    return app, backend


def speech(seconds):
    return pcm(tone(int(seconds * SR)))


def silence(seconds):
    return bytes(int(seconds * SR) * 2)


def start(ws, **kw):
    ws.send_json({"type": "start", "language": "en", "sample_rate": SR, **kw})
    return ws.receive_json()


def close_code(ws):
    with pytest.raises(WebSocketDisconnect) as exc:
        for _ in range(100):
            ws.receive_json()
    return exc.value.code


def collect_until(ws, kind, limit=200):
    out = []
    for _ in range(limit):
        msg = ws.receive_json()
        out.append(msg)
        if msg["type"] == kind:
            return out
    raise AssertionError(f"no {kind}: {out}")


def test_stream_ready_finals_endpoint_stop_done():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ready = start(ws, t0=5.0)
            assert ready["type"] == "ready" and ready["model"] == "fake"
            assert ready["session_id"] and ready["audio_end"] == 5.0
            for _ in range(12):  # 2.4 s of speech, paced like a live mic
                ws.send_bytes(speech(0.2))
                time.sleep(0.02)
            ws.send_bytes(silence(0.7))
            msgs = collect_until(ws, "endpoint")
            finals = [m for m in msgs if m["type"] == "final"]
            assert finals and finals[-1]["eos"] is True
            assert all(m["start"] >= 5.0 and "audio_end" in m for m in finals)
            assert msgs[-1]["at"] == pytest.approx(7.4, abs=0.05)
            assert [m["seq"] for m in finals] == list(range(1, len(finals) + 1))
            status = client.get("/status").json()["sessions"]
            assert status["active"] == 1 and status["max"] == 4
            assert status["open"][0]["ticks"] >= 1
            ws.send_bytes(speech(0.6))
            ws.send_json({"type": "stop"})
            msgs = collect_until(ws, "done")
            assert msgs[-1]["audio_end"] == pytest.approx(5.0 + 3.1 + 0.6, abs=0.01)
            assert close_code(ws) == 1000
        assert client.get("/status").json()["sessions"]["active"] == 0


def test_bad_token_closes_1008_before_accept(tmp_path):
    token = tmp_path / "tok"
    token.write_text("secret")
    app, _ = make(token_file=token)
    with running(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(URL):
                pass
        assert exc.value.code == 1008
        headers = {"Authorization": "Bearer secret"}
        with client.websocket_connect(URL, headers=headers) as ws:
            assert start(ws)["type"] == "ready"


def test_session_limit_closes_1013():
    app, _ = make(RT_ASR_MAX_SESSIONS="1")
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            assert start(ws)["type"] == "ready"
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(URL):
                    pass
            assert exc.value.code == 1013


@pytest.mark.parametrize(
    "first, code",
    [
        ({"type": "start", "sample_rate": 8000}, "unsupported_sample_rate"),
        ({"type": "hello"}, "protocol_error"),
        ({"type": "start", "language": "xx"}, "invalid_request"),
    ],
)
def test_bad_start_is_error_and_1003(first, code):
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ws.send_json(first)
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == code
            assert err["state"] == "ready"
            assert close_code(ws) == 1003


def test_binary_before_start_is_protocol_error():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            ws.send_bytes(silence(0.1))
            assert ws.receive_json()["code"] == "protocol_error"
            assert close_code(ws) == 1003


def test_unloaded_model_is_model_not_ready_1013():
    app, _ = make()
    with running(app) as client:
        assert client.post("/model/unload").status_code == 200
        with client.websocket_connect(URL) as ws:
            ws.send_json({"type": "start", "language": "en"})
            err = ws.receive_json()
            assert err["code"] == "model_not_ready" and err["state"] == "unloaded"
            assert close_code(ws) == 1013


def test_lazy_load_on_handshake():
    app, _ = make(RT_IDLE_UNLOAD_MINUTES="5")
    with TestClient(app) as client:
        assert app.state.runtime.state.value == "unloaded"
        with client.websocket_connect(URL) as ws:
            assert start(ws)["type"] == "ready"
            assert app.state.runtime.state.value == "ready"


def test_unload_during_session_is_model_unloaded_1012():
    app, _ = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            assert start(ws)["type"] == "ready"
            assert client.post("/model/unload").status_code == 200
            err = ws.receive_json()
            assert err["type"] == "error" and err["code"] == "model_unloaded"
            assert close_code(ws) == 1012


def test_open_session_blocks_idle_unload():
    app, _ = make(RT_IDLE_UNLOAD_MINUTES="5")
    runtime = app.state.runtime
    with TestClient(app) as client:
        with client.websocket_connect(URL) as ws:
            assert start(ws)["type"] == "ready"
            runtime.last_used -= 3600
            assert runtime.check_idle() is False
        deadline = time.monotonic() + 2
        while runtime.holds and time.monotonic() < deadline:
            time.sleep(0.01)
        runtime.last_used -= 3600
        assert runtime.check_idle() is True


def test_warm_up_decode_after_ready():
    app, backend = make()
    with running(app) as client:
        with client.websocket_connect(URL) as ws:
            start(ws)
            ws.send_json({"type": "stop"})
            assert collect_until(ws, "done")[-1]["type"] == "done"
    assert backend.calls[0][0] == SR
