"""/health (liveness) and /status (readiness)."""

from __future__ import annotations

import asyncio
from typing import Any, Mapping, Optional, Protocol

from fastapi import APIRouter

from .gpu import VramProbe
from .runtime import ModelState, ServiceRuntime
from .settings import BaseSettings
from .version import SERVICE_VERSION


class VersionedConfig(Protocol):
    """What /status needs from the runtime config (Task 7 RuntimeConfig)."""

    def effective(self) -> Mapping[str, Any]: ...

    def current(self) -> BaseSettings: ...


def build_health_router(
    runtime: ServiceRuntime,
    settings: BaseSettings,
    vram: VramProbe,
    config: Optional[VersionedConfig] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @router.get("/status")
    async def status() -> dict:
        ready = runtime.state is ModelState.READY
        live = config.current() if config is not None else settings
        return {
            "service": settings.service,
            "version": SERVICE_VERSION,
            "device": live.device,
            "backend": live.backend,
            "model": {
                "id": live.model,
                "loaded": runtime.backend is not None,
                "state": runtime.state.value,
                "load_seconds": runtime.load_seconds,
                "error": runtime.error,
            },
            "ready": ready,
            "vram_mb": await asyncio.to_thread(vram.read),
            "pending": runtime.worker.pending,
            "config_version": (
                config.effective()["version"] if config is not None else None
            ),
        }

    return router
