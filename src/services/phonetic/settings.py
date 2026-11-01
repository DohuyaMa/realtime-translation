"""rt-phonetic settings from env.

``model`` is ONE character-level CTC model alias (wav2vec2, greedy decoding,
no dictionary or LM): it writes what it hears letter by letter in the
alphabet of one language (from the model registry); a request in another
language is refused (409 ``model_language_mismatch``), the model is
switched explicitly via ``POST /config``. CPU only, float32 (the model is
small enough: ~0.3 s per 2 s of speech on 4 threads, RT_NUM_THREADS).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar, Dict, Mapping, Optional

from ..common.env import SettingsError, env_int
from ..common.model_registry import specs_for
from ..common.settings import BaseSettings, FieldMeta, base_from_env

PHONETIC_BACKENDS = ("wav2vec2", "fake")
DEFAULT_PHONETIC_MODEL = "uk-wav2vec2-stress"


def phonetic_languages() -> Dict[str, str]:
    """Registry alias -> language of every pinned phonetic model."""
    return {s.alias: s.language or "" for s in specs_for("phonetic")}


def parse_model(key: str, raw: str) -> str:
    """One registry alias of a phonetic model."""
    alias = raw.strip()
    known = phonetic_languages()
    if alias not in known:
        known_list = ", ".join(sorted(known))
        raise SettingsError(
            f"{key}: unknown phonetic model {alias!r}; known: {known_list}"
        )
    return alias


@dataclass(frozen=True)
class PhoneticSettings(BaseSettings):
    max_upload_mb: int = 20

    BACKEND_ENV: ClassVar[Optional[str]] = "RT_PHONETIC_BACKEND"
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        **BaseSettings.ENV_KEYS,
        "max_upload_mb": "RT_PHONETIC_MAX_UPLOAD_MB",
    }
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = BaseSettings.TUNABLE_FIELDS | {
        "max_upload_mb"
    }
    RELOAD_FIELDS: ClassVar[frozenset[str]] = frozenset({"backend", "model"})
    FIELD_META: ClassVar[Mapping[str, FieldMeta]] = {
        **BaseSettings.FIELD_META,
        "backend": FieldMeta("choice", "phonetic backend", PHONETIC_BACKENDS),
        "model": FieldMeta(
            "model",
            "phonetic CTC model (one language; see GET /v1/models)",
            tuple(phonetic_languages()),
        ),
        "max_upload_mb": FieldMeta("int", "max upload, MB", min=1, max=200),
    }

    @property
    def language(self) -> str:
        """Language of the configured model (from the registry)."""
        return phonetic_languages()[self.model]

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "PhoneticSettings":
        keys = cls.env_keys()
        base = base_from_env(
            env,
            settings_cls=cls,
            service="rt-phonetic",
            backends=PHONETIC_BACKENDS,
            default_backend="wav2vec2",
            default_model=DEFAULT_PHONETIC_MODEL,
            compute_cpu="float32",
            compute_gpu="float32",
        )
        if base["devices"] != ("cpu",):
            raise SettingsError(
                f"{keys['device']}/{keys['devices']}: rt-phonetic runs on cpu only"
            )
        if base["compute_type"] != "float32":
            raise SettingsError(
                f"{keys['compute_type']}: rt-phonetic computes in float32 only"
            )
        base["model"] = parse_model(keys["model"], base["model"])
        return cls(
            **base,
            max_upload_mb=env_int(
                env, keys["max_upload_mb"], 20, min_value=1, max_value=200
            ),
        )
