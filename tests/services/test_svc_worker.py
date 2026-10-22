import concurrent.futures
import inspect
import threading

import pytest

from src.services.common.worker import (
    CancelledJob,
    InferenceWorker,
    Priority,
    WorkerBusy,
)

pytestmark = pytest.mark.timeout(10)


@pytest.fixture
def worker():
    w = InferenceWorker(max_pending=10)
    w.start()
    yield w
    w.stop()


def _gate(worker, priority=Priority.REQUEST):
    started, release = threading.Event(), threading.Event()

    def blocker():
        started.set()
        release.wait(5)
        return "gate"

    fut = worker.submit(blocker, priority=priority, bypass_limit=True)
    assert started.wait(5)
    return release, fut


def test_priority_values():
    assert [int(p) for p in Priority] == [0, 1, 2, 3]
    assert [p.name for p in Priority] == ["FINAL", "REQUEST", "PARTIAL", "BATCH"]
    assert issubclass(CancelledJob, RuntimeError)
    assert issubclass(WorkerBusy, RuntimeError)


def test_runs_jobs_and_returns_results(worker):
    assert worker.submit(lambda a, b: a + b, 2, 3).result(5) == 5


def test_priority_order_then_fifo(worker):
    order = []
    release, _ = _gate(worker)
    futs = [
        worker.submit(order.append, "batch", priority=Priority.BATCH),
        worker.submit(order.append, "partial", priority=Priority.PARTIAL),
        worker.submit(order.append, "request-1", priority=Priority.REQUEST),
        worker.submit(order.append, "final", priority=Priority.FINAL),
        worker.submit(order.append, "request-2", priority=Priority.REQUEST),
    ]
    release.set()
    concurrent.futures.wait(futs, timeout=5)
    assert order == ["final", "request-1", "request-2", "partial", "batch"]


def test_limit_and_bypass():
    w = InferenceWorker(max_pending=2)
    w.start()
    try:
        release, _ = _gate(w)
        w.submit(lambda: None)
        assert w.pending == 2
        with pytest.raises(WorkerBusy):
            w.submit(lambda: None)
        bypass = w.submit(lambda: "ok", bypass_limit=True)
        release.set()
        assert bypass.result(5) == "ok"
    finally:
        w.stop()


def test_cancelled_job_is_skipped(worker):
    called = []
    release, _ = _gate(worker)
    fut = worker.submit(called.append, 1)
    assert fut.cancel()
    release.set()
    worker.submit(lambda: None).result(5)
    assert called == []
    assert worker.pending == 0


def test_exception_propagates(worker):
    def boom():
        raise ValueError("bad")

    with pytest.raises(ValueError, match="bad"):
        worker.submit(boom).result(5)
    assert worker.submit(lambda: 1).result(5) == 1


def test_stop_cancels_pending_and_rejects_new():
    w = InferenceWorker(max_pending=10)
    w.start()
    release, gate = _gate(w)
    queued = w.submit(lambda: None)
    queued_iter = w.submit_iter(lambda: iter([1]))
    w.stop(timeout=0)  # gate job still running: queue is drained right away
    assert queued.cancelled()
    assert queued_iter.cancelled()
    with pytest.raises(WorkerBusy):
        w.submit(lambda: None)
    with pytest.raises(WorkerBusy):
        w.submit_iter(lambda: iter([1]))
    release.set()
    assert gate.result(5) == "gate"
    w.stop()


# --- submit_iter: cooperative generator stepping -----------------------------


def test_submit_iter_collects_items_in_order(worker):
    seen, threads = [], []

    def factory(n, start=1):
        threads.append(threading.current_thread().name)
        return iter(range(start, start + n))

    def on_item(item):
        threads.append(threading.current_thread().name)
        seen.append(item)

    fut = worker.submit_iter(factory, 3, start=1, on_item=on_item)
    assert fut.result(5) == [1, 2, 3]
    assert seen == [1, 2, 3]
    assert threads == ["inference"] * 4  # factory + 3 on_item calls
    assert worker.pending == 0


def test_submit_iter_empty_generator(worker):
    assert worker.submit_iter(lambda: iter(())).result(5) == []


def test_higher_priority_runs_between_batch_steps(worker):
    order, done = [], []
    in_step1, e1 = threading.Event(), threading.Event()

    def gen():
        in_step1.set()
        e1.wait(5)
        order.append("b1")
        yield 1
        order.append("b2")
        yield 2

    batch = worker.submit_iter(gen)
    batch.add_done_callback(lambda f: done.append("batch"))
    assert in_step1.wait(5)
    req = worker.submit(order.append, "req", priority=Priority.REQUEST)
    req.add_done_callback(lambda f: done.append("req"))
    e1.set()
    assert batch.result(5) == [1, 2]
    assert req.result(5) is None
    assert order == ["b1", "req", "b2"]
    assert done == ["req", "batch"]


def test_submit_iter_cancel_between_steps(worker):
    cancel = threading.Event()
    cleaned, made = [], []

    def gen():
        try:
            yield 1
            yield 2
        finally:
            cleaned.append(True)

    def factory():
        g = gen()
        made.append(g)
        return g

    fut = worker.submit_iter(factory, on_item=lambda item: cancel.set(), cancel=cancel)
    with pytest.raises(CancelledJob):
        fut.result(5)
    assert cleaned == [True]
    assert inspect.getgeneratorstate(made[0]) == inspect.GEN_CLOSED
    assert worker.pending == 0


def test_submit_iter_cancel_before_first_step(worker):
    cancel = threading.Event()
    cancel.set()
    called = []
    fut = worker.submit_iter(lambda: called.append(1) or iter([1]), cancel=cancel)
    with pytest.raises(CancelledJob):
        fut.result(5)
    assert called == []


def test_submit_iter_exception_mid_generator(worker):
    cleaned, made, seen = [], [], []

    def gen():
        try:
            yield 1
            raise ValueError("mid")
        finally:
            cleaned.append(True)

    def factory():
        g = gen()
        made.append(g)
        return g

    fut = worker.submit_iter(factory, on_item=seen.append)
    assert isinstance(fut.exception(5), ValueError)
    assert seen == [1]
    assert cleaned == [True]
    assert inspect.getgeneratorstate(made[0]) == inspect.GEN_CLOSED
    assert worker.pending == 0
    assert worker.submit(lambda: "alive").result(5) == "alive"


def test_submit_iter_on_item_exception_closes_generator(worker):
    cleaned = []

    def gen():
        try:
            yield 1
            yield 2
        finally:
            cleaned.append(True)

    def on_item(item):
        raise KeyError("sink")

    fut = worker.submit_iter(gen, on_item=on_item)
    assert isinstance(fut.exception(5), KeyError)
    assert cleaned == [True]


def test_submit_iter_factory_exception(worker):
    def factory():
        raise OSError("no model")

    assert isinstance(worker.submit_iter(factory).exception(5), OSError)
    assert worker.pending == 0


def test_submit_iter_on_item_stop_iteration_is_an_error(worker):
    cleaned, made = [], []

    def gen():
        try:
            yield 1
            yield 2
        finally:
            cleaned.append(True)

    def factory():
        g = gen()
        made.append(g)
        return g

    def on_item(item):
        raise StopIteration

    fut = worker.submit_iter(factory, on_item=on_item)
    assert isinstance(fut.exception(5), StopIteration)
    assert cleaned == [True]
    assert inspect.getgeneratorstate(made[0]) == inspect.GEN_CLOSED
    assert worker.pending == 0


def test_submit_iter_factory_stop_iteration_is_an_error(worker):
    def factory():
        raise StopIteration

    assert isinstance(worker.submit_iter(factory).exception(5), StopIteration)
    assert worker.pending == 0


def test_submit_iter_pending_counts_one_slot():
    w = InferenceWorker(max_pending=1)
    w.start()
    try:
        in1, go1 = threading.Event(), threading.Event()
        in2, go2 = threading.Event(), threading.Event()

        def gen():
            in1.set()
            go1.wait(5)
            yield 1
            in2.set()
            go2.wait(5)
            yield 2

        fut = w.submit_iter(gen)
        assert in1.wait(5)
        assert w.pending == 1
        with pytest.raises(WorkerBusy):
            w.submit(lambda: None)
        go1.set()
        assert in2.wait(5)  # running a continuation: still one slot
        assert w.pending == 1
        with pytest.raises(WorkerBusy):
            w.submit(lambda: None)
        go2.set()
        assert fut.result(5) == [1, 2]
        assert w.pending == 0
    finally:
        w.stop()


def test_stop_with_queued_continuation_fails_future():
    w = InferenceWorker(max_pending=10)
    w.start()
    cleaned, made = [], []
    in_step1, go = threading.Event(), threading.Event()

    def gen():
        try:
            in_step1.set()
            go.wait(5)
            yield 1
            yield 2
        finally:
            cleaned.append(True)

    def factory():
        g = gen()
        made.append(g)
        return g

    fut = w.submit_iter(factory)
    assert in_step1.wait(5)
    # Queued now, runs right after step 1 — before the BATCH continuation.
    blocker_started, release = threading.Event(), threading.Event()

    def blocker():
        blocker_started.set()
        release.wait(5)

    gate = w.submit(blocker, priority=Priority.REQUEST)
    go.set()
    assert blocker_started.wait(5)  # step 1 done, continuation is queued
    w.stop(timeout=0)
    exc = fut.exception(5)
    assert isinstance(exc, WorkerBusy)
    assert "stopping" in str(exc)
    assert cleaned == [True]
    assert inspect.getgeneratorstate(made[0]) == inspect.GEN_CLOSED
    assert w.pending == 1  # only the running gate job
    release.set()
    gate.result(5)
    w.stop()
    assert w.pending == 0


def test_stop_during_step_fails_iter_after_step():
    w = InferenceWorker(max_pending=10)
    w.start()
    cleaned, made = [], []
    in_step1, go = threading.Event(), threading.Event()

    def gen():
        try:
            in_step1.set()
            go.wait(5)
            yield 1
            yield 2
        finally:
            cleaned.append(True)

    def factory():
        g = gen()
        made.append(g)
        return g

    fut = w.submit_iter(factory)
    assert in_step1.wait(5)
    w.stop(timeout=0)  # nothing queued yet: the step is running
    go.set()
    assert isinstance(fut.exception(5), WorkerBusy)  # not re-queued
    assert cleaned == [True]
    assert inspect.getgeneratorstate(made[0]) == inspect.GEN_CLOSED
    w.stop()
    assert w.pending == 0
