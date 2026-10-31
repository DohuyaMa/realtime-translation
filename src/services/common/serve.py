"""Common main() for `python -m src.services.<svc>`."""

from __future__ import annotations

import ipaddress
import os
import socket
import sys
from typing import Callable, Mapping, Sequence

from fastapi import FastAPI

from .env import SettingsError
from .logs import setup_logging
from .settings import BaseSettings, apply_model_dir

# Linux IP_FREEBIND (= IPV6_FREEBIND value too): bind an address that is not
# configured yet (e.g. tailscale0 coming up after the unit starts).
IP_FREEBIND = getattr(socket, "IP_FREEBIND", 15)


def bind_sockets(addresses: Sequence[str], port: int) -> list[socket.socket]:
    """One listening TCP socket per address (RT_LISTEN_ADDRESSES)."""
    socks: list[socket.socket] = []
    try:
        for addr in addresses:
            v6 = ipaddress.ip_address(addr).version == 6
            sock = socket.socket(socket.AF_INET6 if v6 else socket.AF_INET)
            socks.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if v6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            if sys.platform.startswith("linux"):
                level = socket.IPPROTO_IPV6 if v6 else socket.IPPROTO_IP
                sock.setsockopt(level, IP_FREEBIND, 1)
            sock.bind((addr, port))
            sock.listen(2048)
            sock.set_inheritable(True)
    except OSError:
        for sock in socks:
            sock.close()
        raise
    return socks


def run_service(
    settings_loader: Callable[[Mapping[str, str]], BaseSettings],
    app_factory: Callable[[BaseSettings], FastAPI],
) -> int:
    try:
        settings = settings_loader(os.environ)
        setup_logging(settings.log_level)
        apply_model_dir(settings)
        app = app_factory(settings)
    except SettingsError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    import uvicorn

    try:
        socks = bind_sockets(settings.listen_addresses, settings.port)
    except OSError as exc:
        print(
            f"cannot listen on {','.join(settings.listen_addresses)}"
            f" port {settings.port}: {exc}",
            file=sys.stderr,
        )
        return 2
    config = uvicorn.Config(
        app, workers=1, log_config=None, log_level=settings.log_level
    )
    uvicorn.Server(config).run(sockets=socks)
    return 0
