import asyncio
import json
import threading
import time

import httpx
import pytest

from src.services.asr.app import create_app
from src.services.asr.backend import FakeAsrBackend, Segment, Transcription
from src.services.asr.settings import AsrSettings
from src.services.common.device import default_beams, default_model
from src.services.common.worker import Priority
from svc_testutil import SR, running, tone, wav_file


def make(backend=None, state_path=None, **env):
    env = {"RT_ASR_BACKEND": "fake", **env}
    settings = AsrSettings.from_env(env)
    backend = backend or FakeAsrBackend()
    app = create_app(
        settings,
        backend_factory=lambda: backend,
        state_path=state_path,
        bootstrap_env=env,
    )
    return app, backend


def post(client, data=None, **form):
    data = wav_file(tone(16000)) if data is None else data
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", data, "audio/wav")},
        data=form,
    )


def set_config(client, version=1, **values):
    r = client.post("/config", json={"version": version, "values": values})
    assert r.status_code == 200, r.text
    return r.json()


def spy_methods(backend):
    """Record which backend entry points the handler used."""
    used = []
    transcribe, transcribe_iter = backend.transcribe, backend.transcribe_iter

    def spy_transcribe(audio, opts):
        used.append("transcribe")
        return transcribe(audio, opts)

    def spy_iter(audio, opts):
        used.append("transcribe_iter")
        return transcribe_iter(audio, opts)

    backend.transcribe, backend.transcribe_iter = spy_transcribe, spy_iter
    return used


# --- base behaviour ----------------------------------------------------------


def test_json_response_and_options_reach_backend():
    app, backend = make(RT_ASR_LANGUAGE="uk", RT_ASR_BEAM_SIZE="3")
    with running(app) as client:
        r = post(client, prompt="context", temperature="0.2")
    assert r.status_code == 200
    assert r.json() == {"text": "speech 1.00s", "language": "uk"}
    n, opts = backend.calls[0]
    assert n == 16000
    assert (
        opts.language,
        opts.prompt,
        opts.temperature,
        opts.beam_size,
        opts.vad_filter,
    ) == ("uk", "context", 0.2, 3, True)


def test_request_language_overrides_default_and_auto_means_detect():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    with running(app) as client:
        post(client, language="pl")
        post(client, language="auto")
    assert backend.calls[0][1].language == "pl"
    assert backend.calls[1][1].language == "uk"


def test_explicit_language_outside_allowed_list_wins():
    app, backend = make(RT_ASR_LANGUAGES="pl,uk")
    with running(app) as client:
        r = post(client, language="de")
    assert r.json()["language"] == "de"
    assert backend.calls[0][1].language == "de"


def test_verbose_json_and_text_and_repetition_filter():
    backend = FakeAsrBackend()
    backend.transcribe = lambda audio, opts: Transcription(
        "hello hello hello", "en", 1.0, [Segment(0.0, 1.0, "hello hello hello")]
    )
    app, _ = make(backend)
    with running(app) as client:
        verbose = post(client, response_format="verbose_json").json()
        text = post(client, response_format="text")
    assert verbose == {
        "text": "hello",
        "language": "en",
        "duration": 1.0,
        "segments": [{"id": 0, "start": 0.0, "end": 1.0, "text": "hello"}],
        "chunks": 1,
    }
    assert text.headers["content-type"].startswith("text/plain")
    assert text.text == "hello"


@pytest.mark.parametrize(
    "data,code,status",
    [
        (b"garbage", "invalid_audio", 400),
        (b"", "invalid_audio", 400),
    ],
)
def test_invalid_audio(data, code, status):
    app, _ = make()
    with running(app) as client:
        r = post(client, data=data)
    assert r.status_code == status and r.json()["error"]["code"] == code


def test_unsupported_format_and_upload_limit():
    app, _ = make(RT_ASR_MAX_UPLOAD_MB="1")
    with running(app) as client:
        r = post(client, response_format="srt")
        assert r.json()["error"]["code"] == "unsupported_format"
        big = post(client, data=b"\0" * (1024 * 1024 + 10))
    assert big.status_code == 413


def test_auth_applies_to_transcriptions(tmp_path):
    token = tmp_path / "api-token"
    token.write_text("t0k")
    app, _ = make(RT_API_TOKEN_FILE=str(token))
    with running(app) as client:
        assert post(client).status_code == 401
        ok = client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": "Bearer t0k"},
            files={"file": ("a.wav", wav_file(tone(1600)), "audio/wav")},
        )
        assert ok.status_code == 200
        assert client.get("/status").status_code == 200


# --- priority, chunking, runtime config --------------------------------------


def test_auto_priority_short_is_realtime():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    used = spy_methods(backend)
    with running(app) as client:
        r = post(client, data=wav_file(tone(5 * SR)), response_format="verbose_json")
    assert r.status_code == 200 and r.json()["chunks"] == 1
    assert used == ["transcribe"]


def test_auto_priority_long_is_batch_and_chunked():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    used = spy_methods(backend)
    with running(app) as client:
        r = post(
            client,
            data=wav_file(tone(70 * SR)),
            chunk_seconds="30",
            prompt="ctx",
            response_format="verbose_json",
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chunks"] == 3
    assert body["duration"] == 70.0 and body["language"] == "uk"
    starts = [s["start"] for s in body["segments"]]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)
    assert starts[0] == 0.0 and starts[-1] >= 60.0
    assert [s["id"] for s in body["segments"]] == list(range(len(starts)))
    assert used == ["transcribe_iter"] * 3
    prompts = [opts.prompt for _, opts in backend.calls]
    assert prompts[0] == "ctx"
    assert prompts[1] and len(prompts[1]) <= 200 and prompts[1].startswith("speech")


def test_explicit_priority_overrides_duration():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    used = spy_methods(backend)
    with running(app) as client:
        assert post(client, data=wav_file(tone(3 * SR)), priority="batch").json()
        assert post(client, data=wav_file(tone(40 * SR)), priority="realtime").json()
    assert used == ["transcribe_iter", "transcribe"]


@pytest.mark.parametrize(
    "form",
    [
        {"priority": "urgent"},
        {"chunk_seconds": "10"},
        {"chunk_seconds": "1000"},
        {"language": "xx"},
    ],
)
def test_invalid_priority_and_chunk_seconds_400(form):
    app, backend = make()
    with running(app) as client:
        r = post(client, **form)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    assert backend.calls == []


def test_language_detected_among_allowed():
    app, _ = make(FakeAsrBackend(detected="ru"))
    with running(app) as client:
        set_config(client, languages=["pl", "uk"])
        short = post(client)
        long = post(client, data=wav_file(tone(40 * SR)))
    assert short.json()["language"] == "pl"
    assert long.json()["language"] == "pl"


def test_runtime_config_changes_upload_limit():
    app, _ = make()
    two_mb = wav_file(tone(int(2 * 1024 * 1024 / 2) + SR))
    with running(app) as client:
        assert post(client, data=two_mb).status_code == 200
        set_config(client, max_upload_mb=1)
        r = post(client, data=two_mb)
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_runtime_config_changes_default_chunk_seconds():
    app, backend = make(RT_ASR_LANGUAGE="uk")
    with running(app) as client:
        set_config(client, chunk_seconds=30)
        r = post(client, data=wav_file(tone(70 * SR)), response_format="verbose_json")
    assert r.json()["chunks"] == 3


def test_config_restored_from_state_path(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"version": 4, "overrides": {"max_upload_mb": 1}}))
    app, _ = make(state_path=state)
    with running(app) as client:
        assert client.get("/config").json()["version"] == 4
        r = post(client, data=wav_file(tone(int(2 * 1024 * 1024 / 2) + SR)))
    assert r.status_code == 413


@pytest.mark.timeout(60)
def test_realtime_request_overtakes_running_batch():
    backend = FakeAsrBackend(decode_delay=0.3, segment_seconds=10.0)
    app, _ = make(backend, RT_ASR_LANGUAGE="uk")
    batch_wav, short_wav = wav_file(tone(120 * SR)), wav_file(tone(3 * SR))
    batch_done = threading.Event()
    result = {}

    def run_batch():
        result["batch"] = post(client, data=batch_wav, response_format="verbose_json")
        batch_done.set()

    with running(app) as client:
        worker = threading.Thread(target=run_batch, daemon=True)
        worker.start()
        deadline = time.monotonic() + 20
        while not any(n == 120 * SR for n, _ in backend.calls):  # batch decoding
            assert time.monotonic() < deadline, "batch never started"
            time.sleep(0.01)
        time.sleep(0.5)
        assert not batch_done.is_set()
        realtime = post(client, data=short_wav)
        overtook = not batch_done.is_set()
        worker.join(timeout=30)
    assert realtime.status_code == 200
    assert realtime.json()["text"] == "speech 3.00s"
    assert overtook, "realtime answer arrived only after the batch finished"
    assert result["batch"].status_code == 200
    assert len(result["batch"].json()["segments"]) == 12


def test_config_endpoint_mounted_and_reload_swaps_backend():
    app, backend = make()
    with running(app) as client:
        body = set_config(client, model="x")
        assert body["reloaded"] is True
        assert body["values"]["model"] == "x"
        status = client.get("/status").json()
        r = post(client)
    assert status["model"]["id"] == "x" and status["ready"] is True
    assert app.state.runtime.backend is not backend
    assert app.state.runtime.backend.model_id == "x"
    assert r.status_code == 200


def test_client_disconnect_stops_batch_between_chunks():
    """No further chunk is decoded once the client has gone away."""
    app, backend = make(RT_ASR_LANGUAGE="uk")
    req = httpx.Request(
        "POST",
        "http://test/v1/audio/transcriptions",
        files={"file": ("a.wav", wav_file(tone(70 * SR)), "audio/wav")},
        data={"chunk_seconds": "30"},
    )
    body = req.read()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/audio/transcriptions",
        "raw_path": b"/v1/audio/transcriptions",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in req.headers.items()],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    with running(app):
        asyncio.run(app(scope, receive, send))
    assert len(backend.calls) == 1  # first chunk only, then the disconnect


def test_config_device_change_rederives_device_defaults():
    app, _ = make(RT_DEVICES="cpu,cuda")
    with running(app) as client:
        body = set_config(client, device="cuda")
    assert body["values"]["model"] == default_model("asr", "cuda")
    assert body["values"]["compute_type"] == "int8_float16"
    assert body["values"]["beam_size"] == default_beams("asr", "cuda")
    assert body["bootstrap"]["model"] == default_model("asr", "cpu")
    assert app.state.runtime.backend.model_id == default_model("asr", "cuda")


def test_config_device_choices_are_the_node_devices():
    app, _ = make(RT_DEVICES="cpu,cuda")
    with running(app) as client:
        assert client.get("/config").json()["schema"]["device"]["choices"] == [
            "cpu",
            "cuda",
        ]
    app, _ = make()  # no RT_DEVICES: only the start device
    with running(app) as client:
        assert client.get("/config").json()["schema"]["device"]["choices"] == ["cpu"]
        r = client.post("/config", json={"version": 1, "values": {"device": "cuda"}})
        assert r.status_code == 422 and "not available on this node" in r.text
        assert client.get("/status").json()["device"] == "cpu"


def test_config_device_switch_failure_is_explicit(monkeypatch):
    """CUDA OOM on switch: 500 + /status reload_error, old model keeps serving
    on cpu; no silent fallback. The next good switch clears the error."""
    import src.services.asr.app as asr_app

    real = asr_app.make_backend

    def make_backend(settings):
        if settings.device == "cuda":
            raise RuntimeError("CUDA failed with error out of memory")
        return real(settings)

    monkeypatch.setattr(asr_app, "make_backend", make_backend)
    app, _ = make(RT_DEVICES="cpu,cuda")
    with running(app) as client:
        r = client.post("/config", json={"version": 1, "values": {"device": "cuda"}})
        assert r.status_code == 500 and "out of memory" in r.text
        status = client.get("/status").json()
        assert status["device"] == "cpu" and status["ready"] is True
        assert "out of memory" in status["model"]["reload_error"]
        assert client.get("/config").json()["values"]["device"] == "cpu"
        assert post(client).status_code == 200
        set_config(client, model="whisper-base")
        assert client.get("/status").json()["model"]["reload_error"] is None


def spy_runtime(runtime):
    """Record (kind, fn name, audio samples, priority) of submitted jobs."""
    jobs = []
    run, run_iter = runtime.run, runtime.run_iter

    async def spy_run(fn, *args, priority=Priority.REQUEST, **kw):
        jobs.append(("run", fn.__name__, len(args[0]), priority))
        return await run(fn, *args, priority=priority, **kw)

    async def spy_run_iter(fn, *args, priority=Priority.BATCH, **kw):
        jobs.append(("run_iter", fn.__name__, len(args[0]), priority))
        return await run_iter(fn, *args, priority=priority, **kw)

    runtime.run, runtime.run_iter = spy_run, spy_run_iter
    return jobs


def test_job_priorities_and_detection_window():
    app, _ = make()
    jobs = spy_runtime(app.state.runtime)
    with running(app) as client:
        post(client, data=wav_file(tone(5 * SR)))
        post(client, data=wav_file(tone(70 * SR)), chunk_seconds="30")
    assert jobs == [
        ("run", "detect_language", 5 * SR, Priority.REQUEST),
        ("run", "transcribe", 5 * SR, Priority.REQUEST),
        ("run", "detect_language", 30 * SR, Priority.BATCH),
        ("run_iter", "transcribe_iter", 30 * SR, Priority.BATCH),
        ("run_iter", "transcribe_iter", 30 * SR, Priority.BATCH),
        ("run_iter", "transcribe_iter", 10 * SR, Priority.BATCH),
    ]


def test_next_chunk_prompt_is_tail_of_filtered_text():
    backend = FakeAsrBackend(text_fn=lambda audio, opts: "again " * 60)
    app, _ = make(backend, RT_ASR_LANGUAGE="uk")
    with running(app) as client:
        post(client, data=wav_file(tone(70 * SR)), chunk_seconds="30")
    assert backend.calls[1][1].prompt == "again"
