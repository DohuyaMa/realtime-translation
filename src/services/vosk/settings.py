"""rt-vosk settings from env.

``model`` is ONE Vosk model alias: a Vosk model is single-language (its
language comes from the model registry) and the service holds exactly one
model at a time; a request in another language is refused (409
``model_language_mismatch``), the model is switched explicitly via
``POST /config``. CPU only (Kaldi), so ``device`` and ``compute_type`` are
not runtime fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar, Dict, Mapping, Optional

from ..common.env import SettingsError, env_int
from ..common.model_registry import specs_for
from ..common.settings import BaseSettings, FieldMeta, base_from_env

VOSK_BACKENDS = ("vosk", "fake")
DEFAULT_VOSK_MODEL = "vosk-small-uk-v3-nano"


def vosk_languages() -> Dict[str, str]:
    """Registry alias -> language of every pinned Vosk model."""
    return {s.alias: s.language or "" for s in specs_for("vosk")}


def parse_model(key: str, raw: str) -> str:
    """One registry alias of a Vosk model."""
    alias = raw.strip()
    known = vosk_languages()
    if alias not in known:
        known_list = ", ".join(sorted(known))
        raise SettingsError(f"{key}: unknown Vosk model {alias!r}; known: {known_list}")
    return alias


@dataclass(frozen=True)
class VoskSettings(BaseSettings):
    max_upload_mb: int = 100
    max_sessions: int = 4

    BACKEND_ENV: ClassVar[Optional[str]] = "RT_VOSK_BACKEND"
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        **BaseSettings.ENV_KEYS,
        "max_upload_mb": "RT_VOSK_MAX_UPLOAD_MB",
        "max_sessions": "RT_VOSK_MAX_SESSIONS",
    }
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = BaseSettings.TUNABLE_FIELDS | {
        "max_upload_mb"
    }
    RELOAD_FIELDS: ClassVar[frozenset[str]] = frozenset({"backend", "model"})
    FIELD_META: ClassVar[Mapping[str, FieldMeta]] = {
        **BaseSettings.FIELD_META,
        "backend": FieldMeta("choice", "ASR backend", VOSK_BACKENDS),
        "model": FieldMeta(
            "model",
            "Vosk model (one language; see GET /v1/models)",
            tuple(vosk_languages()),
        ),
        "max_upload_mb": FieldMeta("int", "max upload, MB", min=1, max=1000),
    }

    @property
    def language(self) -> str:
        """Language of the configured model (from the registry)."""
        return vosk_languages()[self.model]

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "VoskSettings":
        keys = cls.env_keys()
        base = base_from_env(
            env,
            settings_cls=cls,
            service="rt-vosk",
            backends=VOSK_BACKENDS,
            default_backend="vosk",
            default_model=DEFAULT_VOSK_MODEL,
            compute_cpu="float32",  # Kaldi's fixed precision; not configurable
            compute_gpu="float32",
        )
        if base["devices"] != ("cpu",):
            raise SettingsError(
                f"{keys['device']}/{keys['devices']}: rt-vosk runs on cpu only"
            )
        if base["compute_type"] != "float32":
            raise SettingsError(
                f"{keys['compute_type']}: Kaldi computes in float32 only"
            )
        base["model"] = parse_model(keys["model"], base["model"])
        return cls(
            **base,
            max_upload_mb=env_int(
                env, keys["max_upload_mb"], 100, min_value=1, max_value=1000
            ),
            max_sessions=env_int(
                env, keys["max_sessions"], 4, min_value=1, max_value=64
            ),
        )
