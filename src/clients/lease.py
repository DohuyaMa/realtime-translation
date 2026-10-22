"""Lease an existing llama worker through Django (lease design spec 2.3, 5)."""

from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

import httpx
from loguru import logger

from ..core.runtime import LlmSettings
from .base import ServiceError, read_token

SERVICE = "llm"


@dataclass(frozen=True)
class Lease:
    id: str
    django_url: str
    service_name: str
    base_url: str
    current_model: str
    worker_token: str
    ttl_s: int
    heartbeat_interval_s: int
    expires_at: datetime


class LeaseError(Exception):
    def __init__(self, status_code: int, detail: str = "") -> None:
        super().__init__(f"lease api HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class DjangoLeaseApi:
    def __init__(
        self,
        django_url: str,
        token_file: Optional[str],
        *,
        timeout_s: float = 10.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.django_url = django_url.rstrip("/")
        self._token_file = token_file
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {}
        token = read_token(self._token_file)
        if token:
            headers["Authorization"] = f"Token {token}"
        try:
            return self._http.request(
                method, self.django_url + path, headers=headers, **kwargs
            )
        except httpx.TimeoutException as e:
            raise ServiceError(SERVICE, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(SERVICE, "unreachable", str(e)) from e

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            if isinstance(body, dict) and "detail" in body:
                return str(body["detail"])
        except ValueError:
            pass
        return resp.text[:200]

    def _parse(self, resp: httpx.Response) -> Lease:
        try:
            d = resp.json()
            svc = d["service"]
            return Lease(
                id=str(d["id"]),
                django_url=self.django_url,
                service_name=svc["name"],
                base_url=str(svc["base_url"]).rstrip("/"),
                current_model=svc.get("current_model") or "",
                worker_token=d["worker_token"],
                ttl_s=int(d["ttl_s"]),
                heartbeat_interval_s=int(d["heartbeat_interval_s"]),
                expires_at=datetime.fromisoformat(
                    d["expires_at"].replace("Z", "+00:00")
                ),
            )
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ServiceError(SERVICE, "http", f"bad lease payload: {e}") from e

    def acquire(self, client_name: str, service: Optional[str], ttl_s: int) -> Lease:
        resp = self._call(
            "POST",
            "/api/llm/leases/",
            json={"client_name": client_name, "service": service, "ttl_s": ttl_s},
        )
        if resp.status_code != 201:
            raise LeaseError(resp.status_code, self._detail(resp))
        return self._parse(resp)

    def heartbeat(self, lease: Lease, ttl_s: Optional[int] = None) -> Lease:
        resp = self._call(
            "POST",
            f"/api/llm/leases/{lease.id}/heartbeat/",
            json={} if ttl_s is None else {"ttl_s": ttl_s},
        )
        if resp.status_code != 200:
            raise LeaseError(resp.status_code, self._detail(resp))
        return self._parse(resp)

    def release(self, lease: Lease) -> None:
        resp = self._call("DELETE", f"/api/llm/leases/{lease.id}/")
        if resp.status_code not in (204, 404):
            raise LeaseError(resp.status_code, self._detail(resp))


class LeaseKeeper:
    """LlmTarget backed by a Django lease, kept alive by a heartbeat thread."""

    def __init__(
        self,
        settings: LlmSettings,
        client_name: Optional[str] = None,
        *,
        api_factory: Optional[Callable[[str], DjangoLeaseApi]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._client_name = client_name or f"rt-edge@{socket.gethostname()}"
        self._api_factory = api_factory or (
            lambda url: DjangoLeaseApi(url, settings.django_token_file)
        )
        self._apis: dict[str, DjangoLeaseApi] = {}
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()  # guards the state below
        self._op_lock = threading.RLock()  # serializes network operations
        self._primary: Optional[Lease] = None
        self._mirrors: dict[str, Lease] = {}
        self._deadline = 0.0
        self._error: Optional[str] = None
        self._started = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _api(self, url: str) -> DjangoLeaseApi:
        if url not in self._apis:
            self._apis[url] = self._api_factory(url)
        return self._apis[url]

    # -- acquire / release -------------------------------------------------

    def _acquire_all(self, service: Optional[str]) -> Lease:
        errors: list[str] = []
        primary: Optional[Lease] = None
        for url in self._settings.django_urls:
            try:
                primary = self._api(url).acquire(
                    self._client_name, service, self._settings.lease_ttl_s
                )
                break
            except (LeaseError, ServiceError) as e:
                logger.warning("lease acquire on {} failed: {}", url, e)
                errors.append(f"{url}: {e}")
        if primary is None:
            msg = "; ".join(errors) or "no django configured"
            with self._lock:
                self._error = msg
            raise ServiceError(SERVICE, "unreachable", f"no lease available: {msg}")
        mirrors: dict[str, Lease] = {}
        for url in self._settings.django_urls:
            if url == primary.django_url:
                continue
            try:
                mirrors[url] = self._api(url).acquire(
                    self._client_name, primary.service_name, self._settings.lease_ttl_s
                )
            except (LeaseError, ServiceError) as e:
                logger.info("mirror lease on {} skipped: {}", url, e)
        with self._lock:
            self._primary = primary
            self._mirrors = mirrors
            self._deadline = self._clock() + primary.ttl_s
            self._error = None
        return primary

    def _release_all(self) -> None:
        with self._lock:
            leases = ([self._primary] if self._primary else []) + list(
                self._mirrors.values()
            )
            self._primary = None
            self._mirrors = {}
        for lease in leases:
            try:
                self._api(lease.django_url).release(lease)
            except (LeaseError, ServiceError) as e:
                logger.warning("lease release on {} failed: {}", lease.django_url, e)

    # -- public ------------------------------------------------------------

    def start(self) -> Lease:
        with self._op_lock:
            lease = self._acquire_all(self._settings.service)
            self._started = True
            if self._thread is None:
                self._stop = threading.Event()
                self._thread = threading.Thread(
                    target=self._run,
                    args=(self._stop,),
                    name="lease-heartbeat",
                    daemon=True,
                )
                self._thread.start()
            return lease

    def stop(self) -> None:
        self._stop.set()
        with self._op_lock:
            self._started = False
            self._release_all()
        t = self._thread
        self._thread = None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=0.2)

    def current(self) -> tuple[str, str]:
        with self._lock:
            if self._primary is None:
                raise ServiceError(
                    SERVICE, "unreachable", self._error or "lease not started"
                )
            return self._primary.base_url, self._primary.worker_token

    def invalidate(self, kind: str) -> None:
        with self._op_lock:
            if not self._started:
                return
            if kind == "auth":
                with self._lock:
                    primary = self._primary
                if primary is not None:
                    self._heartbeat_primary(primary)
                return
            self._release_all()
            try:
                self._acquire_all(None)
            except ServiceError as e:
                logger.warning("re-acquire after {} failed: {}", kind, e)

    def snapshot(self) -> dict:
        with self._lock:
            p = self._primary
            return {
                "service": p.service_name if p else None,
                "base_url": p.base_url if p else None,
                "model": p.current_model if p else None,
                "expires_at": p.expires_at.isoformat() if p else None,
                "django": p.django_url if p else None,
                "mirrors": [
                    {"django": m.django_url, "service": m.service_name}
                    for m in self._mirrors.values()
                ],
                "error": self._error,
            }

    # -- heartbeat ---------------------------------------------------------

    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                interval = self._primary.heartbeat_interval_s if self._primary else 10
            self._sleep(interval)
            if stop.is_set():
                break
            try:
                self.tick()
            except Exception as e:  # keep the thread alive
                logger.warning("lease heartbeat tick failed: {}", e)

    def _reacquire(self) -> None:
        self._release_all()
        try:
            self._acquire_all(self._settings.service)
        except ServiceError as e:
            logger.warning("lease re-acquire failed: {}", e)

    def _heartbeat_primary(self, primary: Lease) -> None:
        try:
            fresh = self._api(primary.django_url).heartbeat(primary)
        except LeaseError as e:
            if e.status_code in (404, 410):
                logger.info("primary lease gone ({}), re-acquiring", e.status_code)
                self._reacquire()
            else:
                logger.warning("primary heartbeat failed: {}", e)
                with self._lock:
                    self._error = str(e)
                if self._clock() >= self._deadline:
                    self._reacquire()
            return
        except ServiceError as e:
            logger.warning("primary heartbeat network error: {}", e)
            with self._lock:
                self._error = str(e)
            if self._clock() >= self._deadline:
                self._reacquire()
            return
        with self._lock:
            self._primary = fresh
            self._deadline = self._clock() + fresh.ttl_s
            self._error = None

    def tick(self) -> None:
        """One heartbeat round for the primary lease and mirrors."""
        with self._op_lock:
            with self._lock:
                primary = self._primary
                mirrors = dict(self._mirrors)
            if not self._started:
                return
            if primary is None:
                self._reacquire()
                return
            self._heartbeat_primary(primary)
            with self._lock:
                if self._primary is None or self._primary.id != primary.id:
                    return  # re-acquired, mirrors were recreated
            for url, m in mirrors.items():
                try:
                    fresh = self._api(url).heartbeat(m)
                    with self._lock:
                        self._mirrors[url] = fresh
                except LeaseError as e:
                    logger.info("mirror {} heartbeat failed: {}", url, e)
                    if e.status_code in (404, 410):
                        try:
                            new = self._api(url).acquire(
                                self._client_name,
                                primary.service_name,
                                self._settings.lease_ttl_s,
                            )
                            with self._lock:
                                self._mirrors[url] = new
                        except (LeaseError, ServiceError):
                            with self._lock:
                                self._mirrors.pop(url, None)
                except ServiceError as e:
                    logger.info("mirror {} heartbeat network error: {}", url, e)
