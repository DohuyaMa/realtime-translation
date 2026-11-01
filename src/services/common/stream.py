"""``WS /v1/audio/stream`` plumbing of rt-asr.

Protocol (spec 2026-10-31 §3, 2026-10-06 §6.2): the client sends
``start{language, sample_rate: 16000, prompt?, t0?}``, binary PCM16LE mono
frames, ``stop``. The server answers ``ready{session_id, model}``, then
``final``/``partial``/``endpoint`` from the service's ``StreamSession``,
``error{code, state, message}`` and ``done``; every message carries
``audio_end`` (seconds, ``t0`` + samples received).

The service-specific part is a ``StreamSession`` (pure, no asyncio): ``feed``
runs on the event loop and must be cheap; ``step``/``finish``/``warm_up`` run
on the inference thread. Ticks are latest-wins: one step in flight per
session, ticks that fall inside a running step are skipped (counted).

Lifecycle: bad token -> close 1008 before accept; ``max_sessions`` reached ->
close 1013 before accept; model not ready (after a lazy load, like
``runtime.acquire``) -> ``error model_not_ready`` + close 1013; an open
session blocks the idle unload (``runtime.hold``); unload/reload ->
``error model_unloaded`` + close 1012; ``stop`` -> remaining finals,
``done``, close 1000.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from fastapi import APIRouter, WebSocket
from loguru import logger
from starlette.websockets import WebSocketDisconnect

from .auth import ws_authorized
from .errors import ApiError
from .runtime import ServiceRuntime
from .worker import Priority, WorkerBusy

SAMPLE_RATE = 16000

CLOSE_NORMAL = 1000
CLOSE_UNSUPPORTED = 1003
CLOSE_POLICY = 1008
CLOSE_INTERNAL = 1011
CLOSE_RESTART = 1012
CLOSE_TRY_AGAIN = 1013


class StreamError(Exception):
    """Refuses the session: ``error{code}`` + close ``close_code``."""

    def __init__(
        self, code: str, message: str, close_code: int = CLOSE_UNSUPPORTED
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.close_code = close_code


@dataclass(frozen=True)
class StreamStart:
    language: Optional[str]
    prompt: Optional[str]
    t0: float  # client timeline of the first sample, s (the edge timeline)

    @classmethod
    def parse(cls, msg: Dict[str, Any]) -> "StreamStart":
        if msg.get("type") != "start":
            raise StreamError("protocol_error", "the first message must be start")
        rate = msg.get("sample_rate", SAMPLE_RATE)
        if rate != SAMPLE_RATE:
            raise StreamError(
                "unsupported_sample_rate",
                f"sample_rate must be {SAMPLE_RATE} (resample on the client)",
            )
        language = msg.get("language")
        prompt = msg.get("prompt")
        t0 = msg.get("t0", 0.0)
        if language is not None and not isinstance(language, str):
            raise StreamError("invalid_request", "language must be a string")
        if prompt is not None and not isinstance(prompt, str):
            raise StreamError("invalid_request", "prompt must be a string")
        if isinstance(t0, bool) or not isinstance(t0, (int, float)) or t0 < 0:
            raise StreamError("invalid_request", "t0 must be a number >= 0")
        return cls(language or None, prompt or None, float(t0))


class StreamSession(Protocol):
    """One stream's recognizer state (service specific, no asyncio)."""

    @property
    def audio_end(self) -> float:
        """t0 + seconds of audio fed so far."""
        ...

    def feed(self, pcm: bytes) -> bool:
        """Event loop: take PCM16LE bytes; True = step now (endpoint)."""
        ...

    def pending(self) -> bool:
        """There is fed audio a step would process."""
        ...

    def step(self) -> List[dict]:
        """Inference thread: process the fed audio; messages to send."""
        ...

    def finish(self) -> List[dict]:
        """Inference thread, after ``stop``: everything left as finals."""
        ...

    def warm_up(self) -> None:
        """Inference thread: a dummy decode (1 s of silence) after ready."""
        ...

    def stats(self) -> dict:
        """Service-specific counters for /status and the close log."""
        ...


# (start, backend) -> session; raises StreamError to refuse the start.
SessionFactory = Callable[[StreamStart, Any], StreamSession]


@dataclass
class TickStats:
    ticks: int = 0
    skipped: int = 0
    busy: int = 0  # worker queue full: tick dropped
    step_ms: List[float] = field(default_factory=list)

    def add(self, ms: float, tick_s: float) -> None:
        self.ticks += 1
        self.step_ms.append(ms)
        # latest-wins: ticks that fell inside this step never ran
        self.skipped += max(0, int(ms / 1000.0 / tick_s - 1e-9))

    def as_dict(self) -> dict:
        ms = sorted(self.step_ms)
        return {
            "ticks": self.ticks,
            "skipped_ticks": self.skipped,
            "busy_ticks": self.busy,
            "step_ms_p50": round(ms[len(ms) // 2], 1) if ms else None,
            "step_ms_max": round(ms[-1], 1) if ms else None,
        }


class StreamHub:
    """Open sessions of one service: limit, ``/status.sessions``."""

    def __init__(
        self,
        runtime: ServiceRuntime,
        factory: SessionFactory,
        *,
        max_sessions: int,
        tick_s: float,
    ) -> None:
        self.runtime = runtime
        self.factory = factory
        self.max_sessions = max_sessions
        self.tick_s = tick_s
        self._reserved = 0
        self._open: Dict[str, tuple[StreamSession, TickStats]] = {}
        self.opened = 0

    def reserve(self) -> bool:
        if self._reserved >= self.max_sessions:
            return False
        self._reserved += 1
        return True

    def unreserve(self) -> None:
        self._reserved -= 1

    def add(self, sid: str, session: StreamSession, stats: TickStats) -> None:
        self._open[sid] = (session, stats)
        self.opened += 1

    def remove(self, sid: str) -> None:
        self._open.pop(sid, None)

    def status(self) -> dict:
        return {
            "active": self._reserved,
            "max": self.max_sessions,
            "opened": self.opened,
            "tick_ms": round(self.tick_s * 1000),
            "open": [
                {"session_id": sid, **stats.as_dict(), **session.stats()}
                for sid, (session, stats) in self._open.items()
            ],
        }


class _Closed(Exception):
    """The connection ended (close already sent or the peer is gone)."""


class _Dropped(Exception):
    """The model was unloaded/reloaded while the session waited on it."""


class _Connection:
    def __init__(self, ws: WebSocket, hub: StreamHub) -> None:
        self.ws = ws
        self.hub = hub
        self.runtime = hub.runtime
        self.dropped = asyncio.Event()
        self.wake = asyncio.Event()
        self.stopping = False
        self.session: Optional[StreamSession] = None
        self.stats = TickStats()

    # --- sending ------------------------------------------------------------

    async def send(self, msg: dict) -> None:
        msg["audio_end"] = (
            round(self.session.audio_end, 3) if self.session is not None else 0.0
        )
        await self.ws.send_json(msg)

    async def fail(self, code: str, message: str, close_code: int) -> None:
        state = self.runtime.snapshot()[0].value
        logger.info(
            "{} stream: {} ({}), close {}",
            self.runtime.service,
            code,
            message,
            close_code,
        )
        try:
            await self.send(
                {"type": "error", "code": code, "state": state, "message": message}
            )
            await self.ws.close(code=close_code)
        except (WebSocketDisconnect, RuntimeError):
            pass  # the peer is already gone

    # --- inference ----------------------------------------------------------

    async def run(self, fn: Callable[[], Any], *, bypass: bool) -> Any:
        fut = self.runtime.submit_threadsafe(
            fn, priority=Priority.FINAL, bypass_limit=bypass
        )
        return await asyncio.wrap_future(fut)

    # --- tasks --------------------------------------------------------------

    async def receive_start(self) -> StreamStart:
        msg = await self.ws.receive()
        if msg["type"] == "websocket.disconnect":
            raise _Closed()
        if msg.get("text") is None:
            raise StreamError("protocol_error", "the first message must be start")
        try:
            data = json.loads(msg["text"])
        except ValueError as exc:
            raise StreamError("protocol_error", f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise StreamError("protocol_error", "messages must be JSON objects")
        return StreamStart.parse(data)

    async def receive(self, session: StreamSession) -> None:
        """Feed audio until ``stop`` (returns) or the peer leaves (_Closed)."""
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                raise _Closed()
            data = msg.get("bytes")
            if data is not None:
                if session.feed(data):
                    self.wake.set()
                continue
            try:
                body = json.loads(msg.get("text") or "")
            except ValueError as exc:
                raise StreamError("protocol_error", f"invalid JSON: {exc}") from exc
            kind = body.get("type") if isinstance(body, dict) else None
            if kind == "stop":
                return
            raise StreamError("protocol_error", f"unexpected message type {kind!r}")

    async def pump(self, session: StreamSession) -> None:
        """Latest-wins ticks until ``stopping``; one step in flight."""
        tick = self.hub.tick_s
        await self.run(session.warm_up, bypass=True)
        loop = asyncio.get_running_loop()
        last = loop.time()
        while not self.stopping:
            wait = max(0.0, tick - (loop.time() - last))
            try:
                await asyncio.wait_for(self.wake.wait(), wait)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            if self.stopping:
                return
            last = loop.time()
            if not session.pending():
                continue
            t = time.perf_counter()
            try:
                msgs = await self.run(session.step, bypass=False)
            except WorkerBusy:
                self.stats.busy += 1
                continue
            self.stats.add((time.perf_counter() - t) * 1000.0, tick)
            for m in msgs:
                await self.send(m)

    # --- main ---------------------------------------------------------------

    async def serve(self) -> None:
        try:
            start = await self.receive_start()
        except StreamError as exc:
            await self.fail(exc.code, exc.message, exc.close_code)
            return
        try:
            backend = await self.runtime.acquire()
        except ApiError as exc:
            await self.fail("model_not_ready", exc.message, CLOSE_TRY_AGAIN)
            return
        try:
            session = self.hub.factory(start, backend)
        except StreamError as exc:
            await self.fail(exc.code, exc.message, exc.close_code)
            return
        self.session = session
        sid = str(uuid.uuid4())
        self.hub.add(sid, session, self.stats)
        t_open = time.monotonic()
        try:
            await self.send(
                {"type": "ready", "session_id": sid, "model": backend.model_id}
            )
            await self._loop(session)
        finally:
            self.hub.remove(sid)
            logger.info(
                "{} stream {} closed after {:.1f}s audio / {:.1f}s wall: {} {}",
                self.runtime.service,
                sid[:8],
                session.audio_end - start.t0,
                time.monotonic() - t_open,
                self.stats.as_dict(),
                session.stats(),
            )

    async def _loop(self, session: StreamSession) -> None:
        recv = asyncio.create_task(self.receive(session))
        pump = asyncio.create_task(self.pump(session))
        drop = asyncio.create_task(self.dropped.wait())
        tasks = {recv, pump, drop}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if drop.done():
                await self.fail("model_unloaded", "model unloaded", CLOSE_RESTART)
                return
            if recv.done():
                exc = recv.exception()
                if isinstance(exc, _Closed):
                    return
                if isinstance(exc, StreamError):
                    await self.fail(exc.code, exc.message, exc.close_code)
                    return
                if exc is not None:
                    raise exc
                # stop: let the running step deliver, then flush the rest
                self.stopping = True
                self.wake.set()
                await self._race(pump, drop)
                await pump
                msgs = await self._race(
                    asyncio.ensure_future(self.run(session.finish, bypass=True)), drop
                )
                for m in msgs:
                    await self.send(m)
                await self.send({"type": "done"})
                await self.ws.close(code=CLOSE_NORMAL)
                return
            await pump  # pump only ends early by raising
        except (_Closed, WebSocketDisconnect):
            return
        except _Dropped:
            await self.fail("model_unloaded", "model unloaded", CLOSE_RESTART)
        except Exception as exc:  # noqa: BLE001 — reported to the client
            if self.dropped.is_set() or self.runtime.backend is None:
                await self.fail("model_unloaded", "model unloaded", CLOSE_RESTART)
            else:
                logger.exception("{} stream failed", self.runtime.service)
                await self.fail("internal_error", repr(exc), CLOSE_INTERNAL)
        finally:
            for task in tasks:
                task.cancel()

    async def _race(self, fut: "asyncio.Future[Any]", drop: "asyncio.Task[Any]") -> Any:
        """``fut``'s result, or _Dropped if the model goes first."""
        await asyncio.wait({fut, drop}, return_when=asyncio.FIRST_COMPLETED)
        if not fut.done():
            fut.cancel()
            raise _Dropped()
        return fut.result()


async def serve_stream(ws: WebSocket, hub: StreamHub, token: Optional[str]) -> None:
    if not ws_authorized(ws, token):
        await ws.close(code=CLOSE_POLICY)
        return
    if not hub.reserve():
        await ws.close(code=CLOSE_TRY_AGAIN)
        return
    conn = _Connection(ws, hub)
    loop = asyncio.get_running_loop()

    def on_drop() -> None:  # any thread: the model is being unloaded
        loop.call_soon_threadsafe(conn.dropped.set)

    handle = hub.runtime.hold(on_drop)
    try:
        await ws.accept()
        await conn.serve()
    except (_Closed, WebSocketDisconnect):
        pass
    finally:
        hub.runtime.release(handle)
        hub.unreserve()


def build_stream_router(hub: StreamHub, token: Optional[str]) -> APIRouter:
    """``WS /audio/stream`` (mounted under /v1 via ``ws_routers``)."""
    router = APIRouter()

    @router.websocket("/audio/stream")
    async def stream(websocket: WebSocket) -> None:
        await serve_stream(websocket, hub, token)

    return router
