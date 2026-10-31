"""Service discovery: which llm workers / rt-asr / rt-tts / rt-vosk the app
may use.

Owner design (2026-10-31): the app asks Django for the list of services and
their tokens (``GET <django>/api/llm/app-services/``, user DRF token), keeps
the services allowed in its config and connects to them directly; Django is
not on the request path. The last answer is cached on disk (0600) and used
while no Django is reachable (explicit ``source = "cache"``).

Endpoint order per kind: the local endpoint (``local.<kind>``) first, then the
allowed cluster services in Django's order (own host first).
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx
from loguru import logger

from ..core.runtime import KINDS, OFF, EdgeSettings
from .base import Credential, read_token

PATH = "/api/llm/app-services/"
REFRESH_EVERY_S = 300.0


@dataclass(frozen=True)
class ServiceEntry:
    kind: str
    node: str
    url: str
    token: str
    tier: Optional[int] = None
    models: tuple[str, ...] = ()
    healthy: bool = True

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.node}"


@dataclass
class Endpoint:
    kind: str
    node: str
    url: str
    credential: Credential
    local: bool = False


@dataclass
class Directory:
    """Discovered services plus where the list came from.

    ``fetch`` (set by ``discover``) re-runs discovery: on a 401 via
    ``token_for`` (at most once per ``min_refresh_s``) and every
    ``REFRESH_EVERY_S`` in the background; fresh tokens are pushed into the
    credentials handed out by ``endpoints``.
    """

    services: list[ServiceEntry] = field(default_factory=list)
    source: str = "none"  # "django:<url>" | "cache" | "none"
    instance: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    fetch: Optional[Callable[[], "Directory"]] = field(default=None, repr=False)
    min_refresh_s: float = 30.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        self._last = self.clock()
        self._lock = threading.Lock()
        self._creds: dict[tuple[str, str], list[Credential]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _token(self, kind: str, node: str) -> Optional[str]:
        e = next((e for e in self.services if e.kind == kind and e.node == node), None)
        return e.token if e else None

    def register(self, kind: str, node: str, cred: Credential) -> Credential:
        ref = weakref.ref(self)  # no Directory <-> Credential reference cycle

        def refresh() -> Optional[str]:
            directory = ref()
            return directory.token_for(kind, node) if directory else None

        cred.refresh = refresh
        with self._lock:
            self._creds.setdefault((kind, node), []).append(cred)
        return cred

    def refresh(self) -> bool:
        """Re-run discovery (rate-limited); True if Django answered."""
        if self.fetch is None:
            return False
        with self._lock:
            if self.clock() - self._last < self.min_refresh_s:
                return False
            self._last = self.clock()
        new = self.fetch()
        if not new.source.startswith("django:"):
            return False  # keep the current list; failures are logged
        with self._lock:
            self.services, self.source = new.services, new.source
            self.instance, self.errors = new.instance, new.errors
            creds = {k: list(v) for k, v in self._creds.items()}
        for (kind, node), items in creds.items():
            token = self._token(kind, node)
            for cred in items:
                if token and token != cred.value:
                    logger.info("discovery: new token for {}:{}", kind, node)
                    cred.value, cred.offline = token, False
        return True

    def token_for(self, kind: str, node: str) -> Optional[str]:
        self.refresh()
        return self._token(kind, node)

    def start_periodic(self, interval_s: float = 0.0) -> None:
        if self.fetch is None or self._thread is not None:
            return
        interval = interval_s or REFRESH_EVERY_S
        self._stop = threading.Event()

        def run(stop: threading.Event) -> None:
            while not stop.wait(interval):
                try:
                    self.refresh()
                except Exception as e:  # keep refreshing
                    logger.warning("periodic discovery failed: {}", e)

        self._thread = threading.Thread(
            target=run, args=(self._stop,), name="discovery", daemon=True
        )
        self._thread.start()

    def stop_periodic(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.0)


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "real-time-translator" / "app-services.json"


def parse(payload: Any) -> tuple[Optional[str], list[ServiceEntry]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), list):
        raise ValueError(f"unexpected app-services payload: {payload!r:.200}")
    entries = []
    for d in payload["services"]:
        try:
            if d["kind"] not in KINDS:
                continue
            entries.append(
                ServiceEntry(
                    kind=d["kind"],
                    node=str(d["node"]),
                    url=str(d["url"]).rstrip("/"),
                    token=str(d.get("token") or ""),
                    tier=d.get("tier"),
                    models=tuple(d.get("models") or ()),
                    healthy=bool(d.get("healthy", True)),
                )
            )
        except (KeyError, TypeError) as e:
            raise ValueError(f"bad service entry {d!r:.200}: {e}") from e
    return payload.get("instance"), entries


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def discover(
    settings: EdgeSettings,
    *,
    cache_path: Optional[Path] = None,
    transport: Optional[httpx.BaseTransport] = None,
    timeout_s: float = 3.0,
) -> Directory:
    """Ask each Django in order; fall back to the on-disk cache."""
    cache_path = cache_path or default_cache_path()
    d = _discover(settings, cache_path, transport, timeout_s)
    if settings.django_urls:  # nothing to refresh without Django
        d.fetch = lambda: _discover(settings, cache_path, transport, timeout_s)
    return d


def _discover(
    settings: EdgeSettings,
    cache_path: Path,
    transport: Optional[httpx.BaseTransport],
    timeout_s: float,
) -> Directory:
    errors: list[str] = []
    if settings.django_urls:
        token = read_token(settings.django_token_file) or ""
        with httpx.Client(timeout=timeout_s, transport=transport) as http:
            for url in settings.django_urls:
                try:
                    resp = http.get(
                        url + PATH, headers={"Authorization": f"Token {token}"}
                    )
                    if resp.status_code != 200:
                        raise ValueError(f"HTTP {resp.status_code}")
                    payload = resp.json()
                    instance, entries = parse(payload)
                except (httpx.HTTPError, ValueError) as e:
                    logger.warning("discovery on {} failed: {}", url, e)
                    errors.append(f"{url}: {e}")
                    continue
                try:
                    _write_cache(cache_path, payload)
                except OSError as e:
                    logger.warning("cannot write discovery cache {}: {}", cache_path, e)
                logger.info("discovered {} services via {}", len(entries), url)
                return Directory(entries, f"django:{url}", instance, errors)
    try:
        instance, entries = parse(json.loads(cache_path.read_text()))
    except FileNotFoundError:
        if settings.django_urls:
            logger.warning("no Django reachable and no discovery cache: local only")
        return Directory([], "none", None, errors)
    except (OSError, ValueError) as e:
        logger.warning("discovery cache {} unusable: {}", cache_path, e)
        return Directory([], "none", None, errors + [f"cache: {e}"])
    if settings.django_urls:
        logger.warning(
            "no Django reachable: using cached service list ({} services)",
            len(entries),
        )
    return Directory(entries, "cache", instance, errors)


def is_allowed(entry: ServiceEntry, allowed: Optional[Sequence[str]]) -> bool:
    return allowed is None or entry.key in allowed


_ADDR_CACHE: tuple[float, frozenset[str]] = (float("-inf"), frozenset())


def host_addresses(max_age_s: float = 10.0) -> frozenset[str]:
    """Cached ``read_host_addresses`` (polled by the UI every 0.5 s)."""
    global _ADDR_CACHE
    at, addrs = _ADDR_CACHE
    if time.monotonic() - at > max_age_s:
        _ADDR_CACHE = (time.monotonic(), read_host_addresses())
        addrs = _ADDR_CACHE[1]
    return addrs


def read_host_addresses() -> frozenset[str]:
    """IP addresses assigned to this host (Linux /proc; a bind probe is not
    reliable: net.ipv4.ip_nonlocal_bind=1 lets any address bind)."""
    out: set[str] = set()
    try:
        lines = Path("/proc/net/fib_trie").read_text().splitlines()
    except OSError:
        lines = []
    for prev, line in zip(lines, lines[1:]):
        if line.strip() == "/32 host LOCAL":
            out.add(prev.strip().lstrip("|-+ ").strip())
    try:
        for line in Path("/proc/net/if_inet6").read_text().splitlines():
            raw = line.split()[0]
            out.add(str(ipaddress.ip_address(bytes.fromhex(raw))))
    except (OSError, ValueError, IndexError):
        pass
    return frozenset(out)


def is_local_address(host: str) -> bool:
    """True for loopback names and IP literals assigned to this machine."""
    if host.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_loopback or str(ip) in host_addresses()


def own_node(settings: EdgeSettings, hostname: Optional[str] = None) -> str:
    return settings.local.node or hostname or socket.gethostname()


def is_own(
    entry: ServiceEntry,
    node: str,
    local_address: Callable[[str], bool] = is_local_address,
) -> bool:
    """Service of this host: same node name (any case) or a local URL host."""
    if entry.node.lower() == node.lower():
        return True
    host = urlsplit(entry.url).hostname
    return bool(host) and local_address(host)


def loopback_url(url: str) -> str:
    parts = urlsplit(url)
    netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", "")).rstrip("/")


# (kind, node) -> (loopback URL, discovered URL) last logged: endpoints() runs
# on every stats poll (~2x/s), the substitution is logged once per change
_loopback_logged: dict[tuple[str, str], tuple[str, str]] = {}


def _log_loopback(kind: str, node: str, local_url: str, url: str) -> None:
    if _loopback_logged.get((kind, node)) == (local_url, url):
        return
    _loopback_logged[(kind, node)] = (local_url, url)
    logger.info(
        "{}:{} is this host: using {} instead of {}", kind, node, local_url, url
    )


def endpoints(
    kind: str,
    settings: EdgeSettings,
    directory: Directory,
    hostname: Optional[str] = None,
    local_address: Callable[[str], bool] = is_local_address,
    register: bool = True,
) -> list[Endpoint]:
    """Endpoints for ``kind`` in priority order.

    Auto (``use.<kind>`` unset): this host's service first, then the allowed
    cluster services in discovery order. Pinned to a node: only that node's
    endpoint (no failover). ``OFF``: none. This host's service is always reached locally
    (``local.<kind>`` or 127.0.0.1 on the discovered port), never via the
    cluster URL Django lists for it. ``register=False``: credentials are not
    refreshed by discovery (listing only, e.g. for the UI).
    """
    reg = directory.register if register else (lambda kind, node, cred: cred)
    node = own_node(settings, hostname)
    allowed = settings.allowed_services
    pinned = getattr(settings.use, kind)
    if pinned == OFF:  # switched off in the settings (optional kinds only)
        return []
    own = [
        e
        for e in directory.services
        if e.kind == kind and is_own(e, node, local_address)
    ]
    own_entry = own[0] if own else None
    own_name = own_entry.node if own_entry else node
    out: list[Endpoint] = []
    local_url = getattr(settings.local, kind)
    if not local_url and own_entry is not None:
        local_url = loopback_url(own_entry.url)
        _log_loopback(kind, own_entry.node, local_url, own_entry.url)
    own_allowed = allowed is None or f"{kind}:{own_name}" in allowed
    own_picked = pinned is None or pinned.lower() in (own_name.lower(), node.lower())
    if local_url and own_allowed and own_picked:
        fallback = settings.local_token_file if kind == "llm" else None
        out.append(
            Endpoint(
                kind,
                own_name,
                local_url,
                reg(
                    kind,
                    own_name,
                    Credential(own_entry.token if own_entry else "", fallback),
                ),
                local=True,
            )
        )
    for e in directory.services:
        if e.kind != kind or e in own or not is_allowed(e, allowed):
            continue
        if pinned is not None and e.node != pinned:
            continue
        cred = reg(kind, e.node, Credential(e.token))
        out.append(Endpoint(kind, e.node, e.url, cred))
    if pinned is not None and not out and register:
        logger.error(
            "{}: pinned service {}:{} is not available (not discovered, not"
            " allowed or no local endpoint)",
            kind,
            kind,
            pinned,
        )
    return out


def choices(
    kind: str,
    settings: EdgeSettings,
    directory: Directory,
    hostname: Optional[str] = None,
    local_address: Callable[[str], bool] = is_local_address,
) -> list[Endpoint]:
    """Every instance the user can pick for ``kind`` (auto order, unpinned)."""
    import dataclasses

    free = dataclasses.replace(
        settings, use=dataclasses.replace(settings.use, **{kind: None})
    )
    return endpoints(kind, free, directory, hostname, local_address, register=False)
