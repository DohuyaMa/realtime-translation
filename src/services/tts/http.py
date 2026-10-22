"""POST /v1/audio/speech, GET /v1/audio/voices. No playback on the server."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

import numpy as np
from fastapi import APIRouter
from fastapi.responses import Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from ..common.audio import float_to_pcm16, resample
from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from ..common.worker import Priority
from .backend import UnsupportedVoiceLanguage
from .encoding import wav_bytes, wav_header
from .settings import TtsSettings

FORMATS = {"wav": "audio/wav", "pcm": "audio/pcm"}
_DONE = object()


class SpeechRequest(BaseModel):
    input: str
    voice: Optional[str] = None
    speed: Optional[float] = Field(default=None, ge=0.25, le=4.0)
    response_format: str = "wav"
    stream: bool = False
    model: Optional[str] = None  # accepted for OpenAI compatibility, ignored


def build_http_router(
    config: RuntimeConfig[TtsSettings], runtime: ServiceRuntime
) -> APIRouter:
    router = APIRouter()

    @router.post("/audio/speech")
    async def speech(req: SpeechRequest):
        s = config.current()
        backend = runtime.require_ready()
        text = req.input.strip()
        if not text:
            raise ApiError(400, "invalid_request", "input must not be empty")
        if len(req.input) > s.max_chars:
            raise ApiError(
                413, "payload_too_large", f"input exceeds {s.max_chars} characters"
            )
        fmt = req.response_format.lower()
        if fmt not in FORMATS:
            raise ApiError(
                400, "unsupported_format", "response_format must be 'wav' or 'pcm'"
            )
        voice = req.voice or s.voice
        if voice not in {v.id for v in backend.voices()}:
            raise ApiError(400, "unknown_voice", f"unknown voice {voice!r}")
        speed = req.speed or s.speed
        rate = s.sample_rate
        headers = {"X-Sample-Rate": str(rate)}
        try:
            await runtime.run(backend.prepare_voice, voice, priority=Priority.REQUEST)
        except UnsupportedVoiceLanguage as exc:
            raise ApiError(400, "unsupported_voice_language", str(exc)) from exc

        def convert(chunk: np.ndarray) -> bytes:
            return float_to_pcm16(resample(chunk, backend.sample_rate, rate))

        if not req.stream:

            def job() -> bytes:
                return b"".join(
                    convert(c) for c in backend.synthesize(text, voice, speed)
                )

            pcm = await runtime.run(job, priority=Priority.REQUEST)
            payload = wav_bytes(pcm, rate) if fmt == "wav" else pcm
            return Response(payload, media_type=FORMATS[fmt], headers=headers)

        loop = asyncio.get_running_loop()
        chunks: "asyncio.Queue[Any]" = asyncio.Queue()
        cancel = threading.Event()

        def stream_job() -> None:
            try:
                for chunk in backend.synthesize(text, voice, speed):
                    if cancel.is_set():
                        break
                    loop.call_soon_threadsafe(chunks.put_nowait, convert(chunk))
            except Exception as exc:  # noqa: BLE001 - reported by ending the stream
                loop.call_soon_threadsafe(chunks.put_nowait, exc)
                return
            loop.call_soon_threadsafe(chunks.put_nowait, _DONE)

        fut = runtime.submit_threadsafe(stream_job, priority=Priority.REQUEST)

        async def body():
            try:
                if fmt == "wav":
                    yield wav_header(rate, None)
                while True:
                    item = await chunks.get()
                    if item is _DONE:
                        return
                    if isinstance(item, Exception):
                        logger.error("streaming synthesis failed: {}", item)
                        return
                    yield item
            finally:
                cancel.set()
                fut.cancel()

        return StreamingResponse(body(), media_type=FORMATS[fmt], headers=headers)

    @router.get("/audio/voices")
    async def voices() -> dict:
        backend = runtime.require_ready()
        return {
            "voices": [
                {"id": v.id, "language": v.language, "gender": v.gender}
                for v in backend.voices()
            ],
            "default": config.current().voice,
        }

    return router
