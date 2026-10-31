"""POST /v1/audio/transcriptions: the rt-asr request shape (multipart file +
language), answered with Vosk's words and their per-word confidence."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from ..common.audio import AudioDecodeError, decode_audio_file
from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from ..common.worker import Priority
from .settings import VoskSettings


def pick_language(requested: Optional[str], loaded: tuple[str, ...]) -> str:
    """The request's language; may be omitted only with a single model.

    Vosk cannot detect the language, so "auto" counts as omitted."""
    code = (requested or "").strip().lower()
    if code in ("", "auto"):
        if len(loaded) == 1:
            return loaded[0]
        raise ApiError(
            400,
            "invalid_request",
            f"language is required (loaded: {', '.join(loaded)})",
        )
    if code not in loaded:
        raise ApiError(
            400,
            "unsupported_language",
            f"no Vosk model for {code!r} (loaded: {', '.join(loaded)})",
        )
    return code


def build_http_router(
    config: RuntimeConfig[VoskSettings], runtime: ServiceRuntime
) -> APIRouter:
    router = APIRouter()

    @router.post("/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: Optional[str] = Form(None),  # accepted for OpenAI compatibility
        language: Optional[str] = Form(None),
    ) -> dict:
        s = config.current()
        backend = await runtime.acquire()
        lang = pick_language(language, backend.languages())
        limit = s.max_upload_mb * 1024 * 1024
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                413, "payload_too_large", f"file exceeds {s.max_upload_mb} MB"
            )
        try:
            audio = await asyncio.to_thread(decode_audio_file, data)
        except AudioDecodeError as exc:
            raise ApiError(400, "invalid_audio", str(exc)) from exc
        result = await runtime.run(
            backend.transcribe, audio, lang, priority=Priority.REQUEST
        )
        return {
            "text": result.text,
            "language": lang,
            "words": [asdict(w) for w in result.words],
        }

    return router
