"""rt-tts settings from env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import ClassVar, Mapping, Optional

from ..common.device import default_model
from ..common.env import SettingsError, env_choice, env_float, env_int, env_str
from ..common.settings import DEVICES, BaseSettings, base_from_env
from .engines import DEFAULT_TTS_ENGINE
from .voices import KOKORO_VOICES

TTS_BACKENDS = ("kokoro", "fake")
TTS_ENGINES = ("onnx",)


@dataclass(frozen=True)
class TtsSettings(BaseSettings):
    engine: str = DEFAULT_TTS_ENGINE
    voice: str = "af_heart"
    speed: float = 1.0
    sample_rate: int = 24000
    max_chars: int = 4096

    BACKEND_ENV: ClassVar[Optional[str]] = "RT_TTS_BACKEND"
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        **BaseSettings.ENV_KEYS,
        "engine": "RT_TTS_ENGINE",
        "voice": "RT_TTS_VOICE",
        "speed": "RT_TTS_SPEED",
        "sample_rate": "RT_TTS_SAMPLE_RATE",
        "max_chars": "RT_TTS_MAX_CHARS",
    }
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = BaseSettings.TUNABLE_FIELDS | {
        "voice",
        "speed",
        "sample_rate",
        "max_chars",
    }
    RELOAD_FIELDS: ClassVar[frozenset[str]] = BaseSettings.RELOAD_FIELDS | {"engine"}

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "TtsSettings":
        keys = cls.env_keys()
        device = env_choice(env, keys["device"], "cpu", DEVICES)
        if device == "rocm":
            raise SettingsError(f"{keys['device']}=rocm is not supported by rt-tts")
        engine = env_choice(env, keys["engine"], DEFAULT_TTS_ENGINE, TTS_ENGINES)
        base = base_from_env(
            env,
            settings_cls=cls,
            service="rt-tts",
            backends=TTS_BACKENDS,
            default_backend="kokoro",
            default_model=default_model("tts", device),
            compute_cpu="float32",
            compute_gpu="float32",
        )
        voice = env_str(env, keys["voice"], "af_heart")
        if voice not in KOKORO_VOICES:
            raise SettingsError(f"{keys['voice']}: unknown voice {voice!r}")
        return cls(
            **base,
            engine=engine,
            voice=voice,
            speed=env_float(env, keys["speed"], 1.0, min_value=0.25, max_value=4.0),
            sample_rate=env_int(
                env, keys["sample_rate"], 24000, min_value=8000, max_value=48000
            ),
            max_chars=env_int(env, keys["max_chars"], 4096, min_value=1),
        )
