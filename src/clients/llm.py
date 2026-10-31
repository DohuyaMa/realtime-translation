from __future__ import annotations

import re
from typing import Optional, Protocol, Sequence

import httpx

from ..core.prompt import build_messages, split_words
from ..core.runtime import LlmSettings
from .base import ServiceError, json_body, raise_for_response

SOURCE_LANGUAGES = ("pl", "uk", "ru")
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
        try:
            return self._send(method, path, **kwargs)
        except ServiceError as e:
            if e.kind == "auth":  # still rejected: let the target fail over
                self._target.invalidate("auth")
            raise

    def translate(
        self, text: str, source_lang: str, context: Sequence[tuple[str, str]] = ()
    ) -> str:
        if source_lang == "en":
            return text
        if source_lang not in SOURCE_LANGUAGES:
            raise ValueError(f"unsupported source language {source_lang!r}")
        s = self._settings
        ctx = tuple(context)
        results = []
        # one call per chunk of <= words_per_request words, results in order;
        # earlier chunks of this phrase become context of the next ones
        for chunk in split_words(text, s.words_per_request):
            result = self._complete(
                build_messages(
                    s.system_prompt, s.user_template, chunk, source_lang, ctx
                ),
                chunk,
            )
            results.append(result)
            ctx = ctx + ((chunk, result),)
        return " ".join(results)

    def _complete(self, messages: list, chunk: str) -> str:
        s = self._settings
        body = {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": min(s.max_tokens_cap, 32 + 3 * len(chunk.split())),
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
