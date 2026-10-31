import asyncio
import threading
import time
import weakref

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from src.services.common.app import create_service_app
from src.services.common.env import SettingsError
from src.services.common.errors import ApiError
from src.services.common.gpu import VramProbe
from src.services.common.runtime import MB, ModelBusy, ModelState, ServiceRuntime
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
        self.unloaded = False

    def fetch(self, progress=None):
        pass

    def unload(self):
        self.unloaded = True

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
        assert client.get("/health").json() == {
            "status": "ok",
            "model": {"state": "loading"},
        }
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "loading"
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_loading"
        assert r.headers["retry-after"] == "5"
        gate.set()
        assert runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["service"] == "rt-asr" and status["version"] == "0.1.0"
        assert status["backend"] == "fake"
        assert status["model"] == {
            "id": "gated-model",
            "state": "ready",
            "error": None,
            "download": None,
            "device": "cpu",
        }
        assert status["ready"] is True and status["memory"]["vram_mb"] is None
        assert status["memory"]["rss_mb"] > 0 and "swap_mb" in status["memory"]
        assert status["pending"] == 0
        assert client.get("/v1/echo", params={"x": "a"}).json() == {"x": "a"}


def test_failed_load_reported():
    app, runtime = _app(GatedBackend(fail="no weights"))
    with TestClient(app) as client:
        assert not runtime.wait_ready(5)
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "error"
        assert "no weights" in status["model"]["error"]
        r = client.get("/v1/echo", params={"x": "a"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_error"


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
    probe = VramProbe(runner=runner, clock=lambda: next(clock), pid=111)
    assert probe.read("cuda") == 512
    assert probe.read("cuda") == 512
    assert len(calls) == 1
    probe.read("cuda")
    assert len(calls) == 2
    assert VramProbe(runner=runner).read("cpu") is None


def test_vram_probe_handles_missing_nvidia_smi():
    def runner(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    assert VramProbe(runner=runner).read("cuda") is None


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
    def current(self):
        return _settings(backend="reloaded", device="cuda", model="new-model")

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
        status = client.get("/status").json()
        assert status["config_version"] == 7
        assert status["backend"] == "reloaded"
        assert status["model"]["device"] == "cuda"
        assert status["model"]["id"] == "new-model"
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


class Backends:
    """Backend factory handing out queued backends (or raising)."""

    def __init__(self, *backends):
        self.queue = list(backends)
        self.built = []

    def __call__(self):
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        self.built.append(item)
        return item


def _runtime(*backends, **kw):
    runtime = ServiceRuntime("rt-asr", Backends(*backends), 4, **kw)
    runtime.start()
    return runtime


def _load_when_free(runtime, timeout=5.0):
    """load() once the abandoned load has released the model slot."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return runtime.load()
        except ModelBusy:
            assert time.monotonic() < deadline
            time.sleep(0.005)


def _settle(runtime, timeout=5.0):
    assert _until(
        lambda: runtime.state in (ModelState.READY, ModelState.ERROR), timeout
    ), runtime.state
    return runtime.state


def test_reload_unloads_old_model_before_loading_new():
    old = GatedBackend()
    gate = threading.Event()
    new = GatedBackend(gate)
    runtime = _runtime(old, new)
    try:
        assert runtime.wait_ready(5)
        runtime.reload()  # returns at once: the load is async
        # the old model is gone before the new one loads: never two in memory
        assert old.unloaded and runtime.backend is None
        assert _until(lambda: runtime.state is ModelState.LOADING)
        with pytest.raises(ApiError) as err:
            runtime.require_ready()
        assert err.value.status == 503 and err.value.code == "model_loading"
        gate.set()
        assert _settle(runtime) is ModelState.READY
        assert runtime.backend is new and runtime.error is None
        assert new.loaded_on == "rt-asr-loader"
        assert runtime.submit_threadsafe(new.echo, "x").result(5) == "x"
    finally:
        runtime.stop()


@pytest.mark.parametrize(
    "second, match",
    [
        (GatedBackend(fail="CUDA failed with error out of memory"), "out of memory"),
        (ValueError("bad backend"), "bad backend"),
    ],
)
def test_reload_failure_is_explicit_error_without_old_model(second, match):
    old = GatedBackend()
    runtime = _runtime(old, second, GatedBackend())
    try:
        assert runtime.wait_ready(5)
        runtime.reload()
        assert _settle(runtime) is ModelState.ERROR
        assert match in runtime.error and runtime.backend is None
        assert old.unloaded  # no keeping/restoring the old model
        with pytest.raises(ApiError) as err:
            runtime.require_ready()
        assert err.value.code == "model_error" and match in err.value.message
        assert runtime.load() is True  # explicit retry
        assert _settle(runtime) is ModelState.READY and runtime.error is None
    finally:
        runtime.stop()


def test_reload_unload_waits_for_running_job():
    old, new = GatedBackend(), GatedBackend()
    runtime = _runtime(old, new)
    try:
        assert runtime.wait_ready(5)
        seen = []
        in_job, release = threading.Event(), threading.Event()

        def job():
            seen.append(runtime.backend)
            in_job.set()
            assert release.wait(5)
            seen.append(runtime.backend)

        fut = runtime.submit_threadsafe(job)
        assert in_job.wait(5)
        thread = threading.Thread(target=runtime.reload)
        thread.start()
        time.sleep(0.05)
        assert not old.unloaded  # never under a running job
        release.set()
        fut.result(5)
        thread.join(5)
        assert seen == [old, old] and old.unloaded
        assert _settle(runtime) is ModelState.READY and runtime.backend is new
    finally:
        runtime.stop()


def test_load_timeout_covers_load_only_and_abandoned_load_is_discarded():
    gate = threading.Event()
    slow = GatedBackend(gate)
    runtime = _runtime(slow, GatedBackend(), load_timeout_s=0.1)
    try:
        assert _settle(runtime) is ModelState.ERROR
        assert "did not finish within" in runtime.error
        # the abandoned load still runs: no second load may start next to it
        with pytest.raises(ModelBusy, match="abandoned"):
            runtime.load()
        with pytest.raises(ModelBusy):
            runtime.unload()
        gate.set()
        assert _until(lambda: slow.unloaded)  # discarded when it ends
        assert runtime.backend is None and runtime.state is ModelState.ERROR
        assert _load_when_free(runtime)
        assert _settle(runtime) is ModelState.READY
    finally:
        runtime.stop()


def test_download_is_not_bounded_by_load_timeout_and_reports_progress():
    fetching, release = threading.Event(), threading.Event()

    class Downloading(GatedBackend):
        def fetch(self, progress=None):
            progress(0, 300 * MB)
            fetching.set()
            assert release.wait(5)
            progress(150 * MB, 300 * MB)
            assert release.wait(5)

    runtime = _runtime(Downloading(), load_timeout_s=0.05)
    try:
        assert fetching.wait(5)
        time.sleep(0.2)  # well past the load timeout
        assert runtime.state is ModelState.DOWNLOADING
        assert runtime.download == {"done_mb": 0.0, "total_mb": 300.0}
        with pytest.raises(ApiError) as err:
            runtime.require_ready()
        assert err.value.code == "model_downloading" and "0/300 MB" in str(
            err.value.message
        )
        release.set()
        assert _settle(runtime) is ModelState.READY and runtime.download is None
    finally:
        runtime.stop()


def test_unload_and_load_explicitly():
    runtime = _runtime(GatedBackend(), GatedBackend())
    try:
        assert runtime.wait_ready(5)
        first = runtime.backend
        assert runtime.load() is False  # already ready
        assert runtime.unload() is True
        assert first.unloaded and runtime.backend is None
        assert runtime.state is ModelState.UNLOADED and not runtime.ready
        assert runtime.unload() is False
        with pytest.raises(ApiError) as err:
            runtime.require_ready()
        assert err.value.code == "model_unloaded"
        assert runtime.load() is True
        assert _settle(runtime) is ModelState.READY
    finally:
        runtime.stop()


def test_model_endpoints_and_503_with_state():
    gate = threading.Event()
    factory = Backends(GatedBackend(), GatedBackend(gate))
    runtime = ServiceRuntime("rt-asr", factory, 4)
    router = APIRouter()

    @router.get("/echo")
    async def echo(x: str):
        b = await runtime.acquire()
        return {"x": await runtime.run(b.echo, x)}

    app = create_service_app(
        settings=_settings(), runtime=runtime, token="t", routers=[router]
    )
    auth = {"Authorization": "Bearer t"}
    with TestClient(app) as client:
        assert runtime.wait_ready(5)
        assert client.post("/model/unload").status_code == 401
        r = client.post("/model/unload", headers=auth)
        assert r.status_code == 200 and r.json()["model"]["state"] == "unloaded"
        r = client.get("/v1/echo", params={"x": "a"}, headers=auth)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "model_unloaded"
        status = client.get("/status").json()
        assert status["ready"] is False and status["model"]["state"] == "unloaded"
        r = client.post("/model/load", headers=auth)
        assert r.status_code == 200 and r.json()["model"]["state"] == "loading"
        r = client.post("/model/load", headers=auth)
        assert r.status_code == 409 and r.json()["error"]["code"] == "model_busy"
        r = client.get("/v1/echo", params={"x": "a"}, headers=auth)
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_loading"
        gate.set()
        assert runtime.wait_ready(5)
        assert client.get("/v1/echo", params={"x": "b"}, headers=auth).json() == {
            "x": "b"
        }


# --- memory: no backend outlives its unload --------------------------------


class Heavy(GatedBackend):
    """Backend holding 'weights'; tracked by weakref."""

    def __init__(self, gate=None, fail=None):
        super().__init__(gate, fail)
        self.weights = bytearray(1024)


def _alive(refs):
    """Live backends WITHOUT a gc pass here: the runtime frees them itself."""
    return [r for r in refs if r() is not None]


def test_no_backend_survives_reloads_failures_and_abandoned_loads():
    refs = []

    def factory():
        backend = Heavy(fail="CUDA failed with error out of memory" if fail else None)
        refs.append(weakref.ref(backend))
        return backend

    fail = False
    runtime = ServiceRuntime("rt-asr", factory, 4, load_timeout_s=5)
    runtime.start()
    try:
        assert runtime.wait_ready(5)
        for i in range(6):
            fail = i % 2 == 1
            runtime.reload()
            _settle(runtime)
            assert runtime.state is (ModelState.ERROR if fail else ModelState.READY)
        fail = False
        assert runtime.load() is True and _settle(runtime) is ModelState.READY
        # only the current backend is alive: old and failed ones are freed
        assert [r() for r in _alive(refs)] == [runtime.backend]
        assert len(refs) == 8
        current = runtime.backend
        current_ref = weakref.ref(current)
        del current
        runtime.unload()
        assert current_ref() is None and _alive(refs) == []
    finally:
        runtime.stop()


def test_abandoned_load_backend_is_freed_when_it_ends():
    gate = threading.Event()
    refs = []

    def factory():
        backend = Heavy(gate)
        refs.append(weakref.ref(backend))
        return backend

    runtime = ServiceRuntime("rt-asr", factory, 4, load_timeout_s=0.05)
    runtime.start()
    try:
        assert _settle(runtime) is ModelState.ERROR
        abandoned = refs[0]
        gate.set()
        assert _load_when_free(runtime)
        assert abandoned() is None  # freed as soon as the load ended
        assert _settle(runtime) is ModelState.READY
        assert [r() for r in _alive(refs)] == [runtime.backend]
    finally:
        runtime.stop()


@pytest.mark.slow
@pytest.mark.timeout(900)
def test_real_whisper_rss_stays_flat_over_model_switches(real_model_dir):
    """faster-whisper tiny/base switched 20 times: RSS plateaus (allocator
    noise only), no growth by a model per switch."""
    from src.services.asr.backend import make_backend
    from src.services.asr.settings import AsrSettings
    from src.services.common.memory import process_memory

    models = ["whisper-tiny", "whisper-base"]
    current = {"i": 0}

    def factory():
        model = models[current["i"] % 2]
        return make_backend(
            AsrSettings.from_env(
                {"RT_ASR_BACKEND": "faster-whisper", "RT_MODEL": model}
            )
        )

    runtime = ServiceRuntime("rt-asr", factory, 4, load_timeout_s=600)
    runtime.start()
    try:
        assert runtime.wait_ready(600), runtime.error
        rss = []
        for i in range(1, 21):
            current["i"] = i
            runtime.reload()
            assert _settle(runtime, 600) is ModelState.READY, runtime.error
            rss.append(process_memory()["rss_mb"])
        # whisper-base alone is ~150 MB of RSS: a leak would add that per switch
        assert max(rss[12:]) - max(rss[2:8]) < 100, rss
    finally:
        runtime.stop()


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


def test_run_service_runs_uvicorn_on_bound_sockets(monkeypatch):
    import uvicorn

    from src.services.common import serve

    calls = []
    monkeypatch.setattr(serve, "setup_logging", lambda level: calls.append(level))
    monkeypatch.setattr(
        serve, "bind_sockets", lambda addrs, port: calls.append((addrs, port)) or ["s"]
    )

    def fake_run(self, sockets=None):
        calls.append((self.config.app, self.config.workers, sockets))

    monkeypatch.setattr(uvicorn.Server, "run", fake_run)
    settings = _settings(port=9999, listen_addresses=("127.0.0.1", "100.64.0.1"))
    app = object()
    assert run_service(lambda env: settings, lambda s: app) == 0
    assert calls[0] == "info"
    assert calls[1] == (("127.0.0.1", "100.64.0.1"), 9999)
    assert calls[2] == (app, 1, ["s"])


def test_run_service_exits_2_when_bind_fails(monkeypatch, capsys):
    from src.services.common import serve

    monkeypatch.setattr(serve, "setup_logging", lambda level: None)

    def boom(addrs, port):
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(serve, "bind_sockets", boom)
    assert run_service(lambda env: _settings(port=9999), lambda s: object()) == 2
    assert "cannot listen on 127.0.0.1 port 9999" in capsys.readouterr().err


# -- lazy load / idle unload (RT_IDLE_UNLOAD_MINUTES) ---------------------


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _lazy_app(clock, loads):
    def factory():
        loads.append(1)
        return GatedBackend(gate=None)

    runtime = ServiceRuntime("rt-asr", factory, 4, idle_unload_s=900, clock=clock)
    router = APIRouter()

    @router.get("/echo")
    async def echo(x: str):
        b = await runtime.acquire()
        return {"x": await runtime.run(b.echo, x)}

    app = create_service_app(
        settings=_settings(), runtime=runtime, token=None, routers=[router]
    )
    return app, runtime


def test_lazy_load_on_first_request_and_health_never_loads():
    clock, loads = Clock(), []
    app, runtime = _lazy_app(clock, loads)
    with TestClient(app) as client:
        for _ in range(3):
            health = client.get("/health").json()
            assert health["model"] == {"state": "unloaded"}
        status = client.get("/status").json()
        assert status["ready"] is True and status["model"]["state"] == "unloaded"
        assert loads == []
        assert client.get("/v1/echo", params={"x": "a"}).json() == {"x": "a"}
        assert loads == [1]
        assert client.get("/health").json()["model"] == {"state": "ready"}


def test_idle_unload_after_timeout_then_reload_on_demand():
    clock, loads = Clock(), []
    app, runtime = _lazy_app(clock, loads)
    with TestClient(app) as client:
        client.get("/v1/echo", params={"x": "a"})
        clock.t += 899
        assert runtime.check_idle() is False  # not idle long enough
        client.get("/v1/echo", params={"x": "b"})  # usage resets the timer
        clock.t += 899
        assert runtime.check_idle() is False
        clock.t += 2
        assert runtime.check_idle() is True
        assert runtime.backend is None and runtime.state.value == "unloaded"
        assert client.get("/health").json()["model"]["state"] == "unloaded"
        assert client.get("/status").json()["ready"] is True  # wakes on request
        assert client.get("/v1/echo", params={"x": "c"}).json() == {"x": "c"}
        assert loads == [1, 1]
        # an explicit unload is not undone by the next request
        assert runtime.unload() is True
        r = client.get("/v1/echo", params={"x": "d"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "model_unloaded"
        assert client.get("/status").json()["ready"] is False and loads == [1, 1]


def test_idle_unload_zero_loads_at_start_and_never_unloads(started):
    clock = Clock()
    runtime = ServiceRuntime(
        "rt-asr", lambda: GatedBackend(gate=None), 4, idle_unload_s=0, clock=clock
    )
    runtime.start()
    try:
        assert runtime.wait_ready(5)
        clock.t += 10**6
        assert runtime.check_idle() is False and runtime.backend is not None
    finally:
        runtime.stop()


def test_idle_unload_skips_while_jobs_pending():
    clock, loads = Clock(), []
    app, runtime = _lazy_app(clock, loads)
    with TestClient(app):
        assert runtime.wait_ready(5)
        gate = threading.Event()
        fut = runtime.submit_threadsafe(gate.wait, 5)
        clock.t += 10**4
        assert runtime.check_idle() is False
        gate.set()
        fut.result(5)
        clock.t += 10**4
        assert runtime.check_idle() is True


def test_idle_unload_setting_from_env():
    from src.services.asr.settings import AsrSettings
    from src.services.tts.settings import TtsSettings

    assert AsrSettings.from_env({}).idle_unload_minutes == 15
    assert (
        TtsSettings.from_env({"RT_IDLE_UNLOAD_MINUTES": "0"}).idle_unload_minutes == 0
    )
