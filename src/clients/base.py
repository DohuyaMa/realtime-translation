"""Shared HTTP plumbing for the remote speech services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger


class ServiceError(Exception):
    """A request failed. kind: unreachable | timeout | auth | http | busy."""

    def __init__(self, service: str, kind: str, message: str = "", status_code: Optional[int] = None):
        super().__init__(f"{service}: {kind}: {message}")
        self.service = service
        self.kind = kind
        self.status_code = status_code


def read_token(token_file: Optional[str]) -> Optional[str]:
    if not token_file:
        return None
    try:
        token = Path(token_file).read_text().strip()
    except OSError as e:
        logger.warning("token file {} unreadable ({})", token_file, e)
        return None
    return token or None


def raise_for_response(service: str, resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise ServiceError(service, "auth", f"HTTP {resp.status_code}", resp.status_code)
    if resp.status_code >= 400:
        raise ServiceError(service, "http", f"HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)


def json_body(service: str, resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError as e:
        raise ServiceError(service, "http", f"invalid JSON: {resp.text[:200]!r}") from e
    if not isinstance(data, dict):
        raise ServiceError(service, "http", f"unexpected JSON payload: {data!r:.200}")
    return data


class ServiceClient:
    service = "service"

    def __init__(
        self,
        base_url: str,
        *,
        token_file: Optional[str] = None,
        timeout_s: float = 30.0,
        connect_timeout_s: float = 3.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        headers = {}
        token = read_token(token_file)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=self.base_url + "/",
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            transport=transport,
        )

    def _request(self, method: str, path: str, *, timeout_s: Optional[float] = None, **kwargs: Any) -> httpx.Response:
        if timeout_s is not None:
            kwargs["timeout"] = httpx.Timeout(timeout_s, connect=self._client.timeout.connect)
        try:
            resp = self._client.request(method, path.lstrip("/"), **kwargs)
        except httpx.TimeoutException as e:
            raise ServiceError(self.service, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(self.service, "unreachable", str(e)) from e
        raise_for_response(self.service, resp)
        return resp

    def _json(self, resp: httpx.Response) -> dict:
        return json_body(self.service, resp)

    def health(self) -> bool:
        try:
            self._request("GET", "/health")
        except ServiceError:
            return False
        return True

    def status(self) -> Optional[dict]:
        try:
            return self._json(self._request("GET", "/status"))
        except ServiceError:
            return None

    def close(self) -> None:
        self._client.close()
