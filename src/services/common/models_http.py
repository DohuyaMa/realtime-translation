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

# Above this size a model is too slow for realtime use on a CPU node.
CPU_HEAVY_BYTES = 1_000_000_000


def list_models(
    kind: str, current: str, settings: ModelSettings, device: str = "cpu"
) -> dict:
    store = settings.store()
    models = []
    for spec in specs_for(kind):
        size = sum(f.size for f in spec.files)
        entry = {"id": spec.alias, "cached": not store.verify(spec), "size": size}
        if spec.language:
            entry["language"] = spec.language
        if device == "cpu" and size > CPU_HEAVY_BYTES:
            entry["warning"] = "large model on a CPU node: slow, not realtime"
        models.append(entry)
    return {"models": models, "current": current, "download": settings.download}


def build_models_router(kind: str, config: RuntimeConfig[Any]) -> APIRouter:
    router = APIRouter()

    @router.get("/models")
    async def models() -> dict:
        s = config.current()
        return await run_in_threadpool(
            list_models, kind, s.model, ModelSettings.from_env(), s.device
        )

    return router
