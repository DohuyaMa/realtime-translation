"""Service discovery: which llm workers / rt-asr / rt-tts the app may use.

Owner design (2026-10-31): the app asks Django for the list of services and
their tokens (``GET <django>/api/llm/app-services/``, user DRF token), keeps
the services allowed in its config and connects to them directly; Django is
not on the request path. The last answer is cached on disk (0600) and used
while no Django is reachable (explicit ``source = "cache"``).

Endpoint order per kind: the local endpoint (``local.<kind>``) first, then the
allowed cluster services in Django's order (own host first).
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import httpx
from loguru import logger

from ..core.runtime import KINDS, EdgeSettings
from .base import Credential, read_token

PATH = "/api/llm/app-services/"


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
    """Discovered services plus where the list came from."""

    services: list[ServiceEntry] = field(default_factory=list)
    source: str = "none"  # "django:<url>" | "cache" | "none"
    instance: Optional[str] = None
    errors: list[str] = field(default_factory=list)


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


def endpoints(
    kind: str,
    settings: EdgeSettings,
    directory: Directory,
    hostname: Optional[str] = None,
) -> list[Endpoint]:
    """Local endpoint first, then allowed cluster services (discovery order)."""
    host = hostname or socket.gethostname()
    out: list[Endpoint] = []
    local_url = getattr(settings.local, kind)
    if local_url:
        own = next(
            (e for e in directory.services if e.kind == kind and e.node == host), None
        )
        fallback = settings.local_token_file if kind == "llm" else None
        out.append(
            Endpoint(
                kind,
                host,
                local_url,
                Credential(own.token if own else "", fallback),
                local=True,
            )
        )
    for e in directory.services:
        if e.kind != kind or e.node == host:
            continue
        if not is_allowed(e, settings.allowed_services):
            continue
        out.append(Endpoint(kind, e.node, e.url, Credential(e.token)))
    return out
