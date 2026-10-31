from __future__ import annotations

import json
import re
from typing import Iterable, Iterator, Optional, Protocol, Sequence

import httpx

from ..core.crosscheck import Aligned
from ..core.prompt import build_messages, split_words
from ..core.runtime import LlmSettings
from .base import ServiceError, json_body, raise_for_response

SOURCE_LANGUAGES = ("pl", "uk", "ru")
# a cold worker loads the model on the first request (11 s live, 2026-10-31)
WARM_UP_TIMEOUT_S = 120.0
_THINK = re.compile(r"(?s)<think>.*?</think>")
_SENTENCE = re.compile(r"(?s)(.*?[.!?…]+[\"')\]]*)(\s+|$)")


def _sentences(deltas: Iterable[str]) -> Iterator[str]:
    """Complete sentences out of streamed text deltas (``<think>`` dropped)."""
    buf = ""
    done = 0  # chars of the visible text already emitted
    for delta in deltas:
        buf += delta
        visible = _THINK.sub("", buf)
        if "<think>" in visible:  # reasoning still open: hold it back
            visible = visible[: visible.index("<think>")]
        rest = visible[done:]
        while True:
            m = _SENTENCE.match(rest)
            if not m or not m.group(2):  # need whitespace after the end mark
                break
            sentence = m.group(1).strip()
            done += m.end()
            rest = visible[done:]
            if sentence:
                yield sentence
    tail = _THINK.sub("", buf)[done:].strip()
    if "<think>" in tail:
        tail = tail[: tail.index("<think>")].strip()
    if tail:
        yield tail


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

    def warm_up(self) -> None:
        """One tiny completion (1 token) so the worker loads the model now,
        not on the first phrase. Single attempt: never fails the target over;
        errors are raised to the caller."""
        s = self._settings
        body = {
            "model": s.model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 1,
            "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        timeout = max(WARM_UP_TIMEOUT_S, s.timeout_s)
        self._send("POST", "/v1/chat/completions", json=body, timeout=timeout)

    def translate(
        self,
        text: str,
        source_lang: str,
        context: Sequence[tuple] = (),
        check: Optional[Aligned] = None,
    ) -> str:
        """Whole translation (joined stream)."""
        return " ".join(self.translate_stream(text, source_lang, context, check))

    def translate_stream(
        self,
        text: str,
        source_lang: str,
        context: Sequence[tuple] = (),
        check: Optional[Aligned] = None,
    ) -> Iterator[str]:
        """Translated sentences as soon as each one is complete.

        One streamed call per chunk of <= words_per_request words (in order);
        earlier chunks of this phrase become context of the next ones.

        ``check`` (TASK-SYS-12): the Vosk transcript aligned with ``text``
        (whisper) - two-text mode with the check prompts; each chunk gets
        the Vosk words of its time span. Context items are ``(source,
        translation)`` or ``(source, translation, check)``; in two-text mode
        only the latter are used (a whisper-only pair has no Vosk text).
        """
        if source_lang == "en":
            yield text
            return
        if source_lang not in SOURCE_LANGUAGES:
            raise ValueError(f"unsupported source language {source_lang!r}")
        s = self._settings
        if check is None:
            system, template = s.system_prompt, s.user_template
            ctx = tuple(context)
        else:
            system, template = s.check_system_prompt, s.check_user_template
            ctx = tuple(c for c in context if len(c) > 2)
        rest = check
        for chunk in split_words(text, s.words_per_request):
            chunk_check = None
            if rest is not None:
                part, rest = rest.cut(len(chunk.split()))
                chunk_check = part.check_text
            messages = build_messages(
                system, template, chunk, source_lang, ctx, check=chunk_check
            )
            parts = []
            for sentence in self._complete_stream(messages, chunk):
                parts.append(sentence)
                yield sentence
            if not parts:
                raise ServiceError(self.service, "http", "empty translation")
            done = " ".join(parts)
            ctx = ctx + (
                (chunk, done) if chunk_check is None else (chunk, done, chunk_check),
            )

    def _open_stream(self, body: dict) -> httpx.Response:
        """POST with stream=True; same one-retry policy as ``_request``."""
        for attempt in (0, 1):
            base, token = self._target.current()
            headers = {"Authorization": f"Token {token}"} if token else {}
            req = self._http.build_request(
                "POST",
                base.rstrip("/") + "/v1/chat/completions",
                json=body,
                headers=headers,
            )
            try:
                try:
                    resp = self._http.send(req, stream=True)
                except httpx.TimeoutException as e:
                    raise ServiceError(self.service, "timeout", str(e)) from e
                except httpx.TransportError as e:
                    raise ServiceError(self.service, "unreachable", str(e)) from e
                if resp.status_code >= 400:
                    resp.read()
                    resp.close()
                    if resp.status_code == 503:
                        raise ServiceError(self.service, "busy", "HTTP 503", 503)
                    raise_for_response(self.service, resp)
                return resp
            except ServiceError as e:
                retryable = e.kind in ("auth", "unreachable", "timeout", "busy")
                if attempt == 1 or not retryable:
                    if attempt == 1 and e.kind == "auth":
                        self._target.invalidate("auth")
                    raise
                self._target.invalidate(e.kind)
        raise AssertionError("unreachable")

    def _complete_stream(self, messages: list, chunk: str) -> Iterator[str]:
        s = self._settings
        body = {
            "model": s.model,
            "messages": messages,
            "temperature": s.temperature,
            "max_tokens": min(s.max_tokens_cap, 32 + 3 * len(chunk.split())),
            "chat_template_kwargs": {"enable_thinking": False},
            "stream": True,
        }
        resp = self._open_stream(body)
        try:
            if "text/event-stream" in resp.headers.get("content-type", ""):
                deltas = self._sse_deltas(resp)
            else:  # server answered without streaming: one JSON body
                resp.read()
                deltas = iter([self._message_content(json_body(self.service, resp))])
            yield from _sentences(deltas)
        except httpx.TimeoutException as e:
            raise ServiceError(self.service, "timeout", str(e)) from e
        except httpx.TransportError as e:
            raise ServiceError(self.service, "unreachable", str(e)) from e
        finally:
            resp.close()

    def _sse_deltas(self, resp: httpx.Response) -> Iterator[str]:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            try:
                event = json.loads(data)
                delta = event["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError, TypeError) as e:
                raise ServiceError(
                    self.service, "http", f"malformed stream event: {data!r:.200}"
                ) from e
            if delta.get("content"):
                yield delta["content"]

    def _message_content(self, payload: dict) -> str:
        try:
            return payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ServiceError(
                self.service, "http", f"malformed response: {payload!r:.200}"
            ) from e

    def status(self) -> dict:
        # Health probe: single attempt, never invalidates the session target.
        return json_body(self.service, self._send("GET", "/status"))
