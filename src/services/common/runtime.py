"""Model lifecycle (background load, hot reload) + access to the inference worker."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from enum import Enum
from typing import Any, Callable, Iterator, Optional, TypeVar

from loguru import logger

from .errors import ApiError
from .worker import InferenceWorker, Priority

T = TypeVar("T")


class ModelState(str, Enum):
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class _LoadAbandoned(TimeoutError):
    """reload() timed out; the loader thread now owns ``_load_lock``."""


class ServiceRuntime:
    """Owns the backend and the single inference thread that uses it.

    ``backend`` only changes on the inference thread (between two jobs), so a
    plain job sees one backend for its whole run. Handlers should still capture
    the backend once (``b = runtime.require_ready()``) and submit ``b.method``;
    generator jobs must capture it in their factory, because a reload may land
    between two steps.
    """

    def __init__(
        self,
        service: str,
        backend_factory: Callable[[], Any],
        max_pending: int,
    ) -> None:
        self.service = service
        self._factory = backend_factory
        self.worker = InferenceWorker(max_pending, name=f"{service}-inference")
        self.backend: Any = None
        self.state = ModelState.LOADING
        self.error: Optional[str] = None
        self.load_seconds: Optional[float] = None
        self._settled = threading.Event()
        # One model load at a time (initial load and reloads). Released by
        # whoever finishes the load, which is not always the acquiring thread.
        self._load_lock = threading.Lock()
        self._started = False

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.worker.start()
        self._load_lock.acquire()  # released by _initial_load
        threading.Thread(
            target=self._initial_load, name=f"{self.service}-loader", daemon=True
        ).start()

    def _build(self, factory: Callable[[], Any]) -> Any:
        backend = factory()
        logger.info(
            "{}: loading model {} ({})",
            self.service,
            getattr(backend, "model_id", "?"),
            getattr(backend, "name", "?"),
        )
        backend.load()
        return backend

    def _initial_load(self) -> None:
        t0 = time.monotonic()
        try:
            backend = self._build(self._factory)
        except Exception as exc:  # noqa: BLE001 — reported via /status
            logger.exception("{}: model load failed", self.service)
            self.error = _describe(exc)
            self.state = ModelState.FAILED
        else:
            self.backend = backend
            self.load_seconds = round(time.monotonic() - t0, 3)
            self.state = ModelState.READY
            logger.info("{}: model ready in {:.1f}s", self.service, self.load_seconds)
        finally:
            self._load_lock.release()
            self._settled.set()

    def stop(self) -> None:
        self.worker.stop()

    def wait_ready(self, timeout: float) -> bool:
        self._settled.wait(timeout)
        return self.state is ModelState.READY

    def require_ready(self) -> Any:
        backend = self.backend
        if backend is not None:  # also while a reload is in progress
            return backend
        if self.state is ModelState.FAILED:
            raise ApiError(503, "model_failed", f"Model failed to load: {self.error}")
        raise ApiError(
            503,
            "model_not_ready",
            "Model is still loading",
            headers={"Retry-After": "5"},
        )

    # --- reload -------------------------------------------------------------

    def reload(
        self, backend_factory: Callable[[], Any], timeout_s: float = 600.0
    ) -> None:
        """Load a new backend and swap it in; blocks until done.

        The new backend is built and loaded on a loader thread while the old
        one keeps serving (``state`` is LOADING meanwhile). On success it is
        swapped in on the inference thread, between two jobs. On failure or
        timeout the old backend stays, the previous state is restored and the
        exception is re-raised (``TimeoutError`` on timeout).
        """
        if not self._started:
            raise RuntimeError(f"{self.service}: runtime is not started")
        deadline = time.monotonic() + timeout_s
        if not self._load_lock.acquire(timeout=max(timeout_s, 0.0)):
            raise TimeoutError(f"{self.service}: another model load is still running")
        previous = self.state
        self.state = ModelState.LOADING
        t0 = time.monotonic()
        owns_lock = True
        try:
            backend = self._load_off_thread(backend_factory, deadline, timeout_s)
            self._swap(backend, deadline, timeout_s)
        except BaseException as exc:
            if isinstance(exc, _LoadAbandoned):
                owns_lock = False  # the loader thread releases it when done
            logger.warning(
                "{}: model reload failed, keeping the previous model: {}",
                self.service,
                _describe(exc),
            )
            if previous is ModelState.FAILED:
                self.error = _describe(exc)
            self.state = previous
            raise
        else:
            self.error = None
            self.load_seconds = round(time.monotonic() - t0, 3)
            self.state = ModelState.READY
            logger.info(
                "{}: model reloaded in {:.1f}s", self.service, self.load_seconds
            )
        finally:
            if owns_lock:
                self._load_lock.release()

    def _load_off_thread(
        self, factory: Callable[[], Any], deadline: float, timeout_s: float
    ) -> Any:
        """Build + load on a loader thread; wait for it until ``deadline``.

        On timeout the load is abandoned and the loader thread takes over
        ``_load_lock``: it releases it (and drops the backend) only when the
        load really ends, so no second load can run next to it.
        """
        loaded: concurrent.futures.Future = concurrent.futures.Future()
        handoff = threading.Lock()
        abandoned = False

        def load() -> None:
            try:
                loaded.set_result(self._build(factory))
            except Exception as exc:  # noqa: BLE001 — re-raised by reload()
                loaded.set_exception(exc)
            finally:
                with handoff:
                    if abandoned:
                        logger.info(
                            "{}: abandoned model load ended; discarded", self.service
                        )
                        self._load_lock.release()

        threading.Thread(
            target=load, name=f"{self.service}-loader", daemon=True
        ).start()
        try:
            return loaded.result(timeout=max(deadline - time.monotonic(), 0.0))
        except concurrent.futures.TimeoutError:
            with handoff:
                if not loaded.done():
                    abandoned = True
            if not abandoned:  # finished right at the deadline
                return loaded.result()
            raise _LoadAbandoned(
                f"{self.service}: model load did not finish within {timeout_s}s"
            ) from None

    def _swap(self, backend: Any, deadline: float, timeout_s: float) -> None:
        def set_backend() -> None:
            self.backend = backend

        fut = self.worker.submit(
            set_backend, priority=Priority.FINAL, bypass_limit=True
        )
        try:
            fut.result(timeout=max(deadline - time.monotonic(), 0.0))
        except concurrent.futures.TimeoutError:
            if fut.cancel():
                raise TimeoutError(
                    f"{self.service}: model swap did not happen within {timeout_s}s"
                ) from None
            fut.result()  # already running: a single assignment

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
        return self.worker.submit(
            fn, *args, priority=priority, bypass_limit=bypass_limit, **kwargs
        )

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
        return self.worker.submit_iter(
            gen_factory,
            *args,
            priority=priority,
            on_item=on_item,
            cancel=cancel,
            **kwargs,
        )

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
