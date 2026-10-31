"""App session on an llama worker, with an ordered failover list.

Owner design (2026-10-31): the app connects to workers directly; while an app
session lives the worker does not take Django tasks. No Django involved.

Worker contract (``Authorization: Token <token>``; the token comes from
service discovery, for the local worker with a host-local token file fallback):

- ``POST /app-session`` ``{"client", "ttl_s"}`` -> 201 ``{"session_id", "ttl_s"}``
- ``POST /app-session/<id>/heartbeat`` -> 200 ``{"ttl_s"}``; 404 = expired
- ``DELETE /app-session/<id>`` -> 204

``urls`` is a priority order (local worker first, then cluster nodes). The
session lives on the first worker that accepts it; on worker errors the
target fails over to the next one, and every heartbeat round probes the
preferred workers (``GET /health``) to return to the local one. Every switch
is logged at WARNING and exposed in ``snapshot()``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

import httpx
from loguru import logger

from .base import ServiceError
from .discovery import Endpoint

SERVICE = "llm"


@dataclass(frozen=True)
class Session:
    url: str
    id: str
    ttl_s: int


class WorkerApi:
    """HTTP calls of the worker app-session contract (one 401 re-read)."""

    def __init__(
        self,
        endpoints: dict[str, Endpoint],
        *,
        timeout_s: float = 5.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._endpoints = endpoints
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def _send(self, method: str, base: str, path: str, **kwargs: Any) -> httpx.Response:
        url = base + path
        headers = self._endpoints[base].credential.header()
        try:
            return self._http.request(method, url, headers=headers, **kwargs)
        except httpx.TimeoutException as e:
            raise ServiceError(SERVICE, "timeout", f"{url}: {e}") from e
        except httpx.TransportError as e:
            raise ServiceError(SERVICE, "unreachable", f"{url}: {e}") from e

    def _call(self, method: str, base: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._send(method, base, path, **kwargs)
        if resp.status_code == 401 and self._endpoints[base].credential.reload():
            resp = self._send(method, base, path, **kwargs)
        if resp.status_code in (401, 403):
            raise ServiceError(
                SERVICE, "auth", f"{base}{path}: HTTP {resp.status_code}"
            )
        return resp

    @staticmethod
    def _json(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise ServiceError(
                SERVICE, "http", f"invalid JSON: {resp.text[:200]}"
            ) from e
        if not isinstance(data, dict):
            raise ServiceError(SERVICE, "http", f"unexpected payload: {data!r:.200}")
        return data

    def open(self, base: str, client: str, ttl_s: int) -> Session:
        resp = self._call(
            "POST", base, "/app-session", json={"client": client, "ttl_s": ttl_s}
        )
        if resp.status_code != 201:
            raise ServiceError(
                SERVICE, "http", f"{base}: open session HTTP {resp.status_code}"
            )
        d = self._json(resp)
        try:
            return Session(base, str(d["session_id"]), int(d["ttl_s"]))
        except (KeyError, TypeError, ValueError) as e:
            raise ServiceError(SERVICE, "http", f"bad session payload: {d}") from e

    def heartbeat(self, s: Session) -> Optional[int]:
        """New ttl_s, or None when the worker no longer knows the session."""
        resp = self._call("POST", s.url, f"/app-session/{s.id}/heartbeat")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise ServiceError(
                SERVICE, "http", f"{s.url}: heartbeat HTTP {resp.status_code}"
            )
        try:
            return int(self._json(resp)["ttl_s"])
        except (KeyError, TypeError, ValueError):
            return s.ttl_s

    def close(self, s: Session) -> None:
        resp = self._call("DELETE", s.url, f"/app-session/{s.id}")
        if resp.status_code not in (204, 404):
            raise ServiceError(
                SERVICE, "http", f"{s.url}: close session HTTP {resp.status_code}"
            )

    def healthy(self, base: str) -> bool:
        try:
            return self._send("GET", base, "/health").status_code == 200
        except ServiceError:
            return False


class WorkerSessions:
    """LlmTarget: session on the first available worker of ``urls``."""

    def __init__(
        self,
        endpoints: Sequence[Endpoint],
        ttl_s: int = 30,
        client_name: str = "rt-edge",
        *,
        down_s: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.endpoints = {e.url.rstrip("/"): e for e in endpoints}
        self.urls = list(self.endpoints)
        self._api = WorkerApi(self.endpoints, transport=transport)
        self._ttl_s = ttl_s
        self._client_name = client_name
        self._down_s = down_s
        self._clock = clock
        self._lock = threading.Lock()  # guards the state below
        self._op_lock = threading.RLock()  # serializes network operations
        self._session: Optional[Session] = None
        self._down_until: dict[str, float] = {}
        self._error: Optional[str] = None
        self._started = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # called with the new worker url after a switch to another worker
        # (failover, return to the preferred one); the edge warms it up
        self.on_switch: Optional[Callable[[str], None]] = None

    # -- selection ---------------------------------------------------------

    def _is_down(self, url: str) -> bool:
        return self._clock() < self._down_until.get(url, 0.0)

    def _mark_down(self, url: str) -> None:
        self._down_until[url] = self._clock() + self._down_s

    def _open_first(self, candidates: Sequence[str]) -> Optional[Session]:
        errors = []
        for url in candidates:
            try:
                return self._api.open(url, self._client_name, self._ttl_s)
            except ServiceError as e:
                logger.warning("llm: session on {} failed: {}", url, e)
                errors.append(str(e))
                self._mark_down(url)
        with self._lock:
            self._error = "; ".join(errors) or "no llm worker configured"
        return None

    def _switch(self, new: Optional[Session], reason: str) -> None:
        with self._lock:
            old, self._session = self._session, new
            if new is not None:
                self._error = None
        if old is None and new is not None and reason != "start":
            logger.warning("llm: session on worker {} ({})", new.url, reason)
        if old is not None and (new is None or old.url != new.url):
            if new is None:
                logger.warning("llm: no worker available ({}): {}", reason, self._error)
            else:
                logger.warning(
                    "llm: switched worker {} -> {} ({})", old.url, new.url, reason
                )
            try:
                self._api.close(old)
            except ServiceError as e:
                logger.info("llm: closing session on {} failed: {}", old.url, e)
        switched = new is not None and reason != "start"
        if switched and (old is None or old.url != new.url) and self.on_switch:
            self.on_switch(new.url)

    def _failover(self, reason: str) -> None:
        with self._lock:
            current = self._session
        if current is not None:
            self._mark_down(current.url)
        candidates = [u for u in self.urls if not self._is_down(u)]
        self._switch(self._open_first(candidates), reason)

    # -- public ------------------------------------------------------------

    def start(self) -> Session:
        with self._op_lock:
            session = self._open_first(self.urls)
            if session is None:
                raise ServiceError(
                    SERVICE, "unreachable", f"no llm worker available: {self._error}"
                )
            self._switch(session, "start")
            logger.info("llm: session {} on {}", session.id, session.url)
            self._started = True
            if self._thread is None:
                self._stop = threading.Event()
                self._thread = threading.Thread(
                    target=self._run,
                    args=(self._stop,),
                    name="llm-session",
                    daemon=True,
                )
                self._thread.start()
            return session

    def stop(self) -> None:
        self._stop.set()
        with self._op_lock:
            self._started = False
            with self._lock:
                session, self._session = self._session, None
            if session is not None:
                try:
                    self._api.close(session)
                except ServiceError as e:
                    logger.warning("llm: closing session failed: {}", e)
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=0.2)

    def current(self) -> tuple[str, str]:
        with self._lock:
            if self._session is None:
                raise ServiceError(
                    SERVICE, "unreachable", self._error or "no llm session"
                )
            url = self._session.url
            return url, self.endpoints[url].credential.value

    def invalidate(self, kind: str) -> None:
        """Called by LlmClient after a failed request (then it retries once)."""
        with self._op_lock:
            if not self._started:
                return
            if kind == "auth":
                url = self.current_url()
                if url is not None and self.endpoints[url].credential.reload():
                    return  # retry on the same worker with the new token
                logger.warning("llm: no valid token for {}, failing over", url)
            self._failover(kind)

    def snapshot(self) -> dict:
        with self._lock:
            s = self._session
            e = self.endpoints[s.url] if s else None
            return {
                "active": s.url if s else None,
                "node": e.node if e else None,
                "local": e.local if e else None,
                "offline": e.credential.offline if e else None,
                "session_id": s.id if s else None,
                "urls": list(self.urls),
                "down": [u for u in self.urls if self._is_down(u)],
                "error": self._error,
            }

    # -- heartbeat / return to the preferred worker -------------------------

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                ttl = self._session.ttl_s if self._session else self._ttl_s
            if stop.wait(max(ttl / 3.0, 1.0)):
                break
            try:
                self.tick()
            except Exception as e:  # keep the thread alive
                logger.warning("llm: session tick failed: {}", e)

    def tick(self) -> None:
        """One heartbeat round, then try to return to a preferred worker."""
        with self._op_lock:
            if not self._started:
                return
            with self._lock:
                session = self._session
            if session is None:
                self._switch(
                    self._open_first([u for u in self.urls if not self._is_down(u)]),
                    "recovered",
                )
                return
            try:
                ttl = self._api.heartbeat(session)
            except ServiceError as e:
                logger.warning("llm: heartbeat on {} failed: {}", session.url, e)
                self._failover("heartbeat failed")
                return
            if ttl is None:
                logger.warning(
                    "llm: session {} on {} expired, opening a new one",
                    session.id,
                    session.url,
                )
                self._switch(self._open_first([session.url]), "session expired")
                if self.current_url() is None:
                    self._failover("session expired")
                return
            with self._lock:
                if self._session is session:
                    self._session = Session(session.url, session.id, ttl)
            self._return_to_preferred(session.url)

    def current_url(self) -> Optional[str]:
        with self._lock:
            return self._session.url if self._session else None

    def _return_to_preferred(self, active: str) -> None:
        for url in self.urls[: self.urls.index(active)]:
            if self._is_down(url) or not self._api.healthy(url):
                continue
            try:
                new = self._api.open(url, self._client_name, self._ttl_s)
            except ServiceError as e:
                logger.info("llm: preferred worker {} refused: {}", url, e)
                continue
            self._switch(new, "preferred worker is back")
            return
