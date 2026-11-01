"""FastAPI app skeleton shared by the speech services (rt-asr, rt-phonetic, rt-tts)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable, Optional, Sequence

from fastapi import APIRouter, Depends, FastAPI

from .auth import make_bearer_dependency
from .errors import install_error_handlers
from .gpu import VramProbe
from .health import StatusExtra, build_health_router, build_model_router
from .models_http import build_models_router
from .runtime_config import RuntimeConfig, build_config_router
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
    config: Optional[RuntimeConfig[Any]] = None,
    on_reload: Optional[Callable[[Any], None]] = None,
    status_extra: Optional[StatusExtra] = None,
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
            runtime, settings, vram or VramProbe(), config, status_extra
        )
    )

    auth = [Depends(make_bearer_dependency(token))]
    app.include_router(build_model_router(runtime, settings, config), dependencies=auth)
    if config is not None:
        app.include_router(
            build_config_router(config, on_reload),
            dependencies=auth,
        )

    v1 = APIRouter(prefix="/v1", dependencies=auth)
    for router in routers:
        v1.include_router(router)
    if config is not None:
        v1.include_router(
            build_models_router(settings.service.removeprefix("rt-"), config)
        )
    app.include_router(v1)
    for router in ws_routers:  # WS handlers check the token themselves (close 1008)
        app.include_router(router, prefix="/v1")
    return app
