"""/health (liveness), /status (readiness, model state, memory) and the
explicit model controls ``POST /model/load`` / ``POST /model/unload``."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping, Optional, Protocol

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from .gpu import VramProbe
from .memory import process_memory
from .runtime import ServiceRuntime
from .settings import BaseSettings
from .version import SERVICE_VERSION

# Service-specific /status fields (rt-asr: dropped hallucination counters).
StatusExtra = Callable[[], Mapping[str, Any]]


class VersionedConfig(Protocol):
    """What /status needs from the runtime config (Task 7 RuntimeConfig)."""

    def effective(self) -> Mapping[str, Any]: ...

    def current(self) -> BaseSettings: ...


def _live(settings: BaseSettings, config: Optional[VersionedConfig]) -> BaseSettings:
    return config.current() if config is not None else settings


def model_status(runtime: ServiceRuntime, live: BaseSettings) -> dict:
    """The model of the service: a model change always reloads, so the
    configured model is the one loaded / loading / failed."""
    state, error, download = runtime.snapshot()
    return {
        "id": live.model,
        "state": state.value,
        "error": error,
        "download": download,
        "device": live.device,
    }


def build_health_router(
    runtime: ServiceRuntime,
    settings: BaseSettings,
    vram: VramProbe,
    config: Optional[VersionedConfig] = None,
    status_extra: Optional[StatusExtra] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict:
        # never triggers a load: reports the model state as is
        return {"status": "ok", "model": {"state": runtime.snapshot()[0].value}}

    @router.get("/status")
    async def status() -> dict:
        live = _live(settings, config)
        memory = process_memory()
        memory["vram_mb"] = await asyncio.to_thread(vram.read, live.device)
        body = {
            "service": settings.service,
            "version": SERVICE_VERSION,
            "backend": live.backend,
            "model": model_status(runtime, live),
            # an idle-unloaded model is ready: the next request loads it
            "ready": runtime.ready,
            "memory": memory,
            "pending": runtime.worker.pending,
            "config_version": (
                config.effective()["version"] if config is not None else None
            ),
        }
        if status_extra is not None:
            body.update(status_extra())
        return body

    return router


def build_model_router(
    runtime: ServiceRuntime,
    settings: BaseSettings,
    config: Optional[VersionedConfig] = None,
) -> APIRouter:
    """Explicit model controls (behind the same auth as /config). A model
    change is ``POST /config`` (it unloads, then loads the new model)."""
    router = APIRouter()

    @router.post("/model/load")
    async def load() -> dict:
        runtime.load()  # async: poll /status
        return {"model": model_status(runtime, _live(settings, config))}

    @router.post("/model/unload")
    async def unload() -> dict:
        await run_in_threadpool(runtime.unload)
        return {"model": model_status(runtime, _live(settings, config))}

    return router
