"""rt-asr settings from env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar, Mapping, Optional

from ..common.device import default_beams, default_model
from ..common.env import (
    SettingsError,
    env_bool,
    env_choice,
    env_float,
    env_int,
    env_str,
)
from ..common.model_registry import specs_for
from ..common.settings import DEVICES, BaseSettings, FieldMeta, base_from_env

ASR_BACKENDS = ("faster-whisper", "fake")

# Two-letter language codes Whisper understands (includes its legacy "jw").
WHISPER_LANGUAGES = frozenset(
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo "
    "fr gl gu ha he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt "
    "lv mg mi mk ml mn mr ms mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk "
    "sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk ur uz vi yi yo zh".split()
)
DEFAULT_LANGUAGES = ("pl", "uk", "ru", "en")


def normalize_language(value: Optional[str]) -> Optional[str]:
    """None / "" / "auto" mean autodetect; unknown codes raise SettingsError."""
    if value is None or value.strip() == "" or value.strip().lower() == "auto":
        return None
    code = value.strip().lower()
    if code not in WHISPER_LANGUAGES:
        raise SettingsError(f"unknown language code {value!r}")
    return code


def _parse_languages(key: str, raw: Optional[str]) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_LANGUAGES
    out: list[str] = []
    for part in raw.split(","):
        if not part.strip():
            continue
        code = normalize_language(part)
        if code is None:
            raise SettingsError(f"{key} entries must be language codes, got {part!r}")
        if code not in out:
            out.append(code)
    if not out:
        raise SettingsError(f"{key} must list at least one language")
    return tuple(out)


@dataclass(frozen=True)
class AsrSettings(BaseSettings):
    language: Optional[str] = None
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    beam_size: int = 1
    vad_filter: bool = True
    max_upload_mb: int = 100
    chunk_seconds: float = 600.0
    chunk_lookback_seconds: float = 30.0
    realtime_max_seconds: float = 30.0
    stream_tick_ms: int = 500
    max_sessions: int = 4

    BACKEND_ENV: ClassVar[Optional[str]] = "RT_ASR_BACKEND"
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        **BaseSettings.ENV_KEYS,
        "language": "RT_ASR_LANGUAGE",
        "languages": "RT_ASR_LANGUAGES",
        "beam_size": "RT_ASR_BEAM_SIZE",
        "vad_filter": "RT_ASR_VAD",
        "max_upload_mb": "RT_ASR_MAX_UPLOAD_MB",
        "chunk_seconds": "RT_ASR_CHUNK_SECONDS",
        "chunk_lookback_seconds": "RT_ASR_CHUNK_LOOKBACK_SECONDS",
        "realtime_max_seconds": "RT_ASR_REALTIME_MAX_SECONDS",
        "stream_tick_ms": "RT_ASR_STREAM_TICK_MS",
        "max_sessions": "RT_ASR_MAX_SESSIONS",
    }
    FIELD_META: ClassVar[Mapping[str, FieldMeta]] = {
        **BaseSettings.FIELD_META,
        "backend": FieldMeta("choice", "ASR backend", ASR_BACKENDS),
        "model": FieldMeta(
            "model",
            "Whisper model (downloaded on apply if not cached)",
            tuple(s.alias for s in specs_for("asr")),
        ),
        "language": FieldMeta(
            "choice",
            "default language; empty = auto-detect",
            ("", *sorted(WHISPER_LANGUAGES)),
            nullable=True,
        ),
        "languages": FieldMeta(
            "multichoice",
            "languages auto-detection may pick",
            tuple(sorted(WHISPER_LANGUAGES)),
        ),
        "beam_size": FieldMeta("int", "beam search width", min=1, max=10),
        "vad_filter": FieldMeta("bool", "skip silence (Silero VAD)"),
        "max_upload_mb": FieldMeta("int", "max upload, MB", min=1, max=1000),
        "chunk_seconds": FieldMeta(
            "float", "batch chunk length, s", min=30.0, max=900.0
        ),
        "chunk_lookback_seconds": FieldMeta(
            "float", "batch chunk overlap, s", min=5.0, max=120.0
        ),
        "realtime_max_seconds": FieldMeta(
            "float", "max realtime phrase, s", min=5.0, max=120.0
        ),
    }
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = BaseSettings.TUNABLE_FIELDS | {
        "language",
        "languages",
        "beam_size",
        "vad_filter",
        "max_upload_mb",
        "chunk_seconds",
        "chunk_lookback_seconds",
        "realtime_max_seconds",
    }

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "AsrSettings":
        keys = cls.env_keys()
        device = env_choice(env, keys["device"], "cpu", DEVICES)
        base = base_from_env(
            env,
            settings_cls=cls,
            service="rt-asr",
            backends=ASR_BACKENDS,
            default_backend="faster-whisper",
            default_model=default_model("asr", device),
            compute_cpu="int8",
            compute_gpu="int8_float16",
        )
        return cls(
            **base,
            language=normalize_language(env_str(env, keys["language"], None)),
            languages=_parse_languages(
                keys["languages"], env_str(env, keys["languages"], None)
            ),
            beam_size=env_int(
                env,
                keys["beam_size"],
                default_beams("asr", device),
                min_value=1,
                max_value=10,
            ),
            vad_filter=env_bool(env, keys["vad_filter"], True),
            max_upload_mb=env_int(
                env, keys["max_upload_mb"], 100, min_value=1, max_value=1000
            ),
            chunk_seconds=env_float(
                env, keys["chunk_seconds"], 600.0, min_value=30.0, max_value=900.0
            ),
            chunk_lookback_seconds=env_float(
                env,
                keys["chunk_lookback_seconds"],
                30.0,
                min_value=5.0,
                max_value=120.0,
            ),
            realtime_max_seconds=env_float(
                env,
                keys["realtime_max_seconds"],
                30.0,
                min_value=5.0,
                max_value=120.0,
            ),
            stream_tick_ms=env_int(
                env, keys["stream_tick_ms"], 500, min_value=100, max_value=5000
            ),
            max_sessions=env_int(
                env, keys["max_sessions"], 4, min_value=1, max_value=64
            ),
        )
