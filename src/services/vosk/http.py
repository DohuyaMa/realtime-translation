"""POST /v1/audio/transcriptions: the rt-asr request shape (multipart file +
language), answered with Vosk's words and their per-word confidence. The
language must be the one of the single loaded model (else 409)."""

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


def pick_language(requested: Optional[str], model_id: str, loaded: str) -> str:
    """The request's language; must be the loaded model's language.

    Vosk cannot detect the language, so omitted / "auto" means the model's.
    Another language is refused (409): the service never switches its model
    by itself - that is an explicit ``POST /config``."""
    code = (requested or "").strip().lower()
    if code in ("", "auto"):
        return loaded
    if code != loaded:
        raise ApiError(
            409,
            "model_language_mismatch",
            f"loaded Vosk model {model_id} is {loaded!r}, not {code!r};"
            " switch the model via POST /config",
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
        lang = pick_language(language, backend.model_id, backend.language)
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
