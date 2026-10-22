"""Settings shared by all speech services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import (
    Any,
    ClassVar,
    Dict,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
)

from .env import env_choice, env_int, env_str

DEFAULT_PORTS = {"rt-asr": 8091, "rt-tts": 8093}
DEVICES = ("cpu", "cuda", "rocm")
LOG_LEVELS = ("trace", "debug", "info", "warning", "error", "critical")


def render_env_value(value: object) -> str:
    """Render a settings value the way it would be written in an env var."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (tuple, list)):
        return ",".join(str(v) for v in value)
    if value is None:
        return ""
    return str(value)


@dataclass(frozen=True)
class BaseSettings:
    service: str
    backend: str
    host: str
    port: int
    device: str
    model: str
    compute_type: str
    model_dir: Optional[str]
    api_token_file: Optional[str]
    log_level: str
    max_pending: int
    config_state: Optional[str] = None

    # field -> env key, except "backend": its key is service specific, so
    # subclasses set BACKEND_ENV (e.g. "RT_ASR_BACKEND"). Always read the full
    # mapping through env_keys().
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        "host": "RT_HOST",
        "port": "RT_PORT",
        "device": "RT_DEVICE",
        "model": "RT_MODEL",
        "compute_type": "RT_COMPUTE_TYPE",
        "model_dir": "RT_MODEL_DIR",
        "api_token_file": "RT_API_TOKEN_FILE",
        "log_level": "RT_LOG_LEVEL",
        "max_pending": "RT_MAX_PENDING",
        "config_state": "RT_CONFIG_STATE",
    }
    # Changeable at runtime without reloading the model.
    BACKEND_ENV: ClassVar[Optional[str]] = None
    TUNABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"log_level"})
    # Changeable at runtime, but the model has to be reloaded.
    RELOAD_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"backend", "model", "device", "compute_type"}
    )

    @classmethod
    def env_keys(cls) -> Dict[str, str]:
        """Field -> env key for every env-backed field (single accessor)."""
        keys = dict(cls.ENV_KEYS)
        if cls.BACKEND_ENV is not None:
            keys["backend"] = cls.BACKEND_ENV
        return keys

    @classmethod
    def bootstrap_fields(cls) -> frozenset[str]:
        """Env-backed fields that can only change via restart."""
        keys = frozenset(cls.env_keys())
        missing = (cls.TUNABLE_FIELDS | cls.RELOAD_FIELDS) - keys
        if missing:
            raise TypeError(
                f"{cls.__name__}: no env key for {sorted(missing)}"
                " (set BACKEND_ENV?)"
            )
        return keys - cls.TUNABLE_FIELDS - cls.RELOAD_FIELDS

    @property
    def is_gpu(self) -> bool:
        return self.device != "cpu"

    @property
    def torch_device(self) -> str:
        """Device name for torch / CTranslate2. ROCm builds also use "cuda"."""
        return "cuda" if self.is_gpu else "cpu"


def base_from_env(
    env: Mapping[str, str],
    *,
    service: str,
    backends: Sequence[str],
    default_backend: str,
    default_model: str,
    compute_cpu: str,
    compute_gpu: str,
    settings_cls: type[BaseSettings] = BaseSettings,
) -> Dict[str, Any]:
    """Pure function of ``env``; no os.environ access."""
    keys = settings_cls.env_keys()
    if "backend" not in keys:
        raise TypeError(f"{settings_cls.__name__}.BACKEND_ENV is not set")
    device = env_choice(env, keys["device"], "cpu", DEVICES)
    return dict(
        service=service,
        backend=env_choice(env, keys["backend"], default_backend, backends),
        host=env_str(env, keys["host"], "127.0.0.1"),
        port=env_int(
            env,
            keys["port"],
            DEFAULT_PORTS[service],
            min_value=1,
            max_value=65535,
        ),
        device=device,
        model=env_str(env, keys["model"], default_model),
        compute_type=env_str(
            env,
            keys["compute_type"],
            compute_cpu if device == "cpu" else compute_gpu,
        ),
        model_dir=env_str(env, keys["model_dir"], None),
        api_token_file=env_str(env, keys["api_token_file"], None),
        log_level=env_choice(env, keys["log_level"], "info", LOG_LEVELS),
        max_pending=env_int(env, keys["max_pending"], 16, min_value=1),
        config_state=env_str(env, keys["config_state"], None),
    )


def apply_model_dir(
    settings: BaseSettings,
    environ: Optional[MutableMapping[str, str]] = None,
) -> None:
    """Point HF_HOME at $RT_MODEL_DIR/hf unless HF_HOME is already set."""
    environ = os.environ if environ is None else environ
    if settings.model_dir and not environ.get("HF_HOME"):
        environ["HF_HOME"] = os.path.join(settings.model_dir, "hf")
