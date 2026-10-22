"""Single inference thread with a priority queue (serialises all model access).

Plain jobs (``submit``) run to completion in one go. Generator jobs
(``submit_iter``) are stepped cooperatively: after every item the job is
re-queued as a continuation with a fresh sequence number, so anything of a
higher priority submitted meanwhile runs before the next step.
"""

from __future__ import annotations

import concurrent.futures
import itertools
import queue
import threading
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Generic, Iterator, Optional, TypeVar

from loguru import logger

T = TypeVar("T")

# Queue item: (priority, seq, kind, payload). seq is unique, so tuples never
# compare beyond it: strict priority order, FIFO within one priority.
_JOB = "job"
_ITER = "iter"
_STOP = "stop"


class Priority(IntEnum):
    FINAL = 0  # realtime ASR finals
    REQUEST = 1  # ordinary HTTP requests
    PARTIAL = 2  # realtime ASR partials (droppable)
    BATCH = 3  # long generator jobs, stepped between everything else


class WorkerBusy(RuntimeError):
    """Raised when the queue is full or the worker is stopping."""


class CancelledJob(RuntimeError):
    """Set on a ``submit_iter`` future when its cancel event fired."""


@dataclass(slots=True)
class _Job:
    fut: concurrent.futures.Future
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict


@dataclass(slots=True)
class _IterJob(Generic[T]):
    fut: concurrent.futures.Future[list[T]]
    factory: Callable[..., Iterator[T]]
    args: tuple
    kwargs: dict
    priority: int
    on_item: Optional[Callable[[T], None]]
    cancel: Optional[threading.Event]
    gen: Optional[Iterator[T]] = None  # None until the first step
    items: list[T] = field(default_factory=list)


def _close(gen: Optional[Iterator[Any]]) -> None:
    close = getattr(gen, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception as exc:  # noqa: BLE001 — cleanup must not mask the cause
        logger.warning("closing inference generator failed: {}", exc)


class InferenceWorker:
    def __init__(self, max_pending: int, name: str = "inference") -> None:
        self._max_pending = max_pending
        self._name = name
        self._queue: queue.PriorityQueue[tuple] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._lock = threading.Lock()
        self._pending = 0
        self._stopping = False
        self._thread: Optional[threading.Thread] = None

    @property
    def pending(self) -> int:
        """Jobs queued plus the job currently running (a generator job = 1)."""
        with self._lock:
            return self._pending

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    # --- submission ---------------------------------------------------------

    def _enqueue(self, priority: int, kind: str, payload: Any, bypass: bool) -> None:
        # put() under the lock: stop() sets _stopping and then drains, so no
        # item can slip into the queue behind the drain.
        with self._lock:
            if self._stopping:
                raise WorkerBusy("worker is stopping")
            if not bypass and self._pending >= self._max_pending:
                raise WorkerBusy(
                    f"{self._pending} jobs pending (limit {self._max_pending})"
                )
            self._pending += 1
            self._queue.put((priority, next(self._seq), kind, payload))

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        priority: Priority = Priority.REQUEST,
        bypass_limit: bool = False,
        **kwargs: Any,
    ) -> concurrent.futures.Future:
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self._enqueue(int(priority), _JOB, _Job(fut, fn, args, kwargs), bypass_limit)
        return fut

    def submit_iter(
        self,
        gen_factory: Callable[..., Iterator[T]],
        *args: Any,
        priority: Priority = Priority.BATCH,
        on_item: Callable[[T], None] | None = None,
        cancel: threading.Event | None = None,
        bypass_limit: bool = False,
        **kwargs: Any,
    ) -> concurrent.futures.Future[list[T]]:
        """Run ``gen_factory(*args, **kwargs)`` one item per scheduling step.

        The factory, every ``next()`` and ``on_item`` run on the worker thread.
        The whole job holds one ``pending`` slot until it finishes.
        """
        fut: concurrent.futures.Future[list[T]] = concurrent.futures.Future()
        job = _IterJob(fut, gen_factory, args, kwargs, int(priority), on_item, cancel)
        self._enqueue(job.priority, _ITER, job, bypass_limit)
        return fut

    # --- worker thread ------------------------------------------------------

    def _done(self) -> None:
        with self._lock:
            self._pending -= 1

    def _run(self) -> None:
        while True:
            _, _, kind, payload = self._queue.get()
            if kind == _STOP:
                return
            if kind == _JOB:
                self._run_job(payload)
            else:
                self._step(payload)

    def _run_job(self, job: _Job) -> None:
        # The slot is released before the future resolves, so a caller that
        # waited on the result already sees the updated ``pending``.
        if not job.fut.set_running_or_notify_cancel():
            self._done()
            return
        try:
            result = job.fn(*job.args, **job.kwargs)
        except Exception as exc:  # noqa: BLE001 — propagated to the caller
            logger.debug("inference job failed: {}", exc)
            self._done()
            job.fut.set_exception(exc)
        else:
            self._done()
            job.fut.set_result(result)

    def _step(self, job: _IterJob) -> None:
        if job.gen is None and not job.fut.set_running_or_notify_cancel():
            self._done()
            return
        if job.cancel is not None and job.cancel.is_set():
            _close(job.gen)
            self._done()
            job.fut.set_exception(CancelledJob())
            return
        # Only next() may finish the job via StopIteration; a StopIteration
        # leaking from the factory or on_item is an error like any other.
        try:
            if job.gen is None:
                job.gen = job.factory(*job.args, **job.kwargs)
            try:
                item = next(job.gen)
            except StopIteration:
                self._done()
                job.fut.set_result(job.items)
                return
            if job.on_item is not None:
                job.on_item(item)
        except Exception as exc:  # noqa: BLE001 — propagated to the caller
            logger.debug("inference generator failed: {!r}", exc)
            _close(job.gen)
            self._done()
            job.fut.set_exception(exc)
            return
        job.items.append(item)
        # Continuation: same priority, fresh seq; keeps the slot it already has.
        with self._lock:
            if not self._stopping:
                self._queue.put((job.priority, next(self._seq), _ITER, job))
                return
        self._fail_stopping(job)

    def _fail_stopping(self, job: _IterJob) -> None:
        _close(job.gen)
        self._done()
        job.fut.set_exception(WorkerBusy("stopping"))

    # --- shutdown -----------------------------------------------------------

    def stop(self, timeout: float = 5.0) -> None:
        """Reject new work, drop queued work, let the running job finish."""
        with self._lock:
            self._stopping = True
        while True:
            try:
                _, _, kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == _STOP:
                continue
            if kind == _ITER and payload.gen is not None:
                self._fail_stopping(payload)  # queued continuation
            else:
                payload.fut.cancel()
                self._done()
        self._queue.put((-1, next(self._seq), _STOP, None))
        if self._thread is not None:
            self._thread.join(timeout)
