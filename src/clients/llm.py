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
_CLAUSE = re.compile(r"[,;:]+[\"')\]]*(?=\s)")
# a clause mark ends a TTS piece once the piece has this many words: the
# first audio of a long translation is ready after one clause, not after
# the whole sentence (Kokoro takes ~0.2 s per second of audio on CPU)
PIECE_CLAUSE_WORDS = 4


def _cut(rest: str) -> Optional[tuple[str, int]]:
    """The first complete piece of ``rest`` and the chars it consumes: up
    to a sentence end, or up to a clause mark after >= PIECE_CLAUSE_WORDS
    words; None = not complete yet (needs whitespace after the mark)."""
    m = _SENTENCE.match(rest)
    stop = m.end(1) if m and m.group(2) else len(rest)
    for c in _CLAUSE.finditer(rest, 0, stop):
        if len(rest[: c.end()].split()) >= PIECE_CLAUSE_WORDS:
            ws = re.match(r"\s+", rest[c.end() :])
            return rest[: c.end()], c.end() + (ws.end() if ws else 0)
    if m and m.group(2):
        return m.group(1), m.end()
    return None


def _pieces(deltas: Iterable[str]) -> Iterator[str]:
    """TTS pieces (sentences, long ones cut at clauses) out of streamed text
    deltas (``<think>`` dropped)."""
    buf = ""
    done = 0  # chars of the visible text already emitted
    for delta in deltas:
        buf += delta
        visible = _THINK.sub("", buf)
        if "<think>" in visible:  # reasoning still open: hold it back
            visible = visible[: visible.index("<think>")]
        while True:
            cut = _cut(visible[done:])
            if cut is None:
                break
            piece, used = cut
            done += used
            if piece.strip():
                yield piece.strip()
    tail = _THINK.sub("", buf)[done:].strip()
    if "<think>" in tail:
        tail = tail[: tail.index("<think>")].strip()
    while tail:  # the last piece needs no whitespace after its mark
        cut = _cut(tail + " ")
        if cut is None:
            yield tail
            return
        piece, used = cut
        if piece.strip():
            yield piece.strip()
        tail = (tail + " ")[used:].strip()


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
        sound: Optional[Aligned] = None,
        next_text: str = "",
    ) -> str:
        """Whole translation (joined stream)."""
        return " ".join(
            self.translate_stream(text, source_lang, context, sound, next_text)
        )

    def translate_stream(
        self,
        text: str,
        source_lang: str,
        context: Sequence[tuple] = (),
        sound: Optional[Aligned] = None,
        next_text: str = "",
        insist_complete: bool = False,
    ) -> Iterator[str]:
        """Translated TTS pieces (``_pieces``) as soon as each one is complete.

        One streamed call per chunk of <= words_per_request words (in order);
        earlier chunks of this phrase become context of the next ones.

        ``sound``: rt-phonetic's letter transcript aligned with ``text``
        (whisper) - sound mode ("condition F") with the sound prompts; each
        chunk gets the phonetic words of its time span. Context items are
        ``(source, translation)`` or ``(source, translation, sound)``; in
        sound mode only the latter are used (a whisper-only pair has no
        SOUND).

        ``next_text``: speech recognized after ``text`` (the NEXT line, for
        understanding only); a later chunk of the same text is part of an
        earlier chunk's NEXT.

        ``insist_complete``: the retry of a ``too_short`` reply - every
        chunk's request ends with ``COMPLETE_RETRY_NOTE``.
        """
        if source_lang == "en":
            yield text
            return
        if source_lang not in SOURCE_LANGUAGES:
            raise ValueError(f"unsupported source language {source_lang!r}")
        s = self._settings
        if sound is None:
            system, template = s.system_prompt, s.user_template
            ctx = tuple(context)
        else:
            system, template = s.sound_system_prompt, s.sound_user_template
            ctx = tuple(c for c in context if len(c) > 2)
        rest = sound
        chunks = split_words(text, s.words_per_request)
        for i, chunk in enumerate(chunks):
            chunk_sound = None
            if rest is not None:
                part, rest = rest.cut(len(chunk.split()))
                chunk_sound = part.sound_text
            messages = build_messages(
                system,
                template,
                chunk,
                source_lang,
                ctx,
                sound=chunk_sound,
                terms=s.terms,
                next_text=" ".join(chunks[i + 1 :] + [next_text]).strip(),
                insist_complete=insist_complete,
            )
            parts = []
            for sentence in self._complete_stream(messages, chunk):
                parts.append(sentence)
                yield sentence
            if not parts:
                raise ServiceError(self.service, "http", "empty translation")
            done = " ".join(parts)
            ctx = ctx + (
                (chunk, done) if chunk_sound is None else (chunk, done, chunk_sound),
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
        # no "model": the worker translates with its loaded model (llm_worker.py)
        body = {
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
            yield from _pieces(deltas)
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
