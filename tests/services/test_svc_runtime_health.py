import asyncio
import threading
import time

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from src.services.common.app import create_service_app
from src.services.common.env import SettingsError
from src.services.common.gpu import VramProbe
from src.services.common.runtime import ModelState, ServiceRuntime
from src.services.common.serve import run_service
from src.services.common.settings import BaseSettings, base_from_env


class _Settings(BaseSettings):
    BACKEND_ENV = "RT_ASR_BACKEND"


class GatedBackend:
    name = "gated"
    model_id = "gated-model"

    def __init__(self, gate=None, fail=None):
        self.gate = gate
        self.fail = fail
        self.loaded_on = None

    def load(self):
        if self.gate is not None:
            assert self.gate.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        self.loaded_on = threading.current_thread().name

    def echo(self, x):
        return x


def _settings(**overrides):
    values = base_from_env(
        {"RT_MODEL": "gated-model"},
        settings_cls=_Settings,
        service="rt-asr",
        backends=("fake",),
        default_backend="fake",
        default_model="m",
        compute_cpu="float32",
        compute_gpu="float16",
    )
    return _Settings(**{**values, **overrides})


def _app(backend, hooks=(), config=None, settings=None):
    settings = settings or _settings()
    runtime = ServiceRuntime("rt-asr", lambda: backend, settings.max_pending)
    router = APIRouter()

    @router.get("/echo")
    async def echo(x: str):
        b = runtime.require_ready()
        return {"x": await runtime.run(b.echo, x)}

    app = create_service_app(
        settings=settings,
        runtime=runtime,
        token=None,
        routers=[router],
        lifespan_hooks=hooks,
        config=config,
    )
    return app, runtime


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def started():
    runtimes = []

    def start(backend, max_pending=4):
        runtime = ServiceRuntime("rt-asr", lambda: backend, max_pending)
        runtime.start()
        runtimes.append(runtime)
        assert runtime.wait_ready(5), runtime.error
        return runtime

    yield start
    for runtime in runtimes:
        runtime.stop()


def test_health_ok_while_loading_and_status_transitions():
    gate = threading.Event()
    app, runtime = _app(GatedBackend(gate))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "loading"
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_not_ready"
        assert r.headers["retry-after"] == "5"
        gate.set()
        assert runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["service"] == "rt-asr" and status["version"] == "0.1.0"
        assert status["device"] == "cpu" and status["backend"] == "fake"
        assert status["model"]["id"] == "gated-model"
        assert status["model"]["loaded"] is True
        assert status["ready"] is True and status["vram_mb"] is None
        assert status["pending"] == 0
        assert client.get("/v1/echo", params={"x": "a"}).json() == {"x": "a"}


def test_failed_load_reported():
    app, runtime = _app(GatedBackend(fail="no weights"))
    with TestClient(app) as client:
        assert not runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "failed"
        assert "no weights" in status["model"]["error"]
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_failed"


def test_lifespan_hooks_started_and_stopped():
    events = []

    def hook(runtime):
        events.append(("start", runtime.service))
        return lambda: events.append(("stop", runtime.service))

    app, _ = _app(GatedBackend(), hooks=[hook])
    with TestClient(app):
        assert events == [("start", "rt-asr")]
    assert events == [("start", "rt-asr"), ("stop", "rt-asr")]


def test_vram_probe_parses_own_pid_and_caches():
    calls = []

    class Result:
        stdout = "111, 500\n222, 300\n111, 12\n"

    def runner(*args, **kwargs):
        calls.append(args)
        return Result()

    clock = iter([0.0, 1.0, 10.0])
    probe = VramProbe("cuda", runner=runner, clock=lambda: next(clock), pid=111)
    assert probe.read() == 512
    assert probe.read() == 512
    assert len(calls) == 1
    probe.read()
    assert len(calls) == 2
    assert VramProbe("cpu", runner=runner).read() is None


def test_vram_probe_handles_missing_nvidia_smi():
    def runner(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    assert VramProbe("cuda", runner=runner).read() is None


def test_full_queue_returns_503_server_busy():
    app, runtime = _app(GatedBackend(), settings=_settings(max_pending=1))
    release = threading.Event()
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        blocker = runtime.submit_threadsafe(release.wait, 5)
        r = client.get("/v1/echo", params={"x": "a"})
        release.set()
        blocker.result(5)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "server_busy"
        assert r.headers["retry-after"] == "1"
        assert client.get("/v1/echo", params={"x": "b"}).json() == {"x": "b"}


# --- realtime MVP additions ---------------------------------------------------


class StubConfig:
    def effective(self):
        return {"values": {}, "version": 7}


def test_status_has_pending_and_config_version():
    app, runtime = _app(GatedBackend())
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["pending"] == 0
        assert "config_version" in status and status["config_version"] is None
        assert app.state.config is None

    config = StubConfig()
    app, runtime = _app(GatedBackend(), config=config)
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        assert client.get("/status").json()["config_version"] == 7
        assert app.state.config is config


def test_status_pending_counts_queued_jobs():
    app, runtime = _app(GatedBackend())
    release = threading.Event()
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        blocker = runtime.submit_threadsafe(release.wait, 5)
        assert client.get("/status").json()["pending"] == 1
        release.set()
        blocker.result(5)


def test_reload_swaps_backend_on_success(started):
    old, new = GatedBackend(), GatedBackend()
    runtime = started(old)
    runtime.reload(lambda: new)
    assert runtime.backend is new and runtime.require_ready() is new
    assert runtime.state is ModelState.READY and runtime.error is None
    assert runtime.load_seconds is not None
    # loaded off the caller and off the inference thread
    assert new.loaded_on == "rt-asr-loader"
    assert runtime.submit_threadsafe(runtime.backend.echo, "x").result(5) == "x"


def _factory_raises():
    raise ValueError("bad backend")


@pytest.mark.parametrize(
    "factory, exc, match",
    [
        (lambda: GatedBackend(fail="out of memory"), RuntimeError, "out of memory"),
        (_factory_raises, ValueError, "bad backend"),
    ],
)
def test_reload_failure_keeps_old_backend(started, factory, exc, match):
    old = GatedBackend()
    runtime = started(old)
    with pytest.raises(exc, match=match):
        runtime.reload(factory)
    assert runtime.backend is old and runtime.require_ready() is old
    assert runtime.state is ModelState.READY and runtime.error is None
    assert runtime.submit_threadsafe(old.echo, "still").result(5) == "still"


def test_reload_after_failed_load_recovers():
    runtime = ServiceRuntime("rt-asr", lambda: GatedBackend(fail="no weights"), 4)
    runtime.start()
    try:
        assert not runtime.wait_ready(5)
        with pytest.raises(RuntimeError, match="again"):
            runtime.reload(lambda: GatedBackend(fail="again"))
        assert runtime.state is ModelState.FAILED and "again" in runtime.error
        new = GatedBackend()
        runtime.reload(lambda: new)
        assert runtime.state is ModelState.READY and runtime.error is None
        assert runtime.require_ready() is new
    finally:
        runtime.stop()


def test_reload_shows_loading_while_old_backend_serves():
    old = GatedBackend()
    gate = threading.Event()
    new = GatedBackend(gate)
    app, runtime = _app(old)
    errors = []

    def do_reload():
        try:
            runtime.reload(lambda: new)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        thread = threading.Thread(target=do_reload)
        thread.start()
        assert _until(lambda: runtime.state is ModelState.LOADING)
        status = client.get("/status").json()
        assert status["model"]["state"] == "loading" and status["ready"] is False
        assert status["model"]["loaded"] is True
        assert client.get("/v1/echo", params={"x": "a"}).json() == {"x": "a"}
        assert runtime.require_ready() is old
        gate.set()
        thread.join(5)
        assert not errors
        assert client.get("/status").json()["model"]["state"] == "ready"
        assert runtime.backend is new


def test_reload_swap_waits_for_running_job(started):
    old, new = GatedBackend(), GatedBackend()
    runtime = started(old)
    seen = []
    in_job = threading.Event()
    release = threading.Event()

    def job():
        seen.append(runtime.backend)
        in_job.set()
        assert release.wait(5)
        seen.append(runtime.backend)

    fut = runtime.submit_threadsafe(job)
    assert in_job.wait(5)
    thread = threading.Thread(target=runtime.reload, args=(lambda: new,))
    thread.start()
    # new model loaded, swap queued behind the running job
    assert _until(lambda: new.loaded_on is not None and runtime.worker.pending == 2)
    assert runtime.backend is old
    release.set()
    fut.result(5)
    thread.join(5)
    assert seen == [old, old]
    assert runtime.backend is new and runtime.state is ModelState.READY


def test_reload_timeout_keeps_old_backend(started):
    old = GatedBackend()
    gate = threading.Event()
    new = GatedBackend(gate)
    runtime = started(old)
    with pytest.raises(TimeoutError):
        runtime.reload(lambda: new, timeout_s=0.1)
    assert runtime.backend is old and runtime.state is ModelState.READY
    gate.set()
    assert _until(lambda: new.loaded_on is not None)
    # the abandoned load never gets swapped in; a later reload still works
    newer = GatedBackend()
    runtime.reload(lambda: newer)
    assert runtime.backend is newer


@pytest.mark.asyncio
async def test_run_iter_returns_items(started):
    runtime = started(GatedBackend())
    got = []

    def gen(n, start=0):
        yield from range(start, start + n)

    items = await runtime.run_iter(gen, 3, start=10, on_item=got.append)
    assert items == [10, 11, 12] and got == items
    assert runtime.submit_iter(gen, 2).result(5) == [0, 1]


@pytest.mark.asyncio
async def test_run_iter_cancelled_stops_generator(started):
    runtime = started(GatedBackend())
    stepping = threading.Event()
    closed = threading.Event()

    def gen():
        try:
            while True:
                stepping.set()
                yield 1
        finally:
            closed.set()

    task = asyncio.ensure_future(runtime.run_iter(gen))
    assert await asyncio.to_thread(stepping.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(closed.wait, 5)
    assert _until(lambda: runtime.worker.pending == 0)


def test_run_service_exits_2_on_settings_error(capsys):
    def loader(env):
        raise SettingsError("RT_PORT must be <= 65535")

    assert run_service(loader, lambda settings: None) == 2
    assert "RT_PORT must be <= 65535" in capsys.readouterr().err


def test_run_service_runs_uvicorn(monkeypatch):
    import uvicorn

    from src.services.common import serve

    calls = []
    monkeypatch.setattr(serve, "setup_logging", lambda level: calls.append(level))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    settings = _settings(port=9999)
    app = object()
    assert run_service(lambda env: settings, lambda s: app) == 0
    assert calls[0] == "info"
    assert calls[1][0] is app
    assert calls[1][1]["port"] == 9999 and calls[1][1]["workers"] == 1
