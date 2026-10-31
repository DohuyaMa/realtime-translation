"""POST /v1/audio/transcriptions (OpenAI-compatible, spec section 4.3)."""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import PlainTextResponse, Response

from ..common.audio import TARGET_RATE, AudioDecodeError, decode_audio_file
from ..common.env import SettingsError
from ..common.errors import ApiError
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from ..common.worker import Priority
from .backend import DecodeOptions, Segment
from .chunking import split_for_transcription
from .settings import AsrSettings, normalize_language
from .textfilter import SegmentFilter, filter_repetitions

RESPONSE_FORMATS = ("json", "verbose_json", "text")
PRIORITIES = ("auto", "realtime", "batch")
CHUNK_SECONDS_RANGE = (30.0, 900.0)
DETECT_SECONDS = 30
PROMPT_TAIL_CHARS = 200


def _join(segments: List[Segment]) -> str:
    return " ".join(t for t in (s.text.strip() for s in segments) if t)


def build_http_router(
    config: RuntimeConfig[AsrSettings],
    runtime: ServiceRuntime,
    segment_filter: SegmentFilter,
) -> APIRouter:
    router = APIRouter()

    def kept(segments: List[Segment]) -> List[Segment]:
        return [s for s in segments if segment_filter.keep(s.text, s.stats)]

    @router.post("/audio/transcriptions")
    async def transcriptions(
        request: Request,
        file: UploadFile = File(...),
        model: Optional[str] = Form(None),  # accepted for OpenAI compatibility
        language: Optional[str] = Form(None),
        prompt: Optional[str] = Form(None),
        temperature: float = Form(0.0, ge=0.0, le=1.0),
        response_format: str = Form("json"),
        chunk_seconds: Optional[float] = Form(None),
        priority: str = Form("auto"),
    ):
        s = config.current()
        if response_format not in RESPONSE_FORMATS:
            raise ApiError(
                400,
                "unsupported_format",
                f"response_format must be one of {RESPONSE_FORMATS}",
            )
        if priority not in PRIORITIES:
            raise ApiError(
                400, "invalid_request", f"priority must be one of {PRIORITIES}"
            )
        lo, hi = CHUNK_SECONDS_RANGE
        if chunk_seconds is not None and not lo <= chunk_seconds <= hi:
            raise ApiError(
                400, "invalid_request", f"chunk_seconds must be within {lo}..{hi}"
            )
        chunk = chunk_seconds if chunk_seconds is not None else s.chunk_seconds
        try:
            requested_language = normalize_language(language)
        except SettingsError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc

        backend = await runtime.acquire()
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
        duration = len(audio) / TARGET_RATE

        realtime = priority == "realtime" or (
            priority == "auto" and duration <= s.realtime_max_seconds
        )
        job_priority = Priority.REQUEST if realtime else Priority.BATCH

        lang = requested_language or s.language
        if lang is None:
            lang = await runtime.run(
                backend.detect_language,
                audio[: TARGET_RATE * DETECT_SECONDS],
                s.languages,
                priority=job_priority,
            )
        opts = DecodeOptions(
            language=lang,
            prompt=prompt or None,
            temperature=temperature,
            beam_size=s.beam_size,
            vad_filter=s.vad_filter,
        )

        if realtime:
            result = await runtime.run(
                backend.transcribe, audio, opts, priority=Priority.REQUEST
            )
            segments = kept(result.segments)
            text_raw = _join(segments)
            lang, duration, chunks = result.language, result.duration, 1
        else:
            bounds = await asyncio.to_thread(
                split_for_transcription, audio, chunk, s.chunk_lookback_seconds
            )
            segments = []
            cancel = threading.Event()
            chunk_opts = opts
            for i, (a, b) in enumerate(bounds):
                if i > 0 and await request.is_disconnected():
                    cancel.set()
                    return Response(status_code=499)  # nobody is listening
                segs = kept(
                    await runtime.run_iter(
                        backend.transcribe_iter,
                        audio[a:b],
                        chunk_opts,
                        priority=Priority.BATCH,
                        cancel=cancel,
                    )
                )
                offset = a / TARGET_RATE
                segments.extend(
                    Segment(seg.start + offset, seg.end + offset, seg.text)
                    for seg in segs
                )
                tail = filter_repetitions(_join(segs))[-PROMPT_TAIL_CHARS:]
                chunk_opts = dataclasses.replace(opts, prompt=tail or None)
            text_raw, chunks = _join(segments), len(bounds)

        text = filter_repetitions(text_raw.strip())
        if response_format == "text":
            return PlainTextResponse(text)
        body: dict = {"text": text, "language": lang}
        if response_format == "verbose_json":
            body["duration"] = round(duration, 3)
            body["segments"] = [
                {
                    "id": i,
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": filter_repetitions(seg.text.strip()),
                }
                for i, seg in enumerate(segments)
            ]
            body["chunks"] = chunks
        return body

    return router
