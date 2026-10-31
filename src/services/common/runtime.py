"""Model lifecycle (explicit load/unload, model change, idle unload) +
inference worker.

States (``ModelState``): ``unloaded`` -> [``downloading`` ->] ``loading`` ->
``ready`` | ``error``. At most one model is in memory: a model change
(``reload``) unloads the old model first, then loads the new one; a failed
load leaves ``error`` with its text and no model (never a kept/restored old
one). The download (``backend.fetch``) is not bounded by a timeout and reports
its progress; ``load_timeout_s`` bounds only ``backend.load``. A load that
overruns it is abandoned: state ``error``; it is discarded when it ends and no
other load starts until then (a thread cannot be killed).

Every dropped backend is ``unload()``-ed on the inference thread (no job runs
on it meanwhile), then ``release_memory`` collects cycles and trims the heap.

With ``idle_unload_s > 0`` (``RT_IDLE_UNLOAD_MINUTES``) the model starts
unloaded, the first request loads it (``wake_on_request``) and it is dropped
after that many seconds without requests. An explicit ``unload`` clears
``wake_on_request``: the model stays unloaded until ``load``/``reload``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from enum import Enum
from typing import Any, Callable, Iterator, Optional, TypeVar

from loguru import logger

from .errors import ApiError
from .memory import release_memory
from .worker import InferenceWorker, Priority

T = TypeVar("T")

MB = 1024 * 1024
# Bounds backend.load() only (weights -> RAM/VRAM); never the download.
LOAD_TIMEOUT_S = 300.0


class ModelState(str, Enum):
    UNLOADED = "unloaded"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"


class ModelBusy(ApiError):
    """Another load/unload holds the model slot (HTTP 409)."""

    def __init__(self, message: str) -> None:
        super().__init__(409, "model_busy", message)


_LOADING = "a model load is running"
_UNLOADING = "a model unload is running"
_ABANDONED = "an abandoned model load is still running (discarded when it ends)"


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _Attempt:
    """One load; ``lazy`` = started by a request (requests wait for it)."""

    __slots__ = ("lazy", "finished", "abandoned")

    def __init__(self, lazy: bool) -> None:
        self.lazy = lazy
        self.finished = False
        self.abandoned = False


class ServiceRuntime:
    """Owns the backend and the single inference thread that uses it.

    ``backend`` only changes on the inference thread (between two jobs).
    Handlers capture the backend once (``b = await runtime.acquire()``) and
    submit ``b.method``; generator jobs capture it in their factory.
    """

    def __init__(
        self,
        service: str,
        backend_factory: Callable[[], Any],
        max_pending: int,
        *,
        idle_unload_s: float = 0.0,
        load_timeout_s: float = LOAD_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self._factory = backend_factory
        self.worker = InferenceWorker(max_pending, name=f"{service}-inference")
        self.backend: Any = None
        self.idle_unload_s = idle_unload_s
        self.load_timeout_s = load_timeout_s
        self._clock = clock
        self.last_used = clock()
        self._cond = threading.Condition()  # guards everything below
        self.state = ModelState.UNLOADED
        self.error: Optional[str] = None
        self.download: Optional[dict] = None  # {"done_mb", "total_mb"}
        self.wake_on_request = idle_unload_s > 0
        self._busy: Optional[str] = None  # who holds the model slot
        self._attempt: Optional[_Attempt] = None
        self._idle_stop = threading.Event()
        self._started = False

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.worker.start()
        if self.idle_unload_s > 0:
            logger.info(
                "{}: lazy model load, idle unload after {:.0f}s",
                self.service,
                self.idle_unload_s,
            )
            threading.Thread(
                target=self._idle_loop, name=f"{self.service}-idle", daemon=True
            ).start()
            return
        self.load()

    def stop(self) -> None:
        self._idle_stop.set()
        self.worker.stop()

    @property
    def ready(self) -> bool:
        """Serves now, or loads on the next request (idle-unloaded)."""
        with self._cond:
            return self.state is ModelState.READY or (
                self.state is ModelState.UNLOADED and self.wake_on_request
            )

    def snapshot(self) -> tuple[ModelState, Optional[str], Optional[dict]]:
        """(state, error, download) read together."""
        with self._cond:
            return self.state, self.error, self.download

    def _set_state(
        self,
        state: ModelState,
        *,
        error: Optional[str] = None,
        download: Optional[dict] = None,
    ) -> None:
        """Caller holds ``_cond``."""
        self.state, self.error, self.download = state, error, download
        self._cond.notify_all()

    def _claim(self, what: str) -> None:
        """Take the model slot (caller holds ``_cond``) or raise ModelBusy."""
        if self._busy is not None:
            raise ModelBusy(f"{self.service}: {self._busy}")
        self._busy = what

    # --- load ---------------------------------------------------------------

    def load(self) -> bool:
        """Start loading the configured model (async; poll ``state``).

        False if the model is already ready. ModelBusy while another
        load/unload runs.
        """
        with self._cond:
            self._claim(_LOADING)
            if self.state is ModelState.READY:
                self._busy = None
                return False
            self._begin_load(lazy=False)
        return True

    def reload(self) -> None:
        """Model/device change: unload the old model, then load the
        configured one (async). Never two models in memory."""
        if not self._started:
            raise RuntimeError(f"{self.service}: runtime is not started")
        with self._cond:
            self._claim(_LOADING)
            self._set_state(ModelState.LOADING)  # requests: 503 model_loading
        try:
            self._drop_backend()
        except BaseException as exc:
            with self._cond:
                self._busy = None
                self._set_state(ModelState.ERROR, error=_describe(exc))
            raise
        with self._cond:
            self._begin_load(lazy=False)

    def _begin_load(self, lazy: bool) -> None:
        """Caller holds ``_cond`` and the model slot (handed to the loader)."""
        attempt = _Attempt(lazy)
        self._attempt = attempt
        self._set_state(ModelState.LOADING)
        threading.Thread(
            target=self._load,
            args=(attempt,),
            name=f"{self.service}-loader",
            daemon=True,
        ).start()

    def _on_download(self, done: int, total: int) -> None:
        with self._cond:
            self._set_state(
                ModelState.DOWNLOADING,
                download={
                    "done_mb": round(done / MB, 1),
                    "total_mb": round(total / MB, 1),
                },
            )

    def _load(self, attempt: _Attempt) -> None:
        t0 = time.monotonic()
        backend: Any = None
        timer: Optional[threading.Timer] = None
        error: Optional[str] = None
        try:
            backend = self._factory()
            logger.info(
                "{}: loading model {} ({})",
                self.service,
                getattr(backend, "model_id", "?"),
                getattr(backend, "name", "?"),
            )
            backend.fetch(self._on_download)  # no timeout: downloads take long
            with self._cond:
                self._set_state(ModelState.LOADING)
            timer = threading.Timer(self.load_timeout_s, self._abandon, (attempt,))
            timer.daemon = True
            timer.start()
            backend.load()
        except Exception as exc:  # noqa: BLE001 — reported via state "error"
            # keep only the text: the traceback pins the half-built backend
            error = _describe(exc)
        finally:
            if timer is not None:
                timer.cancel()
        with self._cond:
            attempt.finished = True
            commit = error is None and not attempt.abandoned
            if commit:
                self.backend = backend  # nothing runs on a missing backend
                self._busy = None
                self._attempt = None
                self._set_state(ModelState.READY)
        if commit:
            self.touch()
            logger.info(
                "{}: model {} ready in {:.1f}s",
                self.service,
                getattr(backend, "model_id", "?"),
                time.monotonic() - t0,
            )
            return
        if backend is not None:
            self._discard(backend)
        backend = None
        release_memory()
        with self._cond:
            self._busy = None
            self._attempt = None
            if not attempt.abandoned:
                self._set_state(ModelState.ERROR, error=error)
        if attempt.abandoned:
            logger.warning(
                "{}: abandoned model load ended after {:.0f}s; discarded",
                self.service,
                time.monotonic() - t0,
            )
        else:
            logger.error("{}: model load failed: {}", self.service, error)

    def _abandon(self, attempt: _Attempt) -> None:
        with self._cond:
            if attempt.finished:
                return
            attempt.abandoned = True
            self._busy = _ABANDONED
            message = f"model load did not finish within {self.load_timeout_s:.0f}s"
            self._set_state(ModelState.ERROR, error=message)
        logger.error("{}: {}; {}", self.service, message, _ABANDONED)

    # --- unload -------------------------------------------------------------

    def unload(self) -> bool:
        """Free the model now; it stays unloaded until load/reload."""
        with self._cond:
            self._claim(_UNLOADING)
            self.wake_on_request = False
            if self.backend is None and self.state is ModelState.UNLOADED:
                self._busy = None
                return False
        try:
            self._drop_backend()
        finally:
            with self._cond:
                self._busy = None
                self._set_state(ModelState.UNLOADED)
        logger.info("{}: model unloaded", self.service)
        return True

    def _drop_backend(self) -> None:
        """Unload the backend on the inference thread (between two jobs)."""

        def drop() -> None:
            backend, self.backend = self.backend, None
            if backend is not None:
                self._discard(backend)

        self.worker.submit(drop, priority=Priority.FINAL, bypass_limit=True).result()
        release_memory()

    def _discard(self, backend: Any) -> None:
        try:
            backend.unload()
        except Exception:  # noqa: BLE001 — logged; the reference is dropped anyway
            logger.exception(
                "{}: unloading model {} failed",
                self.service,
                getattr(backend, "model_id", "?"),
            )

    # --- lazy load / idle unload -------------------------------------------

    def touch(self) -> None:
        self.last_used = self._clock()

    def _wake(self) -> bool:
        """Start a lazy load if idle-unloaded; True if requests should wait."""
        with self._cond:
            if (
                self.state is ModelState.UNLOADED
                and self.wake_on_request
                and self._busy is None
                and self._started
            ):
                self._busy = _LOADING
                self._begin_load(lazy=True)
                logger.info("{}: loading model on demand", self.service)
            attempt = self._attempt
            return (
                self.state is ModelState.LOADING
                and attempt is not None
                and attempt.lazy
            )

    def _wait_while_loading(self, timeout: float) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self.state is not ModelState.LOADING, timeout)

    async def acquire(self) -> Any:
        """Backend for a request; 503 with the state when it is not ready.

        Only a lazy (request-started) load is waited for; downloads, explicit
        loads, errors and an explicit unload answer 503 at once.
        """
        self.touch()
        backend = self.backend
        if backend is not None:
            return backend
        if self._wake():
            await asyncio.to_thread(self._wait_while_loading, self.load_timeout_s)
        return self.require_ready()

    def check_idle(self) -> bool:
        """Unload the model if idle long enough; True if it was unloaded."""
        if self.idle_unload_s <= 0:
            return False
        idle = self._clock() - self.last_used
        with self._cond:
            if (
                self.state is not ModelState.READY
                or self.backend is None
                or self._busy is not None
                or self.worker.pending
                or idle < self.idle_unload_s
            ):
                return False
            self._busy = _UNLOADING
            model_id = getattr(self.backend, "model_id", "?")
        try:
            self._drop_backend()
        finally:
            with self._cond:
                self._busy = None
                self.wake_on_request = True
                self._set_state(ModelState.UNLOADED)
        logger.info(
            "{}: model {} unloaded after {:.0f}s without requests",
            self.service,
            model_id,
            idle,
        )
        return True

    def _idle_loop(self) -> None:
        period = min(30.0, max(self.idle_unload_s / 4, 0.05))
        while not self._idle_stop.wait(period):
            try:
                self.check_idle()
            except Exception:  # noqa: BLE001 — keep the watchdog alive
                logger.exception("{}: idle unload check failed", self.service)

    def wait_ready(self, timeout: float) -> bool:
        """Wait for a loaded model; in lazy mode this triggers the load."""
        self._wake()
        with self._cond:
            self._cond.wait_for(
                lambda: self.state in (ModelState.READY, ModelState.ERROR), timeout
            )
            return self.state is ModelState.READY

    def require_ready(self) -> Any:
        backend = self.backend
        if backend is not None:
            return backend
        raise self.not_ready()

    def not_ready(self) -> ApiError:
        """503 naming the model state (code ``model_<state>``)."""
        state, error, download = self.snapshot()
        if state is ModelState.ERROR:
            return ApiError(503, "model_error", f"model error: {error}")
        if state is ModelState.DOWNLOADING:
            progress = (
                f" ({download['done_mb']:.0f}/{download['total_mb']:.0f} MB)"
                if download
                else ""
            )
            return ApiError(
                503,
                "model_downloading",
                f"model is downloading{progress}",
                headers={"Retry-After": "10"},
            )
        if state is ModelState.LOADING:
            return ApiError(
                503,
                "model_loading",
                "model is loading",
                headers={"Retry-After": "5"},
            )
        return ApiError(
            503, "model_unloaded", "model is unloaded; POST /model/load loads it"
        )

    # --- submission ---------------------------------------------------------

    def submit_threadsafe(
        self,
        fn: Callable[..., Any],
        *args: Any,
        priority: Priority = Priority.REQUEST,
        bypass_limit: bool = False,
        **kwargs: Any,
    ) -> concurrent.futures.Future:
        """Thread-safe entry into the inference queue (HTTP, WS, workers)."""
        fut = self.worker.submit(
            fn, *args, priority=priority, bypass_limit=bypass_limit, **kwargs
        )
        fut.add_done_callback(lambda _f: self.touch())  # idle counts from the end
        return fut

    async def run(
        self,
        fn: Callable[..., Any],
        *args: Any,
        priority: Priority = Priority.REQUEST,
        **kwargs: Any,
    ) -> Any:
        return await asyncio.wrap_future(
            self.submit_threadsafe(fn, *args, priority=priority, **kwargs)
        )

    def submit_iter(
        self,
        gen_factory: Callable[..., Iterator[T]],
        *args: Any,
        priority: Priority = Priority.BATCH,
        on_item: Optional[Callable[[T], None]] = None,
        cancel: Optional[threading.Event] = None,
        **kwargs: Any,
    ) -> concurrent.futures.Future[list[T]]:
        """Stepped generator job (see ``InferenceWorker.submit_iter``)."""
        fut = self.worker.submit_iter(
            gen_factory,
            *args,
            priority=priority,
            on_item=on_item,
            cancel=cancel,
            **kwargs,
        )
        fut.add_done_callback(lambda _f: self.touch())
        return fut

    async def run_iter(
        self,
        gen_factory: Callable[..., Iterator[T]],
        *args: Any,
        priority: Priority = Priority.BATCH,
        on_item: Optional[Callable[[T], None]] = None,
        cancel: Optional[threading.Event] = None,
        **kwargs: Any,
    ) -> list[T]:
        """Await a generator job; cancelling the awaiter stops the generator."""
        cancel = cancel if cancel is not None else threading.Event()
        fut = self.submit_iter(
            gen_factory,
            *args,
            priority=priority,
            on_item=on_item,
            cancel=cancel,
            **kwargs,
        )
        try:
            return await asyncio.wrap_future(fut)
        except asyncio.CancelledError:
            cancel.set()  # a running generator stops before its next step
            raise
