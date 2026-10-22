"""rt-asr FastAPI app factory."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Union

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from ..common.settings import render_env_value
from .backend import AsrBackend, make_backend
from .http import build_http_router
from .settings import AsrSettings


def _settings_env(settings: AsrSettings) -> Dict[str, str]:
    """Env that reproduces ``settings`` (bootstrap for the runtime config)."""
    return {
        key: render_env_value(getattr(settings, field))
        for field, key in AsrSettings.env_keys().items()
    }


def create_app(
    settings: Optional[AsrSettings] = None,
    backend_factory: Optional[Callable[[], AsrBackend]] = None,
    lifespan_hooks: Sequence[LifespanHook] = (),
    state_path: Optional[Union[str, Path]] = None,
) -> FastAPI:
    settings = settings or AsrSettings.from_env()
    token = load_token(settings.api_token_file)
    state = state_path or settings.config_state
    config = RuntimeConfig(
        AsrSettings, _settings_env(settings), Path(state) if state else None
    )
    config.restore()
    runtime = ServiceRuntime(
        settings.service,
        backend_factory or (lambda: make_backend(config.current())),
        settings.max_pending,
    )
    return create_service_app(
        settings=settings,
        runtime=runtime,
        token=token,
        routers=[build_http_router(config, runtime)],
        lifespan_hooks=lifespan_hooks,
        config=config,
        on_reload=lambda s: runtime.reload(lambda: make_backend(s)),
    )
