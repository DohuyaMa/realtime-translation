"""rt-asr FastAPI app factory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Sequence, Union

from fastapi import FastAPI

from ..common.app import LifespanHook, create_service_app
from ..common.auth import load_token
from ..common.env import SettingsError
from ..common.runtime import ServiceRuntime
from ..common.runtime_config import RuntimeConfig
from ..common.settings import render_env_value
from ..common.stream import StreamError, StreamHub, StreamStart, build_stream_router
from .backend import AsrBackend, make_backend
from .http import build_http_router
from .settings import AsrSettings, normalize_language
from .stream import AsrStreamSession
from .textfilter import SegmentFilter


def _stream_factory(config: RuntimeConfig[AsrSettings]):
    def open_session(start: StreamStart, backend: AsrBackend) -> AsrStreamSession:
        s = config.current()
        try:
            language = normalize_language(start.language) or s.language
        except SettingsError as exc:
            raise StreamError("invalid_request", str(exc)) from exc
        return AsrStreamSession(
            backend,
            language=language,
            languages=s.languages,
            prompt=start.prompt,
            t0=start.t0,
        )

    return open_session


def _settings_env(settings: AsrSettings) -> Dict[str, str]:
    """Env that reproduces ``settings``.

    Fallback only: it pins device-dependent defaults (model, compute_type,
    beam_size), so a runtime ``device`` change would not re-derive them.
    """
    return {
        key: render_env_value(getattr(settings, field))
        for field, key in AsrSettings.env_keys().items()
    }


def create_app(
    settings: Optional[AsrSettings] = None,
    backend_factory: Optional[Callable[[], AsrBackend]] = None,
    lifespan_hooks: Sequence[LifespanHook] = (),
    state_path: Optional[Union[str, Path]] = None,
    bootstrap_env: Optional[Mapping[str, str]] = None,
) -> FastAPI:
    """``bootstrap_env`` is the raw env ``settings`` came from (the runtime
    config re-derives defaults from it); main() passes ``os.environ``."""
    if settings is None:
        bootstrap_env = os.environ if bootstrap_env is None else bootstrap_env
        settings = AsrSettings.from_env(bootstrap_env)
    elif bootstrap_env is None:
        bootstrap_env = _settings_env(settings)
    token = load_token(settings.api_token_file)
    state = state_path or settings.config_state
    config = RuntimeConfig(AsrSettings, bootstrap_env, Path(state) if state else None)
    config.restore()
    runtime = ServiceRuntime(
        settings.service,
        backend_factory or (lambda: make_backend(config.current())),
        settings.max_pending,
        idle_unload_s=settings.idle_unload_minutes * 60.0,
    )
    segment_filter = SegmentFilter()
    hub = StreamHub(
        runtime,
        _stream_factory(config),
        max_sessions=settings.max_sessions,
        tick_s=settings.stream_tick_ms / 1000.0,
    )
    return create_service_app(
        settings=settings,
        runtime=runtime,
        token=token,
        routers=[build_http_router(config, runtime, segment_filter)],
        ws_routers=[build_stream_router(hub, token)],
        lifespan_hooks=lifespan_hooks,
        config=config,
        on_reload=lambda _s: runtime.reload(),
        status_extra=lambda: {
            "dropped_segments": segment_filter.counts(),
            "sessions": hub.status(),
        },
    )
