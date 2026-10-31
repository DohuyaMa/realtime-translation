"""Shared HTTP plumbing for the remote speech services."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import httpx
from loguru import logger


class ServiceError(Exception):
    """A request failed. kind: unreachable | timeout | auth | http | busy |
    not_ready (HTTP 503 from rt-asr/rt-tts: the model is not loaded/ready) |
    wrong_language (HTTP 409 from rt-vosk: its single model is of another
    language; never retried on another node - the model is switched only
    explicitly)."""

    def __init__(
        self,
        service: str,
        kind: str,
        message: str = "",
        status_code: Optional[int] = None,
    ):
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


class Credential:
    """Token of one service endpoint (sent as ``Authorization: Token <t>``).

    ``value`` comes from service discovery (Django / its cache). Worker tokens
    rotate on every worker restart/re-registration, so after a 401 ``reload``
    first asks ``refresh`` (a rate-limited fresh discovery) for the current
    token; for the local worker it then falls back to ``fallback_file`` (the
    host-local token file) and marks the endpoint ``offline``. The caller
    retries once; a second 401 is an explicit auth error.
    """

    def __init__(
        self,
        value: str = "",
        fallback_file: Optional[str] = None,
        refresh: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self.value = value
        self.fallback_file = fallback_file
        self.refresh = refresh
        self.offline = False
        if not value and fallback_file:
            self.value = read_token(fallback_file) or ""
            self.offline = bool(self.value)

    def header(self) -> dict:
        return {"Authorization": f"Token {self.value}"} if self.value else {}

    def reload(self) -> bool:
        """After a 401: True if a different token is now available."""
        if self.refresh is not None:
            old = self.value
            fresh = self.refresh()  # may also push the new token into self
            if fresh and fresh != old:
                logger.warning("HTTP 401: token refreshed from service discovery")
                self.value = fresh
                self.offline = False
                return True
        if not self.fallback_file:
            return False
        fresh = read_token(self.fallback_file) or ""
        if not fresh or fresh == self.value:
            logger.warning(
                "HTTP 401 and local token file {} has nothing new", self.fallback_file
            )
            return False
        logger.warning(
            "HTTP 401: using the local token file {} (offline token)",
            self.fallback_file,
        )
        self.value = fresh
        self.offline = True
        return True


def raise_for_response(service: str, resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise ServiceError(
            service, "auth", f"HTTP {resp.status_code}", resp.status_code
        )
    if resp.status_code == 503:
        code, message = _error_detail(resp)
        if code.startswith("model_"):
            raise ServiceError(service, "not_ready", f"{code}: {message}", 503)
    if resp.status_code == 409:
        code, message = _error_detail(resp)
        if code == "model_language_mismatch":
            raise ServiceError(service, "wrong_language", message, 409)
    if resp.status_code >= 400:
        raise ServiceError(
            service,
            "http",
            f"HTTP {resp.status_code}: {resp.text[:200]}",
            resp.status_code,
        )


def _error_detail(resp: httpx.Response) -> tuple:
    """(code, message) of an OpenAI-style error body; ("", "") otherwise."""
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return "", ""
    if not isinstance(err, dict):
        return "", ""
    return str(err.get("code") or ""), str(err.get("message") or "")


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
        credential: Optional[Credential] = None,
        timeout_s: float = 30.0,
        connect_timeout_s: float = 3.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.credential = credential or Credential()
        self._client = httpx.Client(
            base_url=self.base_url + "/",
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if timeout_s is not None:
            kwargs["timeout"] = httpx.Timeout(
                timeout_s, connect=self._client.timeout.connect
            )
        resp = self._send(method, path, **kwargs)
        if resp.status_code == 401 and self.credential.reload():
            resp = self._send(method, path, **kwargs)  # one retry, no loop
        raise_for_response(self.service, resp)
        return resp

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(self.credential.header())
        try:
            return self._client.request(
                method, path.lstrip("/"), headers=headers, **kwargs
            )
        except httpx.TimeoutException as e:
            raise ServiceError(self.service, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(self.service, "unreachable", str(e)) from e

    def _json(self, resp: httpx.Response) -> dict:
        return json_body(self.service, resp)

    def health(self) -> bool:
        try:
            self._request("GET", "/health")
        except ServiceError:
            return False
        return True

    def get_status(self) -> dict:
        """GET /status; raises ServiceError (the UI shows why)."""
        return self._json(self._request("GET", "/status"))

    def status(self) -> Optional[dict]:
        """GET /status for the health poller: None when unavailable."""
        try:
            return self.get_status()
        except ServiceError:
            return None

    # -- service administration (settings dialog) --------------------------

    def get_config(self) -> dict:
        """GET /config: runtime values, their source and the version."""
        return self._json(self._request("GET", "/config"))

    def update_config(
        self,
        values: dict,
        version: int,
        reset: Sequence[str] = (),
        *,
        timeout_s: Optional[float] = None,
    ) -> dict:
        """POST /config; a model/device change reloads (and may download)."""
        body = {"version": version, "values": values, "reset": list(reset)}
        return self._json(
            self._request("POST", "/config", json=body, timeout_s=timeout_s)
        )

    def load_model(self, *, timeout_s: Optional[float] = None) -> dict:
        """POST /model/load: start loading the configured model (async on
        the service; poll ``get_status`` for download/load progress)."""
        return self._json(self._request("POST", "/model/load", timeout_s=timeout_s))

    def unload_model(self, *, timeout_s: Optional[float] = None) -> dict:
        """POST /model/unload: free the model's RAM/VRAM."""
        return self._json(self._request("POST", "/model/unload", timeout_s=timeout_s))

    def models(self) -> dict:
        """GET /v1/models: pinned models, ``cached`` flag, ``current``."""
        return self._json(self._request("GET", "/v1/models"))

    def close(self) -> None:
        self._client.close()
