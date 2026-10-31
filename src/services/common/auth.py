"""Optional bearer-token auth (RT_API_TOKEN_FILE)."""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import Callable, Optional

from starlette.requests import HTTPConnection

from .env import SettingsError
from .errors import ApiError


def load_token(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        token = Path(path).read_text().strip()
    except OSError as exc:
        raise SettingsError(
            f"RT_API_TOKEN_FILE {path!r} is not readable: {exc}"
        ) from exc
    if not token:
        raise SettingsError(f"RT_API_TOKEN_FILE {path!r} is empty")
    return token


def header_matches(header: Optional[str], token: str) -> bool:
    if not header:
        return False
    scheme, _, value = header.partition(" ")
    # "Bearer" (curl/docs) and "Token" (edge, same token as the llm workers)
    return scheme.lower() in ("bearer", "token") and hmac.compare_digest(
        value.strip().encode(), token.encode()
    )


def make_bearer_dependency(token: Optional[str]) -> Callable:
    async def require_bearer(conn: HTTPConnection) -> None:
        if token is None:
            return
        if not header_matches(conn.headers.get("authorization"), token):
            raise ApiError(
                401,
                "unauthorized",
                "Missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_bearer


def ws_authorized(conn: HTTPConnection, token: Optional[str]) -> bool:
    return token is None or header_matches(conn.headers.get("authorization"), token)
