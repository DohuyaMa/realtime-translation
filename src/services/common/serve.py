"""Common main() for `python -m src.services.<svc>`."""

from __future__ import annotations

import os
import sys
from typing import Callable, Mapping

from fastapi import FastAPI

from .env import SettingsError
from .logs import setup_logging
from .settings import BaseSettings, apply_model_dir


def run_service(
    settings_loader: Callable[[Mapping[str, str]], BaseSettings],
    app_factory: Callable[[BaseSettings], FastAPI],
) -> int:
    try:
        settings = settings_loader(os.environ)
        setup_logging(settings.log_level)
        apply_model_dir(settings)
        app = app_factory(settings)
    except SettingsError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_config=None,
        log_level=settings.log_level,
    )
    return 0
