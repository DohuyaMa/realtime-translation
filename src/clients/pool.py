"""Endpoint pools for rt-asr / rt-tts with pinning and failover."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Generic, Optional, Sequence, TypeVar

import httpx

from ..core.runtime import EdgeSettings
from .asr import AsrClient
from .base import ServiceError
from .tts import TtsClient

C = TypeVar("C")
T = TypeVar("T")


def is_retryable(err: ServiceError) -> bool:
    return err.kind in ("unreachable", "timeout") or (
        err.kind == "http" and err.status_code == 503
    )


class EndpointPool(Generic[C]):
    def __init__(
        self,
        service: str,
        urls: Sequence[str],
        factory: Callable[[str], C],
        *,
        down_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.service = service
        self.urls = list(urls)
        self._factory = factory
        self._down_s = down_s
        self._clock = clock
        self._lock = threading.RLock()
        self._clients: Dict[str, C] = {}
        self._status: Dict[str, tuple[bool, Optional[int]]] = {}
        self._down_until: Dict[str, float] = {}
        self._pinned: Optional[str] = None

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
        with self._lock:
            alive = [u for u in self.urls if not self._is_down(u)]
            if not alive:
                raise ServiceError(self.service, "unreachable", "no endpoint available")
            ready = [u for u in alive if self._status.get(u, (False, None))[0]]
            if not ready:
                return alive[0]

            def load(u: str) -> float:
                p = self._status[u][1]
                return float("inf") if p is None else p

            return min(ready, key=load)  # min() keeps list order on ties

    def pin(self) -> str:
        with self._lock:
            self._pinned = self.pick()
            return self._pinned

    @property
    def pinned_url(self) -> Optional[str]:
        return self._pinned

    def _client(self, url: str) -> C:
        with self._lock:
            if url not in self._clients:
                self._clients[url] = self._factory(url)
            return self._clients[url]

    def _mark_down(self, url: str) -> None:
        with self._lock:
            self._down_until[url] = self._clock() + self._down_s
            if self._pinned == url:
                self._pinned = None

    def call(self, fn: Callable[[C], T]) -> T:
        url = self._pinned or self.pin()
        try:
            return fn(self._client(url))
        except ServiceError as e:
            if not is_retryable(e):
                raise
            self._mark_down(url)
        return fn(self._client(self.pin()))

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

    def by_name(self) -> Dict[str, EndpointPool]:
        pools: Dict[str, EndpointPool] = {"asr": self.asr}
        if self.tts is not None:
            pools["tts"] = self.tts
        return pools

    def close(self) -> None:
        for pool in self.by_name().values():
            pool.close()


def build_pools(
    settings: EdgeSettings, transport: Optional[httpx.BaseTransport] = None
) -> SpeechPools:
    from . import client_factories

    f = client_factories(settings, transport)
    asr = EndpointPool("asr", settings.asr_urls, f.asr)
    tts = EndpointPool("tts", settings.tts_urls, f.tts) if settings.tts_urls else None
    return SpeechPools(asr=asr, tts=tts)
