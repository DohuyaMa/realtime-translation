"""Ordered endpoint lists for rt-asr / rt-tts / rt-vosk with failover and
return-to-local.

``urls`` is a priority order (local first, then cluster nodes). Every call
goes to the first endpoint that is not marked down and not reported unhealthy
by the health monitor; an endpoint marked down by a failed call comes back
after ``down_s``, so the pool returns to the local one by itself. Every switch
of the serving endpoint is logged at WARNING and exposed as ``active_url``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generic, Optional, Sequence, TypeVar

import httpx
from loguru import logger

from ..core.runtime import EdgeSettings
from .asr import AsrClient
from .base import ServiceError
from .discovery import Directory, Endpoint, endpoints
from .tts import TtsClient
from .vosk import VoskClient

C = TypeVar("C")
T = TypeVar("T")


def is_retryable(err: ServiceError) -> bool:
    # "auth": the client already re-fetched the token once; try the next node
    return err.kind in ("unreachable", "timeout", "auth", "not_ready") or (
        err.kind == "http" and err.status_code == 503
    )


class EndpointPool(Generic[C]):
    def __init__(
        self,
        service: str,
        endpoints: Sequence[Endpoint],
        factory: Callable[[Endpoint], C],
        *,
        down_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.endpoints = {e.url: e for e in endpoints}
        self.urls = list(self.endpoints)
        self._factory = factory
        self._down_s = down_s
        self._clock = clock
        self._lock = threading.RLock()
        self._clients: Dict[str, C] = {}
        self._status: Dict[str, tuple[bool, Optional[int]]] = {}
        self._down_until: Dict[str, float] = {}
        self._active: Optional[str] = None

    def _is_down(self, url: str) -> bool:
        until = self._down_until.get(url)
        if until is None:
            return False
        if self._clock() >= until:
            del self._down_until[url]
            return False
        return True

    def update_status(self, url: str, *, ready: bool, pending: Optional[int]) -> None:
        with self._lock:
            self._status[url] = (ready, pending)

    def pick(self) -> str:
        """First endpoint in priority order that is up and not unhealthy."""
        with self._lock:
            alive = [u for u in self.urls if not self._is_down(u)]
            if not alive:
                raise ServiceError(self.service, "unreachable", "no endpoint available")
            # unknown health (not probed yet) counts as a candidate
            healthy = [u for u in alive if self._status.get(u, (True, None))[0]]
            return (healthy or alive)[0]

    def _activate(self) -> str:
        url = self.pick()
        with self._lock:
            previous, self._active = self._active, url
        if previous is not None and previous != url:
            logger.warning("{}: switched {} -> {}", self.service, previous, url)
        return url

    @property
    def active_url(self) -> Optional[str]:
        return self._active

    def active(self) -> Optional[Dict[str, Any]]:
        """Serving endpoint for the UI: url, node, local, offline token."""
        url = self._active
        if url is None:
            return None
        e = self.endpoints[url]
        return {
            "url": url,
            "node": e.node,
            "local": e.local,
            "offline": e.credential.offline,
        }

    def _client(self, url: str) -> C:
        with self._lock:
            if url not in self._clients:
                self._clients[url] = self._factory(self.endpoints[url])
            return self._clients[url]

    def _mark_down(self, url: str) -> None:
        with self._lock:
            self._down_until[url] = self._clock() + self._down_s

    def call(self, fn: Callable[[C], T]) -> T:
        url = self._activate()
        try:
            return fn(self._client(url))
        except ServiceError as e:
            if not is_retryable(e):
                raise
            logger.warning("{}: {} failed ({}), failing over", self.service, url, e)
            self._mark_down(url)
        return fn(self._client(self._activate()))

    def clients(self) -> Dict[str, C]:
        return {u: self._client(u) for u in self.urls}

    def close(self) -> None:
        with self._lock:
            clients, self._clients = list(self._clients.values()), {}
        for c in clients:
            close = getattr(c, "close", None)
            if close:
                close()


@dataclass(frozen=True)
class SpeechPools:
    asr: EndpointPool[AsrClient]
    tts: Optional[EndpointPool[TtsClient]] = None
    # None: rt-vosk switched off (services.use.vosk = off) or none found
    vosk: Optional[EndpointPool[VoskClient]] = None

    def by_name(self) -> Dict[str, EndpointPool]:
        pools: Dict[str, EndpointPool] = {"asr": self.asr}
        for name in ("vosk", "tts"):
            pool = getattr(self, name)
            if pool is not None:
                pools[name] = pool
        return pools

    def close(self) -> None:
        for pool in self.by_name().values():
            pool.close()


def build_pools(
    settings: EdgeSettings,
    directory: Directory,
    transport: Optional[httpx.BaseTransport] = None,
) -> SpeechPools:
    from . import client_factories

    f = client_factories(settings, transport)
    asr = EndpointPool("asr", endpoints("asr", settings, directory), f.asr)
    tts_eps = endpoints("tts", settings, directory)
    tts = EndpointPool("tts", tts_eps, f.tts) if tts_eps else None
    vosk_eps = endpoints("vosk", settings, directory)
    vosk = EndpointPool("vosk", vosk_eps, f.vosk) if vosk_eps else None
    return SpeechPools(asr=asr, tts=tts, vosk=vosk)
