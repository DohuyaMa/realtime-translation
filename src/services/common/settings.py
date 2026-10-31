"""Settings shared by all speech services."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import asdict, dataclass
from typing import (
    Any,
    ClassVar,
    Dict,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
)

from .env import SettingsError, env_choice, env_int, env_str

DEFAULT_PORTS = {"rt-asr": 8091, "rt-vosk": 8092, "rt-tts": 8093}
DEFAULT_LISTEN_ADDRESSES = ("127.0.0.1",)
DEVICES = ("cpu", "cuda", "rocm")
LOG_LEVELS = ("trace", "debug", "info", "warning", "error", "critical")


CT2_COMPUTE_TYPES = (
    "int8",
    "int8_float16",
    "int8_float32",
    "int8_bfloat16",
    "float16",
    "bfloat16",
    "float32",
)


@dataclass(frozen=True)
class FieldMeta:
    """Schema of one runtime field, published in ``GET /config``.

    type: bool | int | float | choice | multichoice | model | text.
    ``choices`` for choice/multichoice ("" = unset/auto when ``nullable``);
    ``model``: choices are the service's pinned models (see GET /v1/models).
    Ranges must match the env readers (checked by tests).
    """

    type: str
    description: str = ""
    choices: Optional[tuple[str, ...]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    nullable: bool = False

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


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
    listen_addresses: tuple[str, ...]
    port: int
    device: str
    model: str
    compute_type: str
    model_dir: Optional[str]
    api_token_file: Optional[str]
    log_level: str
    max_pending: int
    config_state: Optional[str] = None
    idle_unload_minutes: int = 15
    # Devices ``device`` may switch to at runtime (RT_DEVICES); () = (device,).
    devices: tuple[str, ...] = ()

    # field -> env key, except "backend": its key is service specific, so
    # subclasses set BACKEND_ENV (e.g. "RT_ASR_BACKEND"). Always read the full
    # mapping through env_keys().
    ENV_KEYS: ClassVar[Mapping[str, str]] = {
        "listen_addresses": "RT_LISTEN_ADDRESSES",
        "port": "RT_PORT",
        "device": "RT_DEVICE",
        "model": "RT_MODEL",
        "compute_type": "RT_COMPUTE_TYPE",
        "model_dir": "RT_MODEL_DIR",
        "api_token_file": "RT_API_TOKEN_FILE",
        "log_level": "RT_LOG_LEVEL",
        "max_pending": "RT_MAX_PENDING",
        "config_state": "RT_CONFIG_STATE",
        "idle_unload_minutes": "RT_IDLE_UNLOAD_MINUTES",
        "devices": "RT_DEVICES",
    }
    # Schema of the runtime (TUNABLE | RELOAD) fields; subclasses extend it.
    FIELD_META: ClassVar[Mapping[str, FieldMeta]] = {
        "log_level": FieldMeta("choice", "log level", LOG_LEVELS),
        "device": FieldMeta("choice", "compute device", DEVICES),
        "compute_type": FieldMeta("choice", "precision", CT2_COMPUTE_TYPES),
        "backend": FieldMeta("text", "backend"),
        "model": FieldMeta("text", "model alias or absolute directory"),
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

    @classmethod
    def field_schema(cls, settings: Optional["BaseSettings"] = None) -> Dict[str, dict]:
        """Runtime fields -> FieldMeta dict + ``reload`` flag (GET /config).

        With ``settings`` the ``device`` choices are that node's switchable
        devices (RT_DEVICES), not every device the code knows.
        """
        out = {}
        for name in sorted(cls.TUNABLE_FIELDS | cls.RELOAD_FIELDS):
            meta = cls.FIELD_META.get(name)
            if meta is None:
                raise TypeError(f"{cls.__name__}: no FIELD_META for {name!r}")
            out[name] = meta.as_dict() | {"reload": name in cls.RELOAD_FIELDS}
        if settings is not None and "device" in out:
            out["device"]["choices"] = settings.devices
        return out

    @property
    def is_gpu(self) -> bool:
        return self.device != "cpu"

    @property
    def torch_device(self) -> str:
        """Device name for torch / CTranslate2. ROCm builds also use "cuda"."""
        return "cuda" if self.is_gpu else "cpu"


def parse_listen_addresses(key: str, raw: Optional[str]) -> tuple[str, ...]:
    """Comma list of IP literals (one listening socket each); default loopback."""
    if raw is None:
        return DEFAULT_LISTEN_ADDRESSES
    out: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            addr = str(ipaddress.ip_address(part))
        except ValueError as exc:
            raise SettingsError(
                f"{key} entries must be IP addresses, got {part!r}"
            ) from exc
        if addr not in out:
            out.append(addr)
    if not out:
        raise SettingsError(f"{key} must list at least one address")
    return tuple(out)


def parse_devices(key: str, raw: Optional[str], device: str) -> tuple[str, ...]:
    """RT_DEVICES: comma list of switchable devices; must contain ``device``."""
    if raw is None or not raw.strip():
        return (device,)
    out: list[str] = []
    for part in raw.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in DEVICES:
            raise SettingsError(f"{key} entries must be one of {DEVICES}, got {part!r}")
        if part not in out:
            out.append(part)
    if device not in out:
        raise SettingsError(
            f"device {device!r} is not available on this node ({key}={','.join(out)})"
        )
    return tuple(out)


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
        listen_addresses=parse_listen_addresses(
            keys["listen_addresses"], env_str(env, keys["listen_addresses"], None)
        ),
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
        idle_unload_minutes=env_int(
            env, keys["idle_unload_minutes"], 15, min_value=0, max_value=10080
        ),
        devices=parse_devices(
            keys["devices"], env_str(env, keys["devices"], None), device
        ),
    )


def apply_model_dir(
    settings: BaseSettings,
    environ: Optional[MutableMapping[str, str]] = None,
) -> None:
    """Point HF_HOME at $RT_MODEL_DIR/hf unless HF_HOME is already set."""
    environ = os.environ if environ is None else environ
    if settings.model_dir and not environ.get("HF_HOME"):
        environ["HF_HOME"] = os.path.join(settings.model_dir, "hf")
