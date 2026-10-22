"""FastAPI app skeleton shared by rt-asr and rt-tts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Optional, Sequence

from fastapi import APIRouter, Depends, FastAPI

from .auth import make_bearer_dependency
from .errors import install_error_handlers
from .gpu import VramProbe
from .health import VersionedConfig, build_health_router
from .runtime import ServiceRuntime
from .settings import BaseSettings
from .version import SERVICE_VERSION

# A hook gets the runtime at startup and returns a stop function.
LifespanHook = Callable[[ServiceRuntime], Callable[[], None]]


def create_service_app(
    *,
    settings: BaseSettings,
    runtime: ServiceRuntime,
    token: Optional[str],
    routers: Sequence[APIRouter] = (),
    ws_routers: Sequence[APIRouter] = (),
    lifespan_hooks: Sequence[LifespanHook] = (),
    vram: Optional[VramProbe] = None,
    config: Optional[VersionedConfig] = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.start()
        stops = [hook(runtime) for hook in lifespan_hooks]
        try:
            yield
        finally:
            for stop in reversed(stops):
                stop()
            runtime.stop()

    app = FastAPI(title=settings.service, version=SERVICE_VERSION, lifespan=lifespan)
    app.state.runtime = runtime
    app.state.settings = settings
    app.state.config = config
    install_error_handlers(app)
    app.include_router(
        build_health_router(
            runtime, settings, vram or VramProbe(settings.device), config
        )
    )

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(make_bearer_dependency(token))])
    for router in routers:
        v1.include_router(router)
    app.include_router(v1)
    for router in ws_routers:  # WS handlers check the token themselves (close 1008)
        app.include_router(router, prefix="/v1")
    return app
