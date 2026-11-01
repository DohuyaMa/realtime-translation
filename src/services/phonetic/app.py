"""rt-phonetic FastAPI app factory (HTTP only: one request per unit)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence, Union

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from .backend import PhoneticBackend, make_backend
from .http import build_http_router
from .settings import PhoneticSettings


def create_app(
    settings: Optional[PhoneticSettings] = None,
    backend_factory: Optional[Callable[[], PhoneticBackend]] = None,
    lifespan_hooks: Sequence[LifespanHook] = (),
    state_path: Optional[Union[str, Path]] = None,
    bootstrap_env: Optional[Mapping[str, str]] = None,
) -> FastAPI:
    env = os.environ if bootstrap_env is None else bootstrap_env
    settings = settings or PhoneticSettings.from_env(env)
    token = load_token(settings.api_token_file)
    state = state_path or settings.config_state
    config = RuntimeConfig(PhoneticSettings, env, Path(state) if state else None)
    config.restore()
    runtime = ServiceRuntime(
        settings.service,
        backend_factory or (lambda: make_backend(config.current())),
        settings.max_pending,
        idle_unload_s=settings.idle_unload_minutes * 60.0,
    )
    return create_service_app(
        settings=settings,
        runtime=runtime,
        token=token,
        routers=[build_http_router(config, runtime)],
        lifespan_hooks=lifespan_hooks,
        config=config,
        on_reload=lambda _s: runtime.reload(),
    )
