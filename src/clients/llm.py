from __future__ import annotations

import re
from typing import Optional, Protocol, Sequence

import httpx

from ..core.runtime import LlmSettings
from .base import ServiceError, json_body, raise_for_response

LANGUAGE_NAMES = {"pl": "Polish", "uk": "Ukrainian", "ru": "Russian"}
SYSTEM_PROMPT = (
    "You are a professional interpreter. Translate the user's {lang} speech transcript "
    "into natural English. Output only the English translation, nothing else. "
    "Fix obvious speech-recognition errors using context."
)
_THINK = re.compile(r"(?s)<think>.*?</think>")


class LlmTarget(Protocol):
    def current(self) -> tuple[str, str]: ...

    def invalidate(self, kind: str) -> None: ...


class LlmClient:
    service = "llm"

    def __init__(
        self,
        target: LlmTarget,
        settings: LlmSettings,
        *,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._target = target
        self._settings = settings
        self._http = httpx.Client(timeout=settings.timeout_s, transport=transport)

    @property
    def base_url(self) -> str:
        return self._target.current()[0]

    def close(self) -> None:
        self._http.close()

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        base, token = self._target.current()
        headers = {"Authorization": f"Token {token}"} if token else {}
        try:
            resp = self._http.request(
                method, base.rstrip("/") + path, headers=headers, **kwargs
            )
        except httpx.TimeoutException as e:
            raise ServiceError(self.service, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(self.service, "unreachable", str(e)) from e
        if resp.status_code == 503:
            raise ServiceError(self.service, "busy", "HTTP 503", 503)
        raise_for_response(self.service, resp)
        return resp

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._send(method, path, **kwargs)
        except ServiceError as e:
            if e.kind not in ("auth", "unreachable", "timeout", "busy"):
                raise
            self._target.invalidate(e.kind)
        return self._send(method, path, **kwargs)

    def translate(
        self, text: str, source_lang: str, context: Sequence[tuple[str, str]] = ()
    ) -> str:
        if source_lang == "en":
            return text
        if source_lang not in LANGUAGE_NAMES:
            raise ValueError(f"unsupported source language {source_lang!r}")
        s = self._settings
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(lang=LANGUAGE_NAMES[source_lang]),
            }
        ]
        for src, tr in context:
            messages.append({"role": "user", "content": src})
            messages.append({"role": "assistant", "content": tr})
        messages.append({"role": "user", "content": text})
        body = {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": min(s.max_tokens_cap, 32 + 3 * len(text.split())),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        payload = json_body(
            self.service, self._request("POST", "/v1/chat/completions", json=body)
        )
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ServiceError(
                self.service, "http", f"malformed response: {payload!r:.200}"
            ) from e
        result = _THINK.sub("", content or "").strip()
        if not result:
            raise ServiceError(self.service, "http", "empty translation")
        return result

    def status(self) -> dict:
        # Health probe: single attempt, never invalidates the session target.
        return json_body(self.service, self._send("GET", "/status"))
