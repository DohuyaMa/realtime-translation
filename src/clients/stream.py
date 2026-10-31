"""Edge client of ``WS /v1/audio/stream`` (rt-asr / rt-vosk), spec 2026-10-31
§3 / §3a.

One thread per stream. The pipeline ``feed``s 16 kHz PCM16 at any time: it
goes into a ring buffer (the last ``ring_s`` seconds) and from there to the
server in 100 ms frames once the stream is open. Audio fed while the stream
is still opening (warm-up: the handshake loads the model, an unloaded model
gets ``POST /model/load`` first) waits in the ring and is sent at once after
``ready`` - capture never waits for a service.

Timeline: sample 0 is the first fed sample; ``start.t0`` and every time the
server sends are seconds on it, so rt-asr and rt-vosk words share one scale.

Failover: a broken connection (refused handshake, closed socket, a send
queue of more than ``send_queue_s`` seconds of audio, an ``error``) marks
the endpoint down in the ``EndpointPool`` and the next one is opened; the
audio from the end of the last committed word on is replayed from the ring
and finals that end at or before it are dropped (dedupe by ``end``). With no
endpoint left the client says so (``failed``) and retries every
``RETRY_S`` while audio keeps going into the ring.

Events (``on_event(kind, msg)``, on the client thread): the server's
``final`` (deduped) / ``partial`` / ``endpoint`` and, from the client,
``ready{url, ms, loaded, model, reconnect}``, ``down{url, error}`` (lost,
reconnecting) and ``failed{error}`` (no endpoint available).

``stop``: the rest of the ring is sent, then ``stop``; the remaining finals
arrive before ``done`` (waited at most ``STOP_TIMEOUT_S``).
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Tuple

import numpy as np
from loguru import logger

from .base import ServiceError
from .model_status import ensure_model_ready

RATE = 16000
FRAME = RATE // 10  # 100 ms per binary message
RING_S = 30.0
SEND_QUEUE_S = 5.0
STOP_TIMEOUT_S = 2.0
RETRY_S = 1.0
OPEN_TIMEOUT_S = 10.0
# the handshake may wait for a lazy model load on the server
READY_TIMEOUT_S = 600.0
RECV_POLL_S = 0.02
# close codes of the server (services/common/stream.py)
CLOSE_RESTART = 1012

Event = Callable[[str, Dict[str, Any]], None]


class _Down(Exception):
    """This connection is over; ``mark`` = the endpoint goes down in the pool."""

    def __init__(self, message: str, mark: bool = True) -> None:
        super().__init__(message)
        self.mark = mark


def ws_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base + "/v1/audio/stream"


def _default_connect(url: str, headers: Dict[str, str]) -> Any:
    from websockets.sync.client import connect

    return connect(
        url,
        additional_headers=headers,
        open_timeout=OPEN_TIMEOUT_S,
        max_size=None,
        compression=None,
    )


class StreamClient:
    def __init__(
        self,
        kind: str,
        pool: Any,
        *,
        language: Optional[str],
        on_event: Event,
        prompt: Optional[str] = None,
        ring_s: float = RING_S,
        send_queue_s: float = SEND_QUEUE_S,
        connect: Callable[[str, Dict[str, str]], Any] = _default_connect,
        retry_s: float = RETRY_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.kind = kind
        self.language = language
        self.prompt = prompt
        self._pool = pool
        self._on_event = on_event
        self._ring_max = int(ring_s * RATE)
        self._queue_max = int(send_queue_s * RATE)
        self._connect = connect
        self._retry_s = retry_s
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._ring: Deque[Tuple[int, np.ndarray]] = deque()
        self._ring_start = 0  # sample of the oldest kept audio
        self._end = 0  # samples fed
        self._lost = 0  # samples dropped from the ring before they were sent
        self._sent: Optional[int] = None  # None = not connected
        self._committed_end = 0.0  # end of the last final word, s
        self._stopping = False
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        self._t_start = 0.0
        self.url: Optional[str] = None
        self.state = "idle"  # idle | opening | open | down | done
        self.reconnects = 0
        self.ready_ms: Optional[float] = None

    # --- pipeline side ------------------------------------------------------

    def start(self) -> None:
        self._t_start = self._clock()
        self.state = "opening"
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"rt-{self.kind}-stream"
        )
        self._thread.start()

    def feed(self, pcm: np.ndarray) -> None:
        pcm = np.asarray(pcm, dtype=np.int16).reshape(-1)
        if not pcm.size:
            return
        with self._lock:
            self._ring.append((self._end, pcm))
            self._end += pcm.size
            self._trim_ring()
        self._wake.set()

    def _trim_ring(self) -> None:
        """Caller holds the lock: keep at most ``ring_s`` of audio; audio
        dropped before it was sent (or replayed) is counted as lost."""
        prev = self._ring_start
        while self._ring and self._end - self._ring[0][0] > self._ring_max:
            start, chunk = self._ring[0]
            cut = min(chunk.size, self._end - self._ring_max - start)
            if cut >= chunk.size:
                self._ring.popleft()
            else:
                self._ring[0] = (start + cut, chunk[cut:])
            self._ring_start = start + cut
        need = int(self._committed_end * RATE) if self._sent is None else self._sent
        self._lost += max(0, self._ring_start - max(prev, need))

    def _slice(self, a: int, b: int) -> np.ndarray:
        """Caller holds the lock: ring samples [a, b)."""
        parts = []
        for start, chunk in self._ring:
            end = start + chunk.size
            if end <= a or start >= b:
                continue
            parts.append(chunk[max(0, a - start) : min(chunk.size, b - start)])
        return np.concatenate(parts) if parts else np.zeros(0, np.int16)

    def audio(self, t0: float, t1: float) -> np.ndarray:
        """Fed audio between t0 and t1 s while it is still in the ring."""
        with self._lock:
            return self._slice(int(t0 * RATE), int(t1 * RATE))

    @property
    def fed_s(self) -> float:
        return self._end / RATE

    def stop(self, timeout_s: float = STOP_TIMEOUT_S) -> None:
        """Send the rest, ``stop``, wait for the last finals and ``done``."""
        self._stopping = True
        self._wake.set()
        t = self._thread
        if t is None:
            return
        t.join(timeout=timeout_s + 1.0)
        if t.is_alive():
            logger.warning(
                "{} stream: no done {:.1f} s after stop", self.kind, timeout_s
            )
            self.close()

    def close(self) -> None:
        """Abort without waiting for the last finals."""
        self._stopping = self._closed = True
        self._wake.set()

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            unsent = 0 if self._sent is None else self._end - self._sent
        return {
            "url": self.url,
            "state": self.state,
            "ready_ms": self.ready_ms,
            "reconnects": self.reconnects,
            "fed_s": round(self.fed_s, 2),
            "unsent_s": round(unsent / RATE, 2),
            "lost_s": round(self._lost / RATE, 2),
        }

    # --- client thread --------------------------------------------------------

    def _emit(self, msg: Dict[str, Any]) -> None:
        try:
            self._on_event(self.kind, msg)
        except Exception as e:  # never kills the stream thread
            logger.exception("{} stream: event handler failed: {}", self.kind, e)

    def _run(self) -> None:
        first = True
        while not self._closed:
            if self._stopping and self._sent is None and not first:
                break
            try:
                url = self._pool.activate()
            except ServiceError as e:
                self.state = "down"
                logger.error(
                    "{} stream: {} - retry in {} s", self.kind, e, self._retry_s
                )
                self._emit({"type": "failed", "error": str(e)})
                if self._stopping:
                    break
                self._wake.wait(self._retry_s)
                self._wake.clear()
                continue
            self.url = url
            try:
                self._session(url, first)
                break  # stopped cleanly
            except _Down as e:
                with self._lock:
                    self._sent = None
                self.state = "down"
                self.reconnects += 1
                if e.mark:
                    self._pool.mark_down(url)
                logger.warning("{} stream on {} lost: {}", self.kind, url, e)
                self._emit({"type": "down", "url": url, "error": str(e)})
                first = False
                if self._stopping:
                    # the rest is lost: stop must not wait for another node
                    break
                if not e.mark:  # same endpoint again: not in a tight loop
                    self._wake.wait(0.2)
                    self._wake.clear()
        self.state = "done"
        with self._lock:
            sent = self._sent if self._sent is not None else self._ring_start
            unsent = max(0, self._end - sent)
        if unsent:
            logger.warning(
                "{} stream: {:.1f} s of audio never sent", self.kind, unsent / RATE
            )

    def _open(self, url: str) -> Tuple[Any, Dict[str, Any], int, bool]:
        """Handshake + start + ready; returns (ws, ready, replay sample,
        model loaded first)."""
        endpoint = self._pool.endpoints[url]
        loaded = False
        reloaded_token = False
        while True:
            if self._closed:
                raise _Down("closed", mark=False)
            try:
                ws = self._connect(ws_url(url), endpoint.credential.header())
            except Exception as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 403 and not reloaded_token:
                    # 403 = token refused or session limit (both close
                    # before accept): one retry with a fresh token
                    reloaded_token = True
                    if endpoint.credential.reload():
                        continue
                raise _Down(f"handshake failed: {type(e).__name__}: {e}")
            with self._lock:
                replay = max(self._ring_start, int(round(self._committed_end * RATE)))
                if self._committed_end * RATE < self._ring_start - 1:
                    logger.warning(
                        "{} stream: {:.1f} s after the last committed word"
                        " are no longer in the ring",
                        self.kind,
                        (self._ring_start - self._committed_end * RATE) / RATE,
                    )
            start = {
                "type": "start",
                "language": self.language,
                "sample_rate": RATE,
                "t0": replay / RATE,
            }
            if self.prompt:
                start["prompt"] = self.prompt
            try:
                ws.send(json.dumps(start))
                msg = json.loads(ws.recv(timeout=READY_TIMEOUT_S))
            except Exception as e:
                self._close_ws(ws)
                raise _Down(f"no ready: {type(e).__name__}: {e}")
            if msg.get("type") == "ready":
                return ws, msg, replay, loaded
            self._close_ws(ws)
            code = msg.get("code")
            if msg.get("type") == "error" and code == "model_not_ready" and not loaded:
                try:
                    ensure_model_ready(self.kind, self._pool.client(url))
                except ServiceError as e:
                    raise _Down(f"model not ready: {e}")
                loaded = True
                continue
            raise _Down(f"{code or msg.get('type')}: {msg.get('message', '')}")

    def _session(self, url: str, first: bool) -> None:
        self.state = "opening"
        t0 = self._clock()
        ws, ready, replay, loaded = self._open(url)
        ms = round((self._clock() - (self._t_start if first else t0)) * 1000.0, 1)
        with self._lock:
            self._sent = replay
        self.state = "open"
        if first:
            self.ready_ms = ms
        logger.info(
            "{} stream on {} ready in {} ms{} (session {}, replay from {:.2f} s)",
            self.kind,
            url,
            ms,
            " (model loaded first)" if loaded else "",
            str(ready.get("session_id", ""))[:8],
            replay / RATE,
        )
        self._emit(
            {
                "type": "ready",
                "url": url,
                "ms": ms,
                "loaded": loaded,
                "model": ready.get("model"),
                "reconnect": not first,
            }
        )
        try:
            self._pump(ws)
        finally:
            self._close_ws(ws)

    def _pump(self, ws: Any) -> None:
        while True:
            if self._closed:
                raise _Down("closed", mark=False)
            stopping = self._stopping
            self._send_audio(ws, flush=stopping)
            if stopping:
                with self._lock:
                    done = self._sent is not None and self._sent >= self._end
                if done:
                    self._finish(ws)
                    return
            try:
                raw = ws.recv(timeout=RECV_POLL_S)
            except TimeoutError:
                continue
            except Exception as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                raise _Down(f"closed ({code}): {e}", mark=code != CLOSE_RESTART)
            self._handle(raw)

    def _send_audio(self, ws: Any, flush: bool) -> None:
        while True:
            with self._lock:
                sent = self._sent if self._sent is not None else self._end
                if self._end - sent > self._queue_max:
                    raise _Down(
                        f"send queue {(self._end - sent) / RATE:.1f} s"
                        f" > {self._queue_max / RATE:.0f} s"
                    )
                n = min(FRAME, self._end - sent)
                if n <= 0 or (n < FRAME and not flush):
                    return
                frame = self._slice(sent, sent + n)
            try:
                ws.send(frame.astype("<i2").tobytes())
            except Exception as e:
                raise _Down(f"send failed: {type(e).__name__}: {e}")
            with self._lock:
                self._sent = sent + n

    def _finish(self, ws: Any) -> None:
        try:
            ws.send(json.dumps({"type": "stop"}))
        except Exception as e:
            raise _Down(f"stop failed: {e}", mark=False)
        deadline = self._clock() + STOP_TIMEOUT_S
        while True:
            left = deadline - self._clock()
            if left <= 0:
                logger.warning(
                    "{} stream: no done within {} s", self.kind, STOP_TIMEOUT_S
                )
                return
            try:
                raw = ws.recv(timeout=left)
            except TimeoutError:
                continue
            except Exception as e:
                logger.warning("{} stream closed before done: {}", self.kind, e)
                return
            msg = self._handle(raw)
            if msg is not None and msg.get("type") == "done":
                return

    def _handle(self, raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, str):
            return None
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind == "final":
            words = [
                w
                for w in msg.get("words") or ()
                if w["end"] > self._committed_end + 1e-3
            ]
            if not words:
                logger.debug("{} stream: replayed final dropped: {!r}", self.kind, msg)
                return msg
            if len(words) != len(msg.get("words") or ()):
                msg = dict(
                    msg,
                    words=words,
                    text=" ".join(w["word"] for w in words),
                    start=words[0]["start"],
                )
            self._committed_end = float(words[-1]["end"])
            self._emit(msg)
        elif kind in ("partial", "endpoint"):
            self._emit(msg)
        elif kind == "error":
            raise _Down(
                f"{msg.get('code')}: {msg.get('message', '')}",
                mark=msg.get("code") != "model_unloaded",
            )
        return msg

    @staticmethod
    def _close_ws(ws: Any) -> None:
        try:
            ws.close()
        except Exception:  # already closed
            pass
