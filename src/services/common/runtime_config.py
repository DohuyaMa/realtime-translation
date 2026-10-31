"""Runtime-changeable service config, persisted across restarts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Generic, List, Mapping, Optional, Sequence
from typing import Tuple, TypeVar

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .env import SettingsError
from .settings import BaseSettings, render_env_value

log = logging.getLogger(__name__)

S = TypeVar("S", bound=BaseSettings)

Snapshot = Tuple[Dict[str, object], int]


class RuntimeConfig(Generic[S]):
    def __init__(
        self,
        settings_cls: type[S],
        bootstrap_env: Mapping[str, str],
        state_path: Optional[Path] = None,
    ) -> None:
        self._cls = settings_cls
        self._bootstrap_env = dict(bootstrap_env)
        self._state_path = Path(state_path) if state_path is not None else None
        self._lock = threading.Lock()
        self._runtime_fields = settings_cls.TUNABLE_FIELDS | settings_cls.RELOAD_FIELDS
        self._bootstrap_fields = settings_cls.bootstrap_fields()
        self._bootstrap = self._build({})
        # Switchable devices are a property of the node, not of the current
        # device: pin them so a device override cannot widen them.
        self._bootstrap_env.setdefault(
            settings_cls.env_keys()["devices"],
            render_env_value(self._bootstrap.devices),
        )
        self._current: S = self._bootstrap
        self._overrides: Dict[str, object] = {}
        self._version = 0

    def _build(self, overrides: Mapping[str, object]) -> S:
        keys = self._cls.env_keys()
        env = dict(self._bootstrap_env)
        env.update({keys[f]: render_env_value(v) for f, v in overrides.items()})
        try:
            return self._cls.from_env(env)  # type: ignore[attr-defined]
        except SettingsError as exc:
            raise ValueError(str(exc)) from exc

    def _values(self, settings: S) -> Dict[str, object]:
        return {f: getattr(settings, f) for f in sorted(self._runtime_fields)}

    def current(self) -> S:
        with self._lock:
            return self._current

    def effective(self) -> dict:
        with self._lock:
            return {
                "values": self._values(self._current),
                "source": {
                    f: "override" if f in self._overrides else "bootstrap"
                    for f in sorted(self._runtime_fields)
                },
                "bootstrap": self._values(self._bootstrap),
                "version": self._version,
                "schema": self._cls.field_schema(self._bootstrap),
            }

    def apply(
        self,
        values: Mapping[str, object],
        version: int,
        reset: Sequence[str] = (),
    ) -> List[str]:
        """Apply overrides all-or-nothing; returns changed RELOAD field names."""
        with self._lock:
            keys = self._cls.env_keys()
            new = dict(self._overrides)
            for field in reset:
                new.pop(field, None)
            for field, value in values.items():
                if field in self._bootstrap_fields:
                    raise ValueError(f"{field} is not runtime-configurable")
                if field in self._runtime_fields and field in keys:
                    new[field] = value
            settings = self._build(new)
            self._persist(new, version)
            changed = [
                f
                for f in sorted(self._cls.RELOAD_FIELDS)
                if getattr(settings, f) != getattr(self._current, f)
            ]
            self._current, self._overrides, self._version = settings, new, version
            return changed

    def snapshot(self) -> Snapshot:
        with self._lock:
            return dict(self._overrides), self._version

    def rollback(self, snap: Snapshot) -> None:
        overrides, version = snap
        with self._lock:
            settings = self._build(overrides)
            self._persist(overrides, version)
            self._current = settings
            self._overrides, self._version = dict(overrides), version

    def _persist(self, overrides: Mapping[str, object], version: int) -> None:
        if self._state_path is None:
            return
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp.write_text(json.dumps({"version": version, "overrides": overrides}))
        os.replace(tmp, self._state_path)

    def restore(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            version, overrides = data["version"], data["overrides"]
            if not isinstance(version, int) or not isinstance(overrides, dict):
                raise ValueError("malformed state")
            bad = set(overrides) - self._runtime_fields
            if bad:
                raise ValueError(f"not runtime-configurable: {sorted(bad)}")
            settings = self._build(overrides)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("ignoring runtime config state %s: %s", self._state_path, exc)
            return
        with self._lock:
            self._current, self._overrides, self._version = (
                settings,
                dict(overrides),
                version,
            )


class ConfigUpdate(BaseModel):
    version: int
    values: Dict[str, Any]
    reset: List[str] = Field(default_factory=list)


def build_config_router(
    cfg: RuntimeConfig[Any],
    on_reload: Optional[Callable[[Any], None]] = None,
) -> APIRouter:
    """GET/POST /config. Without on_reload nothing is ever reported reloaded."""
    router = APIRouter()
    # Serializes snapshot + apply + reload + rollback of concurrent POSTs.
    update_lock = asyncio.Lock()

    @router.get("/config")
    async def get_config() -> dict:
        return cfg.effective()

    @router.post("/config")
    async def post_config(body: ConfigUpdate):
        async with update_lock:
            snap = cfg.snapshot()
            try:
                changed = cfg.apply(body.values, body.version, body.reset)
            except ValueError as exc:
                return JSONResponse({"detail": str(exc)}, status_code=422)
            reloaded = False
            if changed and on_reload is not None:
                try:
                    await run_in_threadpool(on_reload, cfg.current())
                except Exception as exc:  # noqa: BLE001 - report any reload failure
                    detail = str(exc)
                    try:
                        cfg.rollback(snap)
                    except Exception:  # noqa: BLE001
                        log.exception("runtime config rollback failed")
                    return JSONResponse({"detail": detail}, status_code=500)
                reloaded = True
            return cfg.effective() | {"reloaded": reloaded}

    return router
