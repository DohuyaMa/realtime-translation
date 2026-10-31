"""Lease an existing llama worker through Django (lease design spec 2.3, 5).

When no configured Django is reachable the keeper enters an explicit
*no-lease* mode instead of failing translation: it talks to the worker
directly, either the worker of the last lease of this session (its
``worker_token`` stays valid until the worker re-registers) or
``llm.direct_url`` / ``llm.direct_token_file``. The mode is logged at WARNING
and exposed in ``snapshot()``; the heartbeat thread keeps retrying the lease
and leaves the mode as soon as a Django answers.
"""

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


@dataclass(frozen=True)
class NoLeaseTarget:
    base_url: str
    token: str
    source: str  # "last_lease:<service>" | "direct_url"


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


class _AllUnreachable(ServiceError):
    """No configured Django answered the acquire."""

    def __init__(self, detail: str) -> None:
        super().__init__(SERVICE, "unreachable", f"no lease available: {detail}")
        self.detail = detail


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
        self._skipped: list[str] = []  # djangos unreachable on the last acquire
        self._last_lease: Optional[Lease] = None  # worker of the last lease
        self._no_lease: Optional[NoLeaseTarget] = None
        self._started = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _api(self, url: str) -> DjangoLeaseApi:
        if url not in self._apis:
            self._apis[url] = self._api_factory(url)
        return self._apis[url]

    # -- acquire / release -------------------------------------------------

    def _acquire_all(self, service: Optional[str]) -> Lease:
        """Lease on the first Django that grants one, mirror on the rest.

        Raises ``_AllUnreachable`` when no Django answered at all (the caller
        may enter no-lease mode) and ``ServiceError`` when some Django answered
        but refused (409/503/...): that is a real refusal, not an outage.
        """
        errors: list[str] = []
        skipped: list[str] = []
        primary: Optional[Lease] = None
        for url in self._settings.django_urls:
            try:
                primary = self._api(url).acquire(
                    self._client_name, service, self._settings.lease_ttl_s
                )
                break
            except ServiceError as e:
                logger.warning("lease acquire on {} skipped (unreachable): {}", url, e)
                errors.append(f"{url}: {e}")
                skipped.append(url)
            except LeaseError as e:
                logger.warning("lease acquire on {} failed: {}", url, e)
                errors.append(f"{url}: {e}")
        if primary is None:
            msg = "; ".join(errors) or "no django configured"
            with self._lock:
                self._error = msg
                self._skipped = skipped
            if errors and len(skipped) == len(errors):
                raise _AllUnreachable(msg)
            raise ServiceError(SERVICE, "unreachable", f"no lease available: {msg}")
        mirrors: dict[str, Lease] = {}
        for url in self._settings.django_urls:
            if url == primary.django_url or url in skipped:
                continue
            try:
                mirrors[url] = self._api(url).acquire(
                    self._client_name, primary.service_name, self._settings.lease_ttl_s
                )
            except ServiceError as e:
                logger.warning("mirror lease on {} skipped (unreachable): {}", url, e)
                skipped.append(url)
            except LeaseError as e:
                logger.info("mirror lease on {} skipped: {}", url, e)
        if skipped:
            logger.warning(
                "lease held on {} without unreachable djangos {}",
                primary.django_url,
                skipped,
            )
        with self._lock:
            was_no_lease = self._no_lease
            self._primary = primary
            self._last_lease = primary
            self._mirrors = mirrors
            self._skipped = skipped
            self._no_lease = None
            self._deadline = self._clock() + primary.ttl_s
            self._error = None
        if was_no_lease is not None:
            logger.warning(
                "leaving no-lease mode ({}): lease {} on {} via {}",
                was_no_lease.source,
                primary.id,
                primary.service_name,
                primary.django_url,
            )
        return primary

    def _acquire_or_no_lease(self, service: Optional[str]) -> Optional[Lease]:
        """Acquire a lease or, when every Django is unreachable, enter no-lease."""
        try:
            return self._acquire_all(service)
        except _AllUnreachable as e:
            self._enter_no_lease(e.detail)
            return None

    def _enter_no_lease(self, reason: str) -> None:
        with self._lock:
            last = self._last_lease
            current = self._no_lease
        if last is not None:
            target = NoLeaseTarget(
                last.base_url, last.worker_token, f"last_lease:{last.service_name}"
            )
        elif self._settings.direct_url:
            target = NoLeaseTarget(
                self._settings.direct_url.rstrip("/"),
                read_token(self._settings.direct_token_file) or "",
                "direct_url",
            )
        else:
            raise ServiceError(
                SERVICE,
                "unreachable",
                f"no lease available and no no-lease target "
                f"(set llm.direct_url): {reason}",
            )
        with self._lock:
            self._no_lease = target
        if current != target:
            logger.warning(
                "NO-LEASE mode: all djangos unreachable ({}); using worker {} "
                "directly (source: {}), retrying the lease in background",
                reason,
                target.base_url,
                target.source,
            )

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

    def start(self) -> Optional[Lease]:
        """Acquire the lease; ``None`` means the keeper runs in no-lease mode."""
        with self._op_lock:
            lease = self._acquire_or_no_lease(self._settings.service)
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
            with self._lock:
                self._no_lease = None
                self._last_lease = None
        t = self._thread
        self._thread = None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=0.2)

    def current(self) -> tuple[str, str]:
        with self._lock:
            if self._primary is not None:
                return self._primary.base_url, self._primary.worker_token
            if self._no_lease is not None:
                return self._no_lease.base_url, self._no_lease.token
            raise ServiceError(
                SERVICE, "unreachable", self._error or "lease not started"
            )

    def invalidate(self, kind: str) -> None:
        with self._op_lock:
            if not self._started:
                return
            with self._lock:
                no_lease = self._no_lease
            if kind == "auth" and no_lease is not None:
                if no_lease.source == "direct_url":
                    # the host-local token rotates on every worker restart
                    self._reload_direct_token(no_lease)
                    return
            with self._lock:
                if no_lease is not None and no_lease.source != "direct_url":
                    # the cached worker failed: fall through to direct_url
                    self._last_lease = None
            if kind == "auth" and no_lease is None:
                with self._lock:
                    primary = self._primary
                if primary is not None:
                    self._heartbeat_primary(primary)
                return
            self._release_all()
            try:
                self._acquire_or_no_lease(None)
            except ServiceError as e:
                with self._lock:
                    self._no_lease = None
                logger.warning("re-acquire after {} failed: {}", kind, e)

    def _reload_direct_token(self, target: NoLeaseTarget) -> None:
        token = read_token(self._settings.direct_token_file) or ""
        if token == target.token:
            logger.warning(
                "no-lease: worker {} rejected the token and {} is unchanged",
                target.base_url,
                self._settings.direct_token_file,
            )
            return
        logger.warning(
            "no-lease: worker {} rejected the token, re-read {}",
            target.base_url,
            self._settings.direct_token_file,
        )
        with self._lock:
            if self._no_lease == target:
                self._no_lease = NoLeaseTarget(target.base_url, token, target.source)

    def snapshot(self) -> dict:
        with self._lock:
            p = self._primary
            n = self._no_lease
            return {
                "mode": "lease" if p else ("no_lease" if n else None),
                "no_lease_source": n.source if n else None,
                "skipped": list(self._skipped),
                "service": p.service_name if p else None,
                "base_url": p.base_url if p else (n.base_url if n else None),
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
            self._acquire_or_no_lease(self._settings.service)
        except ServiceError as e:
            with self._lock:
                self._no_lease = None
            logger.warning("lease re-acquire failed: {}", e)

    def _retry_skipped_mirrors(self, primary: Lease) -> None:
        with self._lock:
            skipped = [u for u in self._skipped if u not in self._mirrors]
        for url in skipped:
            try:
                new = self._api(url).acquire(
                    self._client_name, primary.service_name, self._settings.lease_ttl_s
                )
            except ServiceError:
                continue
            except LeaseError as e:
                logger.info("mirror lease on recovered {} refused: {}", url, e)
                with self._lock:
                    self._skipped = [u for u in self._skipped if u != url]
                continue
            logger.warning("django {} reachable again: mirror lease taken", url)
            with self._lock:
                self._mirrors[url] = new
                self._skipped = [u for u in self._skipped if u != url]

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
            self._retry_skipped_mirrors(primary)
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
