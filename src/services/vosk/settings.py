"""rt-vosk settings from env.

``model`` is a comma list of Vosk model aliases, one per language: Vosk
models are single-language, and the language of each comes from the model
registry. CPU only (Kaldi), so ``device`` and ``compute_type`` are not
runtime fields.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar, Dict, Mapping, Optional

from ..common.env import SettingsError, env_int
from ..common.model_registry import specs_for
from ..common.settings import BaseSettings, FieldMeta, base_from_env

VOSK_BACKENDS = ("vosk", "fake")
DEFAULT_VOSK_MODELS = ("vosk-small-uk-v3-nano", "vosk-small-pl-0.22")


def vosk_languages() -> Dict[str, str]:
    """Registry alias -> language of every pinned Vosk model."""
    return {s.alias: s.language or "" for s in specs_for("vosk")}


def parse_models(key: str, raw: str) -> tuple[str, ...]:
    """Comma list of registry aliases, at most one per language."""
    known = vosk_languages()
    out: list[str] = []
    seen: Dict[str, str] = {}
    for part in raw.split(","):
        alias = part.strip()
        if not alias:
            continue
        if alias not in known:
            known_list = ", ".join(sorted(known))
            raise SettingsError(
                f"{key}: unknown Vosk model {alias!r}; known: {known_list}"
            )
        lang = known[alias]
        if lang in seen and seen[lang] != alias:
            raise SettingsError(
                f"{key}: {seen[lang]!r} and {alias!r} are both {lang!r} models;"
                " list one model per language"
            )
        if alias not in out:
            out.append(alias)
            seen[lang] = alias
    if not out:
        raise SettingsError(f"{key} must list at least one Vosk model")
    return tuple(out)


@dataclass(frozen=True)
class VoskSettings(BaseSettings):
    max_upload_mb: int = 100

    BACKEND_ENV: ClassVar[Optional[str]] = "RT_VOSK_BACKEND"
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        **BaseSettings.ENV_KEYS,
        "max_upload_mb": "RT_VOSK_MAX_UPLOAD_MB",
    }
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = BaseSettings.TUNABLE_FIELDS | {
        "max_upload_mb"
    }
    RELOAD_FIELDS: ClassVar[frozenset[str]] = frozenset({"backend", "model"})
    FIELD_META: ClassVar[Mapping[str, FieldMeta]] = {
        **BaseSettings.FIELD_META,
        "backend": FieldMeta("choice", "ASR backend", VOSK_BACKENDS),
        "model": FieldMeta(
            "text",
            "Vosk models, comma list, one per language: "
            + ", ".join(f"{a} ({lang})" for a, lang in vosk_languages().items()),
        ),
        "max_upload_mb": FieldMeta("int", "max upload, MB", min=1, max=1000),
    }

    @property
    def models(self) -> tuple[str, ...]:
        return tuple(self.model.split(","))

    @property
    def languages(self) -> Dict[str, str]:
        """Language -> model alias of the configured models."""
        known = vosk_languages()
        return {known[alias]: alias for alias in self.models}

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "VoskSettings":
        keys = cls.env_keys()
        base = base_from_env(
            env,
            settings_cls=cls,
            service="rt-vosk",
            backends=VOSK_BACKENDS,
            default_backend="vosk",
            default_model=",".join(DEFAULT_VOSK_MODELS),
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
        base["model"] = ",".join(parse_models(keys["model"], base["model"]))
        return cls(
            **base,
            max_upload_mb=env_int(
                env, keys["max_upload_mb"], 100, min_value=1, max_value=1000
            ),
        )
