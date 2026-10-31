"""GET /v1/models: pinned models of this service and which are provisioned.

Switching the model is ``POST /config {"values": {"model": <id>}}`` (RELOAD
field): a model that is not cached yet is downloaded during the reload when
RT_MODEL_DOWNLOAD allows it (the model store of the loaders).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from .model_registry import specs_for
from .model_store import ModelSettings
from .runtime_config import RuntimeConfig


def list_models(kind: str, current: str, settings: ModelSettings) -> dict:
    store = settings.store()
    models = [
        {
            "id": spec.alias,
            "cached": not store.verify(spec),
            "size": sum(f.size for f in spec.files),
        }
        for spec in specs_for(kind)
    ]
    return {"models": models, "current": current, "download": settings.download}


def build_models_router(kind: str, config: RuntimeConfig[Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/models")
    async def models() -> dict:
        return await run_in_threadpool(
            list_models, kind, config.current().model, ModelSettings.from_env()
        )

    return router
